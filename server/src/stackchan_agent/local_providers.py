import asyncio
import io
import re
import tempfile
import unicodedata
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np

from .providers import STTProvider, TTSProvider, TurnContext


def detect_language(text: str) -> str:
    japanese = sum(
        1 for char in text if "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
    )
    return "ja" if japanese >= max(1, len(text) // 8) else "en"


def is_nonspeech_caption(text: str) -> bool:
    """Recognize Whisper-style bracketed sound captions, not ordinary speech."""
    value = text.strip()
    return len(value) >= 2 and (
        (value[0], value[-1]) in {("(", ")"), ("[", "]"), ("<", ">")}
        or (value.startswith("*") and value.endswith("*"))
    )


def visual_only_glyph(character: str) -> bool:
    """Identify emoji-style symbols while preserving spoken technical notation."""
    codepoint = ord(character)
    return (
        0x2600 <= codepoint <= 0x27BF
        or 0x1F000 <= codepoint <= 0x1FAFF
        or 0xFE00 <= codepoint <= 0xFE0F
    )


def sanitize_for_tts(text: str) -> str:
    """Remove emoji and control glyphs unsupported by local speech tokenizers."""
    output = []
    for character in text:
        category = unicodedata.category(character)
        if category[0] == "C" or visual_only_glyph(character):
            continue
        output.append(character)
    return " ".join("".join(output).split())


def normalize_tts_prosody(text: str, language: str) -> str:
    """Convert decorative pauses into punctuation local TTS pronounces reliably."""
    normalized = sanitize_for_tts(text)
    pause = "、" if language == "ja" else ","
    # Supertonic can swallow a word in long enumerations separated by Unicode
    # ellipses even though every PCM frame is delivered. Normal commas retain
    # the intended pause and produce a stable token boundary.
    return re.sub(r"(?:\.{3,}|…+)", pause, normalized)


def is_joke_request(text: str) -> bool:
    """Allow a setup question to continue; this never supplies canned content."""
    normalized = text.casefold()
    return any(
        cue in normalized
        for cue in ("joke", "make me laugh", "ジョーク", "冗談", "笑わせ")
    )


def response_sentence_budget(context: TurnContext) -> int:
    """Choose spoken depth from intent while keeping commands immediately interruptible."""
    normalized = context.transcript.casefold()
    if is_joke_request(context.transcript):
        return 1
    if any(
        cue in normalized
        for cue in ("one sentence", "briefly", "short answer", "in short", "一言", "短く")
    ):
        return 1
    if any(
        cue in normalized
        for cue in (
            "detail",
            "explain",
            "why",
            "how",
            "compare",
            "詳しく",
            "説明して",
            "なぜ",
            "どうやって",
            "比較して",
        )
    ):
        return 4
    if context.action_results or context.memories:
        return 2
    if any(
        cue in normalized
        for cue in (
            "count ",
            "say ",
            "repeat ",
            "translate ",
            "list ",
            "数えて",
            "言って",
            "繰り返",
            "翻訳して",
            "列挙して",
        )
    ):
        return 1
    return 3


def response_matches_language(text: str, language: str) -> bool:
    """Reject clear language drift while allowing language-neutral numeric replies."""
    visible = "".join(character for character in text if character.isalnum())
    if not visible:
        return False
    if all(character.isdigit() for character in visible):
        return True
    return detect_language(text) == language


def response_is_complete(text: str, language: str, *, joke: bool) -> bool:
    if not response_matches_language(text, language):
        return False
    return not (joke and text.rstrip().endswith(("?", "？")))


def bounded_response_piece(
    emitted: str,
    piece: str,
    language: str,
    *,
    allow_question_setup: bool = False,
    max_sentences: int = 2,
) -> tuple[str, bool]:
    """Bound a streamed response to a small, substantive spoken answer."""
    # The sentence budget is the normal response boundary. This larger guard is
    # only a runaway-output safety net; a tight character cut makes speech end
    # mid-word before the model can finish its final sentence.
    limit = 240 if language == "ja" else 600
    remaining = max(0, limit - len(emitted))
    candidate = piece[:remaining]
    terminal_characters = "。！？" if language == "ja" else ".!?"
    question = "？" if language == "ja" else "?"
    skipped_question = False
    terminal_indexes: list[int] = []
    combined = emitted + candidate
    for absolute_index, character in enumerate(combined):
        if character not in terminal_characters:
            continue
        # Treat repeated punctuation such as ``...`` and ``..`` as a spoken
        # pause. Counting replies commonly stream as ``One.. Two..``; treating
        # both dots as independent sentences truncated the answer to ``One..``.
        previous_is_terminal = (
            absolute_index > 0
            and combined[absolute_index - 1] in terminal_characters
        )
        next_is_terminal = (
            absolute_index + 1 < len(combined)
            and combined[absolute_index + 1] in terminal_characters
        )
        if previous_is_terminal or next_is_terminal:
            continue
        if character == question and allow_question_setup and not skipped_question:
            skipped_question = True
            continue
        # Streaming models often count as `1. 2. 3.` (and occasionally `1。`
        # in Japanese). A small numeric list marker is not a sentence ending.
        if character in {".", "。"} and absolute_index > 0:
            digit_start = absolute_index
            while digit_start > 0 and combined[digit_start - 1].isdigit():
                digit_start -= 1
            token = combined[digit_start:absolute_index]
            boundary = (
                digit_start == 0
                or combined[digit_start - 1].isspace()
                or combined[digit_start - 1] in ".。,，、;；"
            )
            if token and boundary and int(token) <= 100:
                continue
        terminal_indexes.append(absolute_index)
    if len(terminal_indexes) >= max(1, max_sentences):
        boundary = terminal_indexes[max(1, max_sentences) - 1]
        candidate = candidate[: boundary - len(emitted) + 1]
        return candidate, True
    return candidate, len(emitted) + len(candidate) >= limit


class WhisperCppSTT(STTProvider):
    """Local whisper.cpp baseline.

    The first implementation finalizes a turn as one request. A persistent
    streaming backend can replace it through the same interface after the model
    benchmark determines chunk and context settings.
    """

    def __init__(self, executable: Path, model: Path) -> None:
        self.executable = executable
        self.model = model

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> tuple[str, str]:
        if not self.executable.exists():
            raise RuntimeError(f"whisper.cpp executable not found: {self.executable}")
        if not self.model.exists():
            raise RuntimeError(f"whisper.cpp model not found: {self.model}")
        with tempfile.TemporaryDirectory(prefix="stackchan-stt-") as directory:
            root = Path(directory)
            input_path = root / "turn.wav"
            output_stem = root / "transcript"
            with wave.open(str(input_path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(sample_rate)
                handle.writeframes(pcm16)
            process = await asyncio.create_subprocess_exec(
                str(self.executable),
                "-m",
                str(self.model),
                "-f",
                str(input_path),
                "-l",
                "auto",
                "-otxt",
                "-of",
                str(output_stem),
                "-nt",
                "-np",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await process.wait() != 0:
                raise RuntimeError("whisper.cpp transcription failed")
            transcript = output_stem.with_suffix(".txt").read_text(encoding="utf-8").strip()
            return transcript, detect_language(transcript)


class WhisperServerSTT(STTProvider):
    """Turn transcription through a resident whisper.cpp HTTP server."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 30.0,
        *,
        prompt: str = "",
        language: str = "auto",
        confidence: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.prompt = prompt
        self.language = language
        self.confidence = confidence

    @staticmethod
    def _wav(pcm16: bytes, sample_rate: int) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm16)
        return output.getvalue()

    async def transcribe_detailed(self, pcm16: bytes, sample_rate: int) -> "WhisperTranscription":
        if not pcm16:
            return WhisperTranscription("", "en", None)
        files = {"file": ("turn.wav", self._wav(pcm16, sample_rate), "audio/wav")}
        data = {
            "response_format": "verbose_json" if self.confidence else "json",
            "language": self.language,
        }
        if self.prompt:
            data["prompt"] = self.prompt
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(f"{self.base_url}/inference", files=files, data=data)
            response.raise_for_status()
        payload = response.json()
        transcript = str(payload.get("text", "")).strip()
        segment_probabilities = [
            float(segment["avg_logprob"])
            for segment in payload.get("segments", ())
            if "avg_logprob" in segment
        ]
        average_log_probability = (
            sum(segment_probabilities) / len(segment_probabilities)
            if segment_probabilities
            else None
        )
        return WhisperTranscription(
            transcript, detect_language(transcript), average_log_probability
        )

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> tuple[str, str]:
        result = await self.transcribe_detailed(pcm16, sample_rate)
        return result.text, result.language


@dataclass(frozen=True, slots=True)
class WhisperTranscription:
    text: str
    language: str
    average_log_probability: float | None


class BilingualWhisperSTT(STTProvider):
    """Keep English fast; confidence-route Japanese across small and large."""

    def __init__(
        self,
        fast: STTProvider,
        japanese: STTProvider,
        japanese_fast: WhisperServerSTT | None = None,
        *,
        japanese_barge_fast: WhisperServerSTT | None = None,
        japanese_barge_cue_robust: WhisperServerSTT | None = None,
        japanese_barge_robust: WhisperServerSTT | None = None,
        english_barge_fast: WhisperServerSTT | None = None,
        english_barge_robust: WhisperServerSTT | None = None,
        confidence_threshold: float = -0.18,
    ) -> None:
        self.fast = fast
        self.japanese = japanese
        self.japanese_fast = japanese_fast
        self.japanese_barge_fast = japanese_barge_fast or japanese_fast
        self.japanese_barge_cue_robust = japanese_barge_cue_robust
        self.japanese_barge_robust = japanese_barge_robust
        self.english_barge_fast = english_barge_fast
        self.english_barge_robust = english_barge_robust
        self.confidence_threshold = confidence_threshold
        self.last_route: dict[str, str | float | bool | None] = {}
        self.last_language = "en"
        self._robust_next_language: str | None = None

    def prefer_robust_next_turn(self, language: str) -> None:
        """Route the noisy turn following a physical interruption to a larger ASR."""
        self._robust_next_language = language if language in {"en", "ja"} else None

    async def _transcribe_english_barge(
        self, pcm16: bytes, sample_rate: int
    ) -> tuple[str, str] | None:
        if self.english_barge_fast is None:
            return None
        small = await self.english_barge_fast.transcribe_detailed(pcm16, sample_rate)
        small_confidence = small.average_log_probability
        if (
            small_confidence is not None
            and small_confidence >= self.confidence_threshold
        ) or self.english_barge_robust is None:
            self.last_route = {
                "route": "english_barge_small",
                "fallback": False,
                "small_avg_logprob": small_confidence,
            }
            self.last_language = "en"
            return small.text, "en"
        large = await self.english_barge_robust.transcribe_detailed(pcm16, sample_rate)
        large_confidence = large.average_log_probability
        choose_large = large_confidence is not None and (
            small_confidence is None or large_confidence > small_confidence
        )
        chosen = large if choose_large else small
        self.last_route = {
            "route": "english_barge_large" if choose_large else "english_barge_small_retained",
            "fallback": True,
            "small_avg_logprob": small_confidence,
            "large_avg_logprob": large_confidence,
        }
        self.last_language = "en"
        return chosen.text, "en"

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> tuple[str, str]:
        robust_language = self._robust_next_language
        self._robust_next_language = None
        if robust_language == "en":
            robust_result = await self._transcribe_english_barge(pcm16, sample_rate)
            if robust_result is not None:
                return robust_result
        if robust_language == "ja" and self.japanese_barge_robust is not None:
            robust = await self.japanese_barge_robust.transcribe_detailed(
                pcm16, sample_rate
            )
            self.last_route = {
                "route": "japanese_barge_large",
                "fallback": False,
                "large_avg_logprob": robust.average_log_probability,
            }
            self.last_language = "ja"
            return robust.text, "ja"
        fast_result = await self.fast.transcribe(pcm16, sample_rate)
        caption_recovery = self.last_language == "ja" and is_nonspeech_caption(fast_result[0])
        force_japanese = robust_language == "ja"
        if fast_result[1] != "ja" and not caption_recovery and not force_japanese:
            self.last_route = {"route": "base", "fallback": False}
            self.last_language = fast_result[1]
            return fast_result
        if self.japanese_fast is not None:
            small = await self.japanese_fast.transcribe_detailed(pcm16, sample_rate)
            small_confidence = small.average_log_probability
            if small_confidence is not None and small_confidence >= self.confidence_threshold:
                self.last_route = {
                    "route": "small",
                    "fallback": False,
                    "small_avg_logprob": small_confidence,
                    "caption_recovery": caption_recovery,
                }
                self.last_language = "ja"
                return small.text, "ja"
            if not isinstance(self.japanese, WhisperServerSTT):
                raise TypeError("confidence routing requires WhisperServerSTT providers")
            large = await self.japanese.transcribe_detailed(pcm16, sample_rate)
            large_confidence = large.average_log_probability
            choose_large = large_confidence is not None and (
                small_confidence is None or large_confidence > small_confidence
            )
            chosen = large if choose_large else small
            chosen_confidence = large_confidence if choose_large else small_confidence
            if caption_recovery and (
                chosen_confidence is None or chosen_confidence < self.confidence_threshold
            ):
                self.last_route = {
                    "route": "base_caption_rejected",
                    "fallback": True,
                    "small_avg_logprob": small_confidence,
                    "large_avg_logprob": large_confidence,
                    "caption_recovery": True,
                }
                return fast_result
            self.last_route = {
                "route": "small_large" if choose_large else "small_retained",
                "fallback": True,
                "small_avg_logprob": small_confidence,
                "large_avg_logprob": large_confidence,
                "caption_recovery": caption_recovery,
            }
            self.last_language = "ja"
            return chosen.text, "ja"
        japanese_result = await self.japanese.transcribe(pcm16, sample_rate)
        self.last_route = {"route": "large", "fallback": False}
        self.last_language = "ja"
        return japanese_result[0], "ja"


class MacOSTTS(TTSProvider):
    """Fast offline baseline using macOS system voices and streamed PCM output."""

    def __init__(self, voice_en: str = "Samantha", voice_ja: str = "Kyoko") -> None:
        self.voice_en = voice_en
        self.voice_ja = voice_ja

    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        voice = self.voice_ja if language == "ja" else self.voice_en
        with tempfile.TemporaryDirectory(prefix="stackchan-tts-") as directory:
            audio_path = Path(directory) / "speech.aiff"
            say = await asyncio.create_subprocess_exec(
                "say", "-v", voice, "-o", str(audio_path), text
            )
            if await say.wait() != 0:
                raise RuntimeError("macOS speech synthesis failed")
            ffmpeg = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(audio_path),
                "-f",
                "s16le",
                "-ac",
                "1",
                "-ar",
                str(self.sample_rate),
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
            )
            assert ffmpeg.stdout is not None
            frame_bytes = self.sample_rate // 50 * 2
            while chunk := await ffmpeg.stdout.read(frame_bytes):
                if len(chunk) % 2:
                    chunk += b"\0"
                yield chunk
            if await ffmpeg.wait() != 0:
                raise RuntimeError("ffmpeg PCM conversion failed")


class SupertonicTTS(TTSProvider):
    """Resident local Supertonic service with in-process PCM resampling."""

    def __init__(
        self,
        base_url: str,
        *,
        voice: str = "F1",
        steps: int = 5,
        speed: float = 1.08,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.voice = voice
        self.steps = steps
        self.speed = speed
        self._cache: dict[tuple[str, str], tuple[bytes, ...]] = {}

    @staticmethod
    def _decode_and_resample(wav_bytes: bytes, output_rate: int) -> bytes:
        with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
            if handle.getsampwidth() != 2:
                raise RuntimeError("Supertonic returned non-PCM16 WAV audio")
            channels = handle.getnchannels()
            input_rate = handle.getframerate()
            samples = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
        if input_rate == output_rate:
            return samples.astype("<i2", copy=False).tobytes()
        output_count = round(len(samples) * output_rate / input_rate)
        source_positions = np.arange(len(samples), dtype=np.float64)
        output_positions = np.arange(output_count, dtype=np.float64) * input_rate / output_rate
        resampled = np.interp(output_positions, source_positions, samples).astype("<i2")
        return resampled.tobytes()

    @classmethod
    def _trim_interjection(cls, frames: tuple[bytes, ...]) -> tuple[bytes, ...]:
        """Remove model padding while preserving 60 ms around a short utterance."""
        if not frames:
            return frames
        pcm = b"".join(frames)
        samples = np.frombuffer(pcm, dtype="<i2")
        frame_samples = cls.sample_rate // 50
        rms = np.array(
            [
                np.sqrt(np.mean(samples[offset : offset + frame_samples].astype(float) ** 2))
                for offset in range(0, len(samples), frame_samples)
            ]
        )
        threshold = max(120.0, float(rms.max(initial=0)) * 0.03)
        voiced = np.flatnonzero(rms >= threshold)
        if not len(voiced):
            return frames
        start = max(0, int(voiced[0]) - 3)
        end = min(len(frames), int(voiced[-1]) + 4)
        return frames[start:end]

    async def _render(self, text: str, language: str) -> tuple[bytes, ...]:
        text = normalize_tts_prosody(text, language)
        if not text:
            return ()
        request = {
            "text": text,
            "voice": self.voice,
            "lang": language if language in {"en", "ja"} else "na",
            "steps": self.steps,
            "speed": self.speed,
            "response_format": "wav",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{self.base_url}/v1/tts", json=request)
            response.raise_for_status()
        pcm = self._decode_and_resample(response.content, self.sample_rate)
        frame_bytes = self.sample_rate // 50 * 2
        return tuple(
            pcm[offset : offset + frame_bytes] for offset in range(0, len(pcm), frame_bytes)
        )

    async def preload(self, text: str, language: str, *, trim_interjection: bool = False) -> None:
        key = (language, normalize_tts_prosody(text, language))
        if key[1] and key not in self._cache:
            frames = await self._render(key[1], language)
            self._cache[key] = self._trim_interjection(frames) if trim_interjection else frames

    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        normalized = normalize_tts_prosody(text, language)
        key = (language, normalized)
        frames = self._cache.get(key)
        if frames is None:
            frames = await self._render(normalized, language)
        for frame in frames:
            yield frame
