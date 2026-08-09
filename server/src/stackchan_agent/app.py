import asyncio
import hashlib
import hmac
import re
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from difflib import SequenceMatcher
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .codex_sessions import recent_codex_titles
from .config import PROJECT_ROOT, Settings
from .echo import EchoCanceller
from .eve_provider import EveLLM
from .local_providers import (
    BilingualWhisperSTT,
    SupertonicTTS,
    WhisperServerSTT,
    WhisperTranscription,
)
from .memory import MemoryStore, SensitiveMemoryError
from .music import music_duration_seconds, signature_jingle
from .pipeline import CascadePipeline, meaningful_transcript, take_speakable_phrase
from .processes import SupertonicServerProcess, WhisperServerProcess
from .protocol import (
    AudioFlags,
    AudioFrame,
    AudioStream,
    ControlMessage,
    ImageFormat,
    ImageFrame,
    control,
)
from .providers import MockLLM, MockSTT, MockTTS, TurnContext
from .realtime import OpenAIRealtimePipeline
from .schedules import ROUTINES, Schedule, ScheduleStore
from .telemetry import TraceRecorder
from .vad import (
    ConsecutiveSpeechDetector,
    EnergyTurnDetector,
    WebRtcSpeechGate,
    pcm16_rms,
)
from .vision import AppleVisionAnalyzer


class RememberMemoryRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    language: str = Field(default="und", pattern="^(en|ja|und)$")
    kind: str = Field(default="fact", min_length=1, max_length=40)
    importance: float = Field(default=0.7, ge=0.0, le=1.0)


class EveSessionBindingRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)


class CreateScheduleRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=500)
    language: str = Field(pattern="^(en|ja)$")
    routine: str
    music: bool = False
    capture_photo: bool = False
    recurrence: str = Field(pattern="^(once|daily)$")
    timezone: str = Field(min_length=1, max_length=80)
    local_time: str = Field(min_length=5, max_length=16)
    quiet_start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class ScheduleEnabledRequest(BaseModel):
    enabled: bool


def require_loopback(request: Request) -> None:
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="endpoint is loopback-only")


async def send_locked_text(websocket: WebSocket, lock: asyncio.Lock, message: str) -> None:
    """Serialize text with audio writes on a single device WebSocket."""
    async with lock:
        await websocket.send_text(message)


def memory_payload(item) -> dict:
    return {
        "id": item.id,
        "content": item.content,
        "language": item.language,
        "kind": item.kind,
        "importance": item.importance,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "expires_at": item.expires_at,
        "memory_key": item.memory_key,
    }


def schedule_payload(item: Schedule) -> dict:
    return {
        "id": item.id,
        "device_id": item.device_id,
        "label": item.label,
        "prompt": item.prompt,
        "language": item.language,
        "routine": item.routine,
        "music": item.music,
        "capture_photo": item.capture_photo,
        "recurrence": item.recurrence,
        "timezone": item.timezone,
        "local_time": item.local_time,
        "quiet_start": item.quiet_start,
        "quiet_end": item.quiet_end,
        "next_fire_at": item.next_fire_at,
        "enabled": item.enabled,
        "last_status": item.last_status,
        "last_fired_at": item.last_fired_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def rebase_pacing_after_gap(
    origin: float, target: float, now: float, *, maximum_lag_seconds: float
) -> tuple[float, float]:
    """Start a new paced segment instead of bursting frames after inference stalls."""
    lag = now - target
    if lag <= maximum_lag_seconds:
        return origin, target
    return origin + lag, now


def should_accept_head_gesture(
    *, now: float, last_accepted_at: float, cooldown_seconds: float
) -> bool:
    """Coalesce the multiple gesture labels produced by one physical pat."""
    return last_accepted_at <= 0 or now - last_accepted_at >= cooldown_seconds


def motion_capture_is_guarded(
    active_request_ids: set[str], *, now: float, guarded_until: float
) -> bool:
    """Suppress servo acoustics for the measured motion, not only its requested duration."""
    return bool(active_request_ids) or now < guarded_until


def motion_capture_allows_speech_start(
    active_request_ids: set[str],
    *,
    guarded: bool,
    clean_rms: float,
    minimum_rms: float,
    voice_frame: bool,
) -> bool:
    """Require voiced high-energy evidence after motion; never listen during motion."""
    if active_request_ids:
        return False
    return not guarded or (clean_rms >= minimum_rms and voice_frame)


def ordinary_capture_allows_speech_start(
    *, turn_active: bool, motion_allows_start: bool
) -> bool:
    """Do not let a background VAD edge replace a turn while its reply is pending."""
    return not turn_active and motion_allows_start


def physical_playback_is_drained(
    *, device_playback_active: bool, now: float, playback_until: float
) -> bool:
    """Completion requires both the firmware state and paced server tail to be idle."""
    return not device_playback_active and now >= playback_until


def merge_audio_without_overlap(prefix: bytes, audio: bytes, *, frame_bytes: int = 640) -> bytes:
    """Join PCM windows while removing frame-aligned detector pre-roll duplication."""
    if not prefix or not audio:
        return prefix + audio
    maximum = min(len(prefix), len(audio))
    maximum -= maximum % frame_bytes
    for overlap in range(maximum, 0, -frame_bytes):
        if prefix[-overlap:] == audio[:overlap]:
            return prefix + audio[overlap:]
    return prefix + audio


def retain_semantic_window_and_suffix(window: bytes, accumulated: bytes) -> bytes:
    """Drop audio before the bounded window while retaining its captured tail."""
    if not window:
        return accumulated
    offset = accumulated.rfind(window)
    if offset < 0:
        return window
    return accumulated[offset:]


def retain_recent_pcm16(audio: bytes, sample_rate: int, duration_ms: int) -> bytes:
    """Retain a bounded mono PCM16 tail without splitting a sample."""
    maximum_bytes = max(0, sample_rate * 2 * duration_ms // 1000)
    maximum_bytes -= maximum_bytes % 2
    if maximum_bytes == 0:
        return b""
    return audio[-maximum_bytes:]


def is_meaningful_barge_transcript(transcript: str) -> bool:
    """Accept natural near-end speech; acoustic/AEC gates reject self-playback."""
    normalized = transcript.casefold().strip(" .,!?:;。！？、…ー〜~")
    fillers = {
        "hm",
        "hmm",
        "ah",
        "oh",
        "uh",
        "um",
        "ん",
        "んん",
        "あ",
        "え",
        "うん",
        "えー",
        "あー",
    }
    return meaningful_transcript(transcript) and normalized not in fillers


def is_substantial_natural_barge(transcript: str, language: str) -> bool:
    """Reject short decoder fragments when no explicit interruption cue exists."""
    normalized = "".join(character for character in transcript.strip() if character.isalnum())
    return len(normalized) >= (6 if language == "ja" else 8)


def pairing_proof(
    secret: str, role: str, server_nonce: str, device_nonce: str, device_id: str
) -> str:
    """Bind each side's proof to both fresh nonces, its role, and the device."""
    if (
        not secret
        or role not in {"device", "server"}
        or not server_nonce
        or not device_nonce
        or not device_id
    ):
        return ""
    message = f"stackchan-v1:{role}:{server_nonce}:{device_nonce}:{device_id}"
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def pairing_response_matches(
    secret: str,
    server_nonce: str,
    device_nonce: str,
    device_id: str,
    supplied: str,
) -> bool:
    """Authenticate a fresh device proof without transmitting the static secret."""
    if not supplied:
        return False
    expected = pairing_proof(secret, "device", server_nonce, device_nonce, device_id)
    if not expected:
        return False
    return secrets.compare_digest(expected, supplied)


def tool_result_is_terminal(payload: dict) -> bool:
    """Failures are terminal even if old firmware mislabeled them dispatched."""
    return payload.get("success") is False or str(payload.get("stage", "")) in {
        "completed",
        "rejected",
        "read_only",
        "failed",
    }


def looks_like_render_echo(transcript: str, rendered_text: str) -> bool:
    """Compare semantic probe text with what Stack-chan is currently saying."""
    number_words = {
        "zero": "0",
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "nine": "9",
        "ten": "10",
    }

    def normalize(value: str) -> str:
        lowered = value.casefold()
        for word, digit in number_words.items():
            lowered = re.sub(rf"\b{word}\b", digit, lowered)
        # Whisper freely alternates equivalent hiragana/katakana spellings
        # (なぜか / なぜカ...). Fold basic katakana so that orthography alone
        # cannot make Stack-chan accept its own render as an interruption.
        folded = "".join(
            chr(ord(character) - 0x60) if "ァ" <= character <= "ヶ" else character
            for character in lowered
        )
        return "".join(character for character in folded if character.isalnum())

    candidate = normalize(transcript)
    rendered = normalize(rendered_text)
    if not candidate or not rendered:
        return False
    if candidate.isdigit() and rendered.isdigit():
        return set(candidate) <= set(rendered)
    if len(candidate) == 1:
        return candidate in rendered
    similarity = SequenceMatcher(None, candidate, rendered)
    # Whisper often contracts a phrase from the speaker (for example,
    # "win an award" -> "winner"). Comparing against the whole response then
    # dilutes an otherwise near-exact local match. Longest-match coverage keeps
    # this robust while still requiring nearly all of the candidate utterance
    # to appear in Stack-chan's current render text.
    local_coverage = similarity.find_longest_match().size / len(candidate)
    partial_similarity = 0.0
    if len(candidate) >= 6:
        shortest = max(1, len(candidate) - 2)
        longest = min(len(rendered), len(candidate) + 2)
        partial_similarity = max(
            (
                SequenceMatcher(None, candidate, rendered[start : start + width]).ratio()
                for width in range(shortest, longest + 1)
                for start in range(len(rendered) - width + 1)
            ),
            default=0.0,
        )
    return (
        candidate in rendered
        or similarity.ratio() >= 0.72
        or (len(candidate) >= 6 and local_coverage >= 0.72)
        or partial_similarity >= 0.65
    )


def select_barge_probe(
    probes: list[tuple[str, WhisperTranscription]],
    preferred_language: str,
    *,
    confidence_threshold: float = -0.40,
    render_text: str = "",
) -> tuple[str, WhisperTranscription]:
    """Prefer a valid active-language decode without hiding a language switch."""

    def confidence(item: tuple[str, WhisperTranscription]) -> float:
        value = item[1].average_log_probability
        return value if value is not None else -999.0

    eligible = [
        item
        for item in probes
        if is_meaningful_barge_transcript(item[1].text)
        and confidence(item) >= confidence_threshold
        and not looks_like_render_echo(item[1].text, render_text)
    ]
    candidates = eligible or probes
    cued = [item for item in eligible if has_explicit_barge_cue(item[1].text, item[0])]
    if cued:
        preferred_cued = next((item for item in cued if item[0] == preferred_language), None)
        if preferred_cued is not None:
            return preferred_cued
        return max(cued, key=confidence)
    best = max(
        candidates,
        key=lambda item: (is_meaningful_barge_transcript(item[1].text), confidence(item)),
    )
    preferred = next(
        (item for item in eligible if item[0] == preferred_language),
        None,
    )
    if preferred is not None:
        return preferred
    return best


async def decode_barge_probes(
    stt: BilingualWhisperSTT,
    audio: bytes,
    sample_rate: int,
    preferred_language: str,
    rendered_text: str,
    *,
    always_decode_both: bool = False,
) -> list[tuple[str, WhisperTranscription]]:
    """Decode the active language first and avoid a redundant echo decode.

    Both forced-language barge providers share one local Whisper process. Running
    them concurrently for every loudspeaker residual can starve real-time audio.
    A preferred-language result that matches the current render cannot authorize
    a cross-language interruption anyway, so return it immediately. A genuine
    same-language Stop/Wait cue is also complete evidence by itself. Only decode
    the alternate language when the first result is neither case.
    """
    providers = {
        "en": stt.english_barge_fast,
        "ja": stt.japanese_barge_fast,
    }
    ordered_languages = [
        preferred_language,
        "ja" if preferred_language == "en" else "en",
    ]
    probes: list[tuple[str, WhisperTranscription]] = []
    for index, language in enumerate(ordered_languages):
        provider = providers.get(language)
        if provider is None:
            continue
        probe = await provider.transcribe_detailed(audio, sample_rate)
        probes.append((language, probe))
        # The small Japanese model can confidently mistake a real ストップ for
        # counting during double-talk. Fall back to the resident large model
        # before discarding the one physical cue; captured boot-34 audio proves
        # that this recovers the cue while the small model misses it.
        if (
            language == "ja"
            and not looks_like_render_echo(probe.text, rendered_text)
            and stt.japanese_barge_cue_robust is not None
        ):
            robust_probe = await stt.japanese_barge_cue_robust.transcribe_detailed(
                audio, sample_rate
            )
            probes.append((language, robust_probe))
            if not always_decode_both and (
                has_explicit_barge_cue(probe.text, language)
                or has_explicit_barge_cue(robust_probe.text, language)
            ):
                break
        if (
            not always_decode_both
            and index == 0
            and (
                looks_like_render_echo(probe.text, rendered_text)
                or has_explicit_barge_cue(probe.text, language)
            )
        ):
            break
    return probes


async def decode_raw_barge_continuation(
    stt: BilingualWhisperSTT,
    audio: bytes,
    sample_rate: int,
    preferred_language: str,
) -> list[tuple[str, WhisperTranscription]]:
    """Use the robust lane only after a cheap decoder has validated a cue."""
    provider = (
        stt.english_barge_robust
        if preferred_language == "en"
        else stt.japanese_barge_robust or stt.japanese
    )
    if provider is None or not hasattr(provider, "transcribe_detailed"):
        return []
    probe = await provider.transcribe_detailed(audio, sample_rate)
    return [(preferred_language, probe)]


def select_preliminary_barge_cue(
    probes: list[tuple[str, WhisperTranscription]],
    rendered_text: str,
    *,
    confidence_threshold: float,
) -> tuple[str, WhisperTranscription] | None:
    """Select a non-render Stop/Wait cue that may only open a listening window."""
    candidates = [
        item
        for item in probes
        if is_meaningful_barge_transcript(item[1].text)
        and has_explicit_barge_cue(item[1].text, item[0])
        and not looks_like_render_echo(item[1].text, rendered_text)
        and (
            item[1].average_log_probability is None
            or item[1].average_log_probability >= confidence_threshold
        )
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            item[1].average_log_probability if item[1].average_log_probability is not None else 0.0
        ),
    )


def has_explicit_barge_cue(transcript: str, language: str) -> bool:
    """Recognize deliberate playback-control words, not the replacement request."""
    normalized = transcript.casefold()
    if language == "en":
        # A command cue occurs at utterance onset. This rejects narration and
        # negation such as "you're not going to stop me" as well as ordinary
        # response words such as "waiting" and "unstoppable".
        return (
            re.search(
                r"^\s*(?:hey\s+)?(?:stack\s*[- ]?\s*chan[,:]?\s*)?"
                r"(?:please\s+)?(?:stop|wait|hold\s+on)\b",
                normalized,
            )
            is not None
        )
    return (
        re.search(
            r"^\s*(?:(?:ねえ|あの|ちょっと)\s*)?"
            r"(?:(?:スタック(?:ちゃん|チャン)?)[、,\s]*)?"
            r"(?:ちょっと\s*)?(?:ストップ|止めて|待って)",
            normalized,
        )
        is not None
    )


def barge_turn_language(
    preferred_language: str, decoded_language: str, *, explicit_cue: bool
) -> str:
    """Avoid forcing a whole turn from a cross-language control-word decode."""
    if explicit_cue and decoded_language != preferred_language:
        return "auto"
    return decoded_language


def has_replacement_request_cue(transcript: str, language: str) -> bool:
    """Recognize an actionable request following a one-time Stop/Wait onset."""
    normalized = transcript.casefold()
    if language == "en":
        return (
            re.search(
                r"\b(?:tell|show|explain|say|play|move|turn|give|help)\s+(?:me|us)\b"
                r"|\b(?:can|could|would|will)\s+you\b"
                r"|\bi\s+need\s+[a-z0-9]",
                normalized,
            )
            is not None
        )
    return any(
        cue in normalized
        for cue in (
            "言って",
            "教えて",
            "見せて",
            "説明して",
            "動かして",
            "代わり",
            "ジョーク",
        )
    )


def is_stable_barge_language_switch(
    language: str,
    transcript: str,
    history: list[tuple[str, str]],
) -> bool:
    """Demand the cross-language cue twice so one decoder hallucination cannot switch."""
    normalized = transcript.casefold().strip()
    current_cued = has_explicit_barge_cue(transcript, language)
    return any(
        previous_language == language
        and (
            SequenceMatcher(None, previous_text, normalized).ratio() >= 0.55
            or (current_cued and has_explicit_barge_cue(previous_text, previous_language))
        )
        for previous_language, previous_text in history[-2:]
    )


def has_barge_intent_evidence(
    language: str,
    transcript: str,
    preferred_language: str,
    history: list[tuple[str, str]],
) -> bool:
    """Require a repeated playback-control cue before interrupting Stack-chan."""
    del preferred_language
    cued = has_explicit_barge_cue(transcript, language)
    stable = is_stable_barge_language_switch(language, transcript, history)
    return cued and stable


def has_prior_explicit_barge_cue(
    history: list[tuple[str, str]], language: str | None = None
) -> bool:
    """Remember a validated Stop/Wait onset while its replacement request arrives."""
    return any(
        (language is None or previous_language == language)
        and has_explicit_barge_cue(previous_text, previous_language)
        for previous_language, previous_text in history[-4:]
    )


def is_actionable_barge_continuation(
    transcript: str,
    language: str,
    confidence: float | None,
    rendered_text: str,
    history: list[tuple[str, str]],
    *,
    confidence_threshold: float,
    allow_cross_language_anchor: bool = False,
) -> bool:
    """Accept a request fragment only when a validated control cue anchors it."""
    anchored = has_prior_explicit_barge_cue(
        history, None if allow_cross_language_anchor else language
    )
    return bool(
        is_meaningful_barge_transcript(transcript)
        and anchored
        and has_replacement_request_cue(transcript, language)
        and not looks_like_render_echo(transcript, rendered_text)
        and (confidence is None or confidence >= confidence_threshold)
    )


def barge_confidence_is_sufficient(
    confidence: float | None,
    *,
    anchored_continuation: bool,
    general_threshold: float,
    continuation_threshold: float,
) -> bool:
    """Apply the continuation margin without a contradictory final veto."""
    if confidence is None:
        return True
    threshold = continuation_threshold if anchored_continuation else general_threshold
    return confidence >= threshold


def barge_probe_interval_ms(language: str, *, english_ms: int, japanese_ms: int) -> int:
    """Keep a possible Japanese cross-language cue intact in the first probe."""
    del language
    return max(english_ms, japanese_ms)


def should_open_acoustic_listening_window(
    *,
    enabled: bool,
    raw_rms: float,
    clean_rms: float,
    render_correlation: float,
    minimum_raw_rms: float,
    maximum_render_correlation: float,
    minimum_clean_ratio: float,
    maximum_clean_ratio: float,
) -> bool:
    """Permit attenuation, never a flush, for strong separated double-talk."""
    clean_ratio = clean_rms / raw_rms if raw_rms else 0.0
    return bool(
        enabled
        and raw_rms >= minimum_raw_rms
        and render_correlation <= maximum_render_correlation
        and minimum_clean_ratio <= clean_ratio <= maximum_clean_ratio
    )


def cross_language_barge_has_acoustic_support(
    language_switch: bool,
    raw_rms: float,
    minimum_raw_rms: float,
    *,
    preferred_decoder_is_render_echo: bool = False,
) -> bool:
    """Reject low-energy cross-decoder cue hallucinations from speaker residuals."""
    return not language_switch or (
        not preferred_decoder_is_render_echo and raw_rms >= minimum_raw_rms
    )


def cross_language_control_cue_can_confirm(
    language_switch: bool,
    switch_cued: bool,
    *,
    playback_ducked: bool,
    probe_stable: bool,
) -> bool:
    """Allow an alternate decoder's cue only after the bounded retry window.

    Forced-language Whisper lanes can recover an English ``Stop`` as Japanese
    ``待って`` (and vice versa) during double-talk. A single such decode is not
    trustworthy enough to stop playback. After the first cue has opened the
    one-shot ducked listening window, however, a second stable cue can proceed
    to the separate raw-lane corroboration gate; rejecting it unconditionally
    discards the captured replacement request before ordinary bilingual STT can
    decode it.
    """
    if not (language_switch and switch_cued):
        return True
    return playback_ducked and probe_stable


def has_raw_control_cue_support(
    probes: list[tuple[str, WhisperTranscription]],
    rendered_text: str,
    *,
    confidence_threshold: float,
) -> bool:
    """Require cue-only confirmation to survive the unsuppressed raw lane.

    AEC can repeatedly turn Stack-chan's own speech into a plausible Stop/Wait
    phrase. Repetition alone therefore cannot authorize a flush. The raw lane
    must independently contain a non-render control cue, unless an actionable
    replacement request already provides stronger evidence.
    """
    return any(
        is_meaningful_barge_transcript(probe.text)
        and has_explicit_barge_cue(probe.text, language)
        and not looks_like_render_echo(probe.text, rendered_text)
        and (
            probe.average_log_probability is None
            or probe.average_log_probability >= confidence_threshold
        )
        for language, probe in probes
    )


def semantic_cue_can_open_listening_window(
    *, clean_cue_supported: bool, raw_control_cue_supported: bool
) -> bool:
    """Keep a clean-lane cue hallucination from attenuating robot speech."""
    return clean_cue_supported and raw_control_cue_supported


def preliminary_cue_has_independent_support(
    *, raw_control_cue_supported: bool, cross_language_acoustic_supported: bool
) -> bool:
    """Accept either raw semantics or the conservative cross-language level gate."""
    return raw_control_cue_supported or cross_language_acoustic_supported


def independent_same_language_cue_confirmed(
    probes: list[tuple[str, WhisperTranscription]],
    *,
    preferred_language: str,
    rendered_text: str,
    render_correlation: float,
    maximum_render_correlation: float,
    confidence_threshold: float,
) -> bool:
    """Require two Japanese model decoders plus clean acoustic separation."""
    if render_correlation > maximum_render_correlation:
        return False
    confirmations = sum(
        1
        for language, candidate in probes
        if language == preferred_language
        and has_explicit_barge_cue(candidate.text, language)
        and not looks_like_render_echo(candidate.text, rendered_text)
        and (
            candidate.average_log_probability is None
            or candidate.average_log_probability >= confidence_threshold
        )
    )
    return confirmations >= 2


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    realtime_api_key = (
        settings.openai_api_key.get_secret_value().strip()
        if settings.openai_api_key is not None
        else ""
    )
    if settings.provider == "speech_to_speech" and not realtime_api_key:
        raise ValueError(
            "STACKCHAN_PROVIDER=speech_to_speech requires OPENAI_API_KEY "
            "or STACKCHAN_OPENAI_API_KEY"
        )
    memory = MemoryStore(
        settings.memory_path,
        automatic_profiles=settings.memory_automatic_profiles,
        episodic_memory=settings.memory_episodic_enabled,
        episode_retention_days=settings.memory_episode_retention_days,
        episode_limit=settings.memory_episode_limit,
    )
    schedules = ScheduleStore(settings.schedule_path)
    whisper_process = WhisperServerProcess(
        settings.whisper_server,
        settings.whisper_model,
        settings.whisper_server_host,
        settings.whisper_server_port,
        settings.trace_dir.parent / "logs/whisper-server.log",
        settings.whisper_threads,
    )
    whisper_ja_process = WhisperServerProcess(
        settings.whisper_server,
        settings.whisper_ja_model,
        settings.whisper_server_host,
        settings.whisper_ja_server_port,
        settings.trace_dir.parent / "logs/whisper-ja-server.log",
        settings.whisper_threads,
    )
    whisper_ja_fast_process = WhisperServerProcess(
        settings.whisper_server,
        settings.whisper_ja_fast_model,
        settings.whisper_server_host,
        settings.whisper_ja_fast_server_port,
        settings.trace_dir.parent / "logs/whisper-ja-fast-server.log",
        settings.whisper_threads,
    )
    supertonic_process = SupertonicServerProcess(
        settings.supertonic_cli,
        settings.supertonic_host,
        settings.supertonic_port,
        settings.trace_dir.parent / "logs/supertonic.log",
    )
    cascade_tts = SupertonicTTS(
        f"http://{settings.supertonic_host}:{settings.supertonic_port}",
        voice=settings.supertonic_voice,
        steps=settings.supertonic_steps,
        speed=settings.supertonic_speed,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        schedule_dispatch_task: asyncio.Task[None] | None = None
        try:
            if settings.provider == "cascade":
                await asyncio.gather(
                    whisper_process.start(),
                    whisper_ja_fast_process.start(),
                    whisper_ja_process.start(),
                )
                await supertonic_process.start()
                eve_warmup = EveLLM(
                    settings.eve_url,
                    core_url=f"http://127.0.0.1:{settings.port}",
                    timeout_seconds=settings.eve_timeout_seconds,
                    approval_timeout_seconds=settings.eve_approval_timeout_seconds,
                )
                await eve_warmup.warmup()
            elif settings.provider == "speech_to_speech":
                # Physical sensor interactions retain durable Eve intelligence
                # and laptop-local voice independently of realtime transport.
                await supertonic_process.start()
                eve_warmup = EveLLM(
                    settings.eve_url,
                    core_url=f"http://127.0.0.1:{settings.port}",
                    timeout_seconds=settings.eve_timeout_seconds,
                    approval_timeout_seconds=settings.eve_approval_timeout_seconds,
                )
                await eve_warmup.warmup()
            schedule_dispatch_task = asyncio.create_task(dispatch_due_schedules())
            yield
        finally:
            if schedule_dispatch_task is not None:
                schedule_dispatch_task.cancel()
                await asyncio.gather(schedule_dispatch_task, return_exceptions=True)
            await asyncio.gather(
                whisper_process.stop(),
                whisper_ja_fast_process.stop(),
                whisper_ja_process.stop(),
            )
            await supertonic_process.stop()
            schedules.close()
            memory.close()

    app = FastAPI(title="Stack-chan Local", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.memory = memory
    app.state.schedules = schedules
    active_devices: dict[str, WebSocket] = {}
    device_send_locks: dict[str, asyncio.Lock] = {}
    proactive_queues: dict[str, asyncio.Queue[Schedule]] = {}
    device_info: dict[str, dict] = {}
    device_results: dict[str, deque[dict]] = {}
    device_captures: dict[str, dict] = {}
    eve_session_devices: dict[str, str] = {}
    bound_control_waiters: dict[tuple[str, str], asyncio.Future[dict]] = {}
    motion_capture_guard_until: dict[str, float] = {}
    captures_dir = settings.trace_dir.parent / "captures"
    captures_dir.mkdir(parents=True, exist_ok=True)
    vision_analyzer = AppleVisionAnalyzer(
        PROJECT_ROOT / "scripts/apple_vision.swift",
        settings.trace_dir.parent / "bin/stackchan-vision",
    )

    async def dispatch_due_schedules() -> None:
        while True:
            for scheduled_device_id, queue in list(proactive_queues.items()):
                if queue.qsize() >= 1:
                    continue
                item = schedules.claim_due(scheduled_device_id)
                if item is not None:
                    await queue.put(item)
            await asyncio.sleep(settings.schedule_poll_seconds)

    def results_for(device_id: str) -> deque[dict]:
        return device_results.setdefault(device_id, deque(maxlen=200))

    def extend_motion_capture_guard(device_id: str, message: ControlMessage) -> None:
        if message.type == "motion.set":
            duration_ms = max(0, min(int(message.payload.get("duration_ms", 450)), 2500))
        elif message.type == "routine.play":
            duration_ms = {
                "greet": 1600,
                "celebrate": 1600,
                "curious": 2100,
                "comfort": 2300,
                "dance": 2500,
                "wake_up": 2300,
                "focus": 1900,
                "good_night": 2100,
            }.get(str(message.payload.get("name", "greet")), 2500)
        else:
            return
        until = (
            asyncio.get_running_loop().time()
            + (duration_ms + settings.motion_capture_tail_ms) / 1_000
        )
        motion_capture_guard_until[device_id] = max(
            motion_capture_guard_until.get(device_id, 0.0), until
        )

    def providers():
        if settings.provider == "cascade":
            conversation_llm = EveLLM(
                settings.eve_url,
                core_url=f"http://127.0.0.1:{settings.port}",
                timeout_seconds=settings.eve_timeout_seconds,
                approval_timeout_seconds=settings.eve_approval_timeout_seconds,
            )
            return (
                BilingualWhisperSTT(
                    WhisperServerSTT(
                        f"http://{settings.whisper_server_host}:"
                        f"{settings.whisper_ja_fast_server_port}",
                        prompt=settings.whisper_prompt,
                    ),
                    WhisperServerSTT(
                        f"http://{settings.whisper_server_host}:{settings.whisper_ja_server_port}",
                        prompt=settings.whisper_ja_prompt,
                        language="ja",
                        confidence=True,
                    ),
                    WhisperServerSTT(
                        f"http://{settings.whisper_server_host}:"
                        f"{settings.whisper_ja_fast_server_port}",
                        prompt=settings.whisper_ja_prompt,
                        language="ja",
                        confidence=True,
                    ),
                    japanese_barge_fast=WhisperServerSTT(
                        f"http://{settings.whisper_server_host}:"
                        f"{settings.whisper_ja_fast_server_port}",
                        prompt=settings.whisper_barge_ja_prompt,
                        language="ja",
                        confidence=True,
                    ),
                    japanese_barge_cue_robust=WhisperServerSTT(
                        f"http://{settings.whisper_server_host}:{settings.whisper_ja_server_port}",
                        prompt=settings.whisper_barge_ja_prompt,
                        language="ja",
                        confidence=True,
                    ),
                    japanese_barge_robust=WhisperServerSTT(
                        f"http://{settings.whisper_server_host}:{settings.whisper_ja_server_port}",
                        prompt=settings.whisper_barge_continuation_ja_prompt,
                        language="ja",
                        confidence=True,
                    ),
                    english_barge_fast=WhisperServerSTT(
                        f"http://{settings.whisper_server_host}:"
                        f"{settings.whisper_ja_fast_server_port}",
                        prompt=settings.whisper_barge_en_prompt,
                        language="en",
                        confidence=True,
                    ),
                    english_barge_robust=WhisperServerSTT(
                        f"http://{settings.whisper_server_host}:{settings.whisper_ja_server_port}",
                        prompt=settings.whisper_barge_continuation_en_prompt,
                        language="en",
                        confidence=True,
                    ),
                    confidence_threshold=settings.whisper_ja_confidence_threshold,
                ),
                conversation_llm,
                cascade_tts,
            )
        if settings.provider == "speech_to_speech":
            return (
                MockSTT(),
                MockLLM(),
                SupertonicTTS(
                    f"http://{settings.supertonic_host}:{settings.supertonic_port}",
                    voice=settings.supertonic_voice,
                    steps=settings.supertonic_steps,
                    speed=settings.supertonic_speed,
                ),
            )
        return MockSTT(), MockLLM(), MockTTS()

    @app.get("/health")
    async def health() -> JSONResponse:
        dependencies: dict[str, bool] = {"device": bool(active_devices)}
        models: dict[str, str] = {"intelligence_backend": settings.intelligence_backend}
        if settings.provider == "speech_to_speech":
            models.update(
                conversation=settings.openai_realtime_model,
                sensor_intelligence_provider="eve",
                sensor_intelligence_configured=settings.eve_model,
                realtime=settings.openai_realtime_model,
                transcription=settings.openai_realtime_transcription_model,
            )
        else:
            models.update(
                conversation_provider="eve",
                conversation_configured=settings.eve_model,
                whisper_english=settings.whisper_ja_fast_model.name,
                whisper_japanese=settings.whisper_ja_model.name,
                whisper_barge=settings.whisper_ja_fast_model.name,
                tts_voice=settings.supertonic_voice,
            )
        if settings.provider in {"cascade", "speech_to_speech"}:
            async with httpx.AsyncClient(timeout=1.0) as client:
                try:
                    eve_response = await client.get(f"{settings.eve_url}/eve/v1/health")
                    dependencies["eve"] = eve_response.status_code == 200
                    if dependencies["eve"]:
                        info_response = await client.get(f"{settings.eve_url}/eve/v1/info")
                        info_response.raise_for_status()
                        runtime_model = str(
                            info_response.json()
                            .get("agent", {})
                            .get("model", {})
                            .get("id", "unknown")
                        )
                        model_key = (
                            "sensor_intelligence_runtime"
                            if settings.provider == "speech_to_speech"
                            else "conversation_runtime"
                        )
                        models[model_key] = runtime_model
                except (httpx.HTTPError, TypeError, ValueError):
                    dependencies["eve"] = False
                try:
                    supertonic_response = await client.get(
                        f"http://{settings.supertonic_host}:{settings.supertonic_port}/v1/health"
                    )
                    dependencies["supertonic"] = supertonic_response.status_code == 200
                except httpx.HTTPError:
                    dependencies["supertonic"] = False
                if settings.provider == "cascade":
                    whisper_ports = (
                        settings.whisper_server_port,
                        settings.whisper_ja_server_port,
                        settings.whisper_ja_fast_server_port,
                    )
                    whisper_checks = []
                    for port in whisper_ports:
                        try:
                            response = await client.get(
                                f"http://{settings.whisper_server_host}:{port}/health"
                            )
                            whisper_checks.append(
                                response.status_code == 200
                                and response.json().get("status") == "ok"
                            )
                        except (httpx.HTTPError, ValueError):
                            whisper_checks.append(False)
                    dependencies["whisper"] = all(whisper_checks)
        ready = all(dependencies.values())
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "ok" if ready else "degraded",
                "provider": settings.provider,
                "models": models,
                "dependencies": dependencies,
            },
        )

    @app.get("/v1/memories")
    async def recall_memories(
        request: Request, query: str = "", limit: int = 6
    ) -> dict[str, list[dict]]:
        require_loopback(request)
        bounded_limit = max(1, min(limit, 50))
        items = (
            memory.retrieve(query, limit=bounded_limit)
            if query.strip()
            else memory.list_recent(limit=bounded_limit)
        )
        return {"memories": [memory_payload(item) for item in items]}

    @app.post("/v1/memories")
    async def remember_memory(body: RememberMemoryRequest, request: Request) -> dict[str, object]:
        require_loopback(request)
        try:
            item, created = memory.remember_once(
                body.content,
                language=body.language,
                kind=body.kind,
                importance=body.importance,
            )
        except SensitiveMemoryError as error:
            raise HTTPException(
                status_code=422,
                detail=f"sensitive {error.category} information cannot be stored",
            ) from error
        return {"memory": memory_payload(item), "created": created}

    @app.delete("/v1/memories/{memory_id}")
    async def forget_memory(memory_id: int, request: Request) -> dict[str, object]:
        require_loopback(request)
        return {"memory_id": memory_id, "deleted": memory.forget(memory_id)}

    @app.get("/v1/devices")
    async def list_devices(request: Request) -> dict[str, list[str]]:
        require_loopback(request)
        return {"devices": sorted(active_devices)}

    @app.get("/v1/devices/{device_id}")
    async def get_device(device_id: str, request: Request) -> dict:
        require_loopback(request)
        if device_id not in active_devices:
            raise HTTPException(status_code=404, detail="device is not connected")
        return {"device_id": device_id, **device_info.get(device_id, {})}

    @app.get("/v1/devices/{device_id}/results")
    async def list_device_results(device_id: str, request: Request) -> dict[str, list[dict]]:
        require_loopback(request)
        return {"results": list(device_results.get(device_id, ()))}

    @app.get("/v1/devices/{device_id}/captures/latest")
    async def latest_device_capture(device_id: str, request: Request) -> FileResponse:
        require_loopback(request)
        capture = device_captures.get(device_id)
        if capture is None:
            raise HTTPException(status_code=404, detail="no camera capture is available")
        return FileResponse(
            capture["path"],
            media_type="image/jpeg",
            filename=Path(capture["path"]).name,
        )

    @app.get("/v1/devices/{device_id}/captures")
    async def get_device_capture_metadata(device_id: str, request: Request) -> dict:
        require_loopback(request)
        capture = device_captures.get(device_id)
        if capture is None:
            raise HTTPException(status_code=404, detail="no camera capture is available")
        return {"capture": {key: value for key, value in capture.items() if key != "path"}}

    @app.post("/v1/eve-sessions/{session_id}")
    async def bind_eve_session(
        session_id: str, body: EveSessionBindingRequest, request: Request
    ) -> dict[str, str]:
        require_loopback(request)
        if body.device_id not in active_devices:
            raise HTTPException(status_code=404, detail="device is not connected")
        eve_session_devices[session_id] = body.device_id
        return {"status": "bound", "session_id": session_id, "device_id": body.device_id}

    @app.delete("/v1/eve-sessions/{session_id}")
    async def unbind_eve_session(session_id: str, request: Request) -> dict[str, object]:
        require_loopback(request)
        deleted = eve_session_devices.pop(session_id, None) is not None
        return {"session_id": session_id, "deleted": deleted}

    def bound_device_id(session_id: str) -> str:
        device_id = eve_session_devices.get(session_id)
        if device_id is None:
            raise HTTPException(status_code=404, detail="Eve session is not bound to a device")
        if device_id not in active_devices:
            eve_session_devices.pop(session_id, None)
            raise HTTPException(status_code=404, detail="bound device is not connected")
        return device_id

    @app.get("/v1/eve-sessions/{session_id}/device")
    async def get_bound_device(session_id: str, request: Request) -> dict:
        require_loopback(request)
        device_id = bound_device_id(session_id)
        return {"device_id": device_id, **device_info.get(device_id, {})}

    def create_schedule_for_device(
        device_id: str, body: CreateScheduleRequest
    ) -> Schedule:
        if body.routine not in ROUTINES:
            raise HTTPException(status_code=422, detail="routine is not allowlisted")
        try:
            return schedules.create(
                device_id=device_id,
                label=body.label,
                prompt=body.prompt,
                language=body.language,
                routine=body.routine,
                music=body.music,
                capture_photo=body.capture_photo,
                recurrence=body.recurrence,
                timezone_name=body.timezone,
                local_time=body.local_time,
                quiet_start=body.quiet_start,
                quiet_end=body.quiet_end,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/v1/eve-sessions/{session_id}/device/schedules")
    async def list_bound_schedules(session_id: str, request: Request) -> dict:
        require_loopback(request)
        device_id = bound_device_id(session_id)
        return {
            "schedules": [
                schedule_payload(item)
                for item in schedules.list(device_id, include_disabled=True)
            ]
        }

    @app.post("/v1/eve-sessions/{session_id}/device/schedules")
    async def create_bound_schedule(
        session_id: str, body: CreateScheduleRequest, request: Request
    ) -> dict:
        require_loopback(request)
        return {
            "schedule": schedule_payload(
                create_schedule_for_device(bound_device_id(session_id), body)
            )
        }

    @app.patch("/v1/eve-sessions/{session_id}/device/schedules/{schedule_id}")
    async def set_bound_schedule_enabled(
        session_id: str,
        schedule_id: int,
        body: ScheduleEnabledRequest,
        request: Request,
    ) -> dict:
        require_loopback(request)
        try:
            item = schedules.set_enabled(
                schedule_id, bound_device_id(session_id), body.enabled
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="schedule not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"schedule": schedule_payload(item)}

    @app.delete("/v1/eve-sessions/{session_id}/device/schedules/{schedule_id}")
    async def delete_bound_schedule(
        session_id: str, schedule_id: int, request: Request
    ) -> dict:
        require_loopback(request)
        deleted = schedules.delete(schedule_id, bound_device_id(session_id))
        return {"schedule_id": schedule_id, "deleted": deleted}

    @app.get("/v1/devices/{device_id}/schedules")
    async def list_device_schedules(device_id: str, request: Request) -> dict:
        require_loopback(request)
        return {
            "schedules": [
                schedule_payload(item)
                for item in schedules.list(device_id, include_disabled=True)
            ]
        }

    @app.post("/v1/devices/{device_id}/schedules")
    async def create_device_schedule(
        device_id: str, body: CreateScheduleRequest, request: Request
    ) -> dict:
        require_loopback(request)
        return {"schedule": schedule_payload(create_schedule_for_device(device_id, body))}

    @app.patch("/v1/devices/{device_id}/schedules/{schedule_id}")
    async def set_device_schedule_enabled(
        device_id: str,
        schedule_id: int,
        body: ScheduleEnabledRequest,
        request: Request,
    ) -> dict:
        require_loopback(request)
        try:
            item = schedules.set_enabled(schedule_id, device_id, body.enabled)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="schedule not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"schedule": schedule_payload(item)}

    @app.delete("/v1/devices/{device_id}/schedules/{schedule_id}")
    async def delete_device_schedule(
        device_id: str, schedule_id: int, request: Request
    ) -> dict:
        require_loopback(request)
        return {
            "schedule_id": schedule_id,
            "deleted": schedules.delete(schedule_id, device_id),
        }

    @app.post("/v1/eve-sessions/{session_id}/device/control")
    async def send_bound_device_control(
        session_id: str, message: ControlMessage, request: Request
    ) -> dict:
        require_loopback(request)
        device_id = bound_device_id(session_id)
        request_id = message.request_id or secrets.token_hex(16)
        message.request_id = request_id
        key = (device_id, request_id)
        if key in bound_control_waiters:
            raise HTTPException(status_code=409, detail="control request id is already pending")
        result_future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        bound_control_waiters[key] = result_future
        try:
            dispatched = await send_device_control(device_id, message, request)
            try:
                terminal = await asyncio.wait_for(result_future, timeout=15.0)
            except TimeoutError:
                terminal = {
                    "success": False,
                    "stage": "timeout",
                    "detail": "no correlated terminal firmware result",
                }
            return {
                **dispatched,
                "request_id": request_id,
                "terminal_result": terminal,
            }
        finally:
            bound_control_waiters.pop(key, None)

    @app.post("/v1/devices/{device_id}/control")
    async def send_device_control(
        device_id: str, message: ControlMessage, request: Request
    ) -> dict[str, str]:
        require_loopback(request)
        if message.type not in {
            "face.set",
            "lights.set",
            "motion.set",
            "motion.diagnose",
            "routine.play",
            "camera.capture",
            "playback.flush",
            "capture.commit",
        }:
            raise HTTPException(status_code=400, detail="control type is not allowlisted")
        socket = active_devices.get(device_id)
        send_lock = device_send_locks.get(device_id)
        if socket is None or send_lock is None:
            raise HTTPException(status_code=404, detail="device is not connected")
        extend_motion_capture_guard(device_id, message)
        await send_locked_text(socket, send_lock, message.encode())
        return {"status": "dispatched", "device_id": device_id, "type": message.type}

    @app.websocket("/v1/device")
    async def device_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        challenge_nonce = secrets.token_hex(32)
        await websocket.send_text(
            control("auth.challenge", nonce=challenge_nonce, algorithm="hmac-sha256").encode()
        )
        trace = TraceRecorder(settings.trace_dir, capture_audio=settings.trace_audio)
        stt, llm, tts = providers()
        if settings.provider == "speech_to_speech":
            assert settings.openai_api_key is not None
            pipeline = OpenAIRealtimePipeline(
                api_key=realtime_api_key,
                memory=memory,
                trace=trace,
                url=settings.openai_realtime_url,
                model=settings.openai_realtime_model,
                voice=settings.openai_realtime_voice,
                reasoning_effort=settings.openai_realtime_reasoning_effort,
                transcription_model=settings.openai_realtime_transcription_model,
                max_output_tokens=settings.openai_realtime_max_output_tokens,
                timeout_seconds=settings.openai_realtime_timeout_seconds,
            )
        else:
            pipeline = CascadePipeline(stt, llm, tts, memory, trace)
        sensor_llm = (
            EveLLM(
                settings.eve_url,
                core_url=f"http://127.0.0.1:{settings.port}",
                timeout_seconds=settings.eve_timeout_seconds,
                approval_timeout_seconds=settings.eve_approval_timeout_seconds,
            )
            if settings.provider != "mock"
            else None
        )
        speech_gate = WebRtcSpeechGate(settings.input_sample_rate, settings.vad_aggressiveness)
        turn_detector = EnergyTurnDetector(
            settings.input_sample_rate,
            start_rms=settings.vad_start_rms,
            stop_rms=settings.vad_stop_rms,
            start_ms=settings.vad_start_ms,
            stop_ms=settings.vad_silence_ms,
            pre_roll_ms=settings.vad_pre_roll_ms,
            voice_gate=speech_gate,
        )
        duck_detector = ConsecutiveSpeechDetector(
            settings.barge_in_duck_ms, settings.audio_frame_ms
        )
        echo = EchoCanceller(delay_ms=settings.aec_delay_ms, enabled=settings.aec_enabled)
        microphone = bytearray()
        barge_microphone = bytearray()
        barge_clean_microphone = bytearray()
        barge_raw_microphone = bytearray()
        max_microphone_bytes = settings.input_sample_rate * 2 * 16
        auto_turn_detection = settings.auto_turn_detection and settings.provider != "mock"
        conversation_suspended = False
        conversation_resumed = asyncio.Event()
        conversation_resumed.set()
        turn_task: asyncio.Task[None] | None = None
        sensor_task: asyncio.Task[None] | None = None
        scheduled_worker_task: asyncio.Task[None] | None = None
        sensor_cancel = asyncio.Event()
        last_head_gesture_accepted_at = 0.0
        device_id: str | None = None
        authenticated = False
        preferred_language = "en"
        playback_started_at = 0.0
        playback_until = 0.0
        voice_barge_started_ns = 0
        voice_barge_flush_ms = 0.0
        voice_barge_preroll = b""
        voice_barge_clean_preroll = b""
        voice_barge_raw_preroll = b""
        voice_barge_probe_attempts = 0
        voice_barge_last_probe_cued = False
        voice_barge_candidate_render_correlation = 0.0
        voice_barge_candidate_raw_rms = 0.0
        barge_probe_history: list[tuple[str, str]] = []
        voice_barge_verification_task: asyncio.Task[bool] | None = None
        voice_barge_verification_audio = b""
        voice_barge_verification_clean_audio = b""
        voice_barge_verification_raw_audio = b""
        confirmed_barge_preroll = b""
        barge_turn_active = False
        playback_gate = asyncio.Event()
        playback_gate.set()
        playback_abort = asyncio.Event()
        playback_pause_started = 0.0
        playback_pause_total = 0.0
        playback_ducked = False
        pending_duck_request_id: str | None = None
        pending_duck_started_ns = 0
        voice_barge_listening_started_ns = 0
        voice_barge_listening_acoustic = False
        playback_listening_window_used = False
        pending_playback_flushes: dict[str, tuple[int, int, str, str | None]] = {}
        verified_barge_language = preferred_language
        verified_barge_audio_source = "projected"
        current_render_text = ""
        barge_rejected_until = 0.0
        playback_tail_seconds = settings.playback_tail_guard_ms / 1_000
        device_playback_active = False
        device_playback_ended_at = 0.0
        physical_render_reference = False
        send_lock = asyncio.Lock()
        pending_tool_results: dict[str, asyncio.Future[dict]] = {}
        active_motion_request_ids: set[str] = set()

        def pause_playback_stream() -> None:
            nonlocal playback_pause_started
            if not playback_gate.is_set():
                return
            playback_pause_started = asyncio.get_running_loop().time()
            playback_gate.clear()

        def resume_playback_stream() -> None:
            nonlocal playback_pause_started, playback_pause_total
            if playback_pause_started:
                playback_pause_total += asyncio.get_running_loop().time() - playback_pause_started
                playback_pause_started = 0.0
            playback_gate.set()

        async def send_text(message: str) -> None:
            if device_id:
                try:
                    outgoing = ControlMessage.decode(message)
                    extend_motion_capture_guard(device_id, outgoing)
                    if (
                        outgoing.request_id
                        and outgoing.type in {"motion.set", "routine.play"}
                    ):
                        active_motion_request_ids.add(outgoing.request_id)
                except (TypeError, ValueError):
                    pass
            await send_locked_text(websocket, send_lock, message)

        async def send_audio(message: bytes) -> bool:
            await playback_gate.wait()
            if playback_abort.is_set():
                return False
            async with send_lock:
                if not playback_gate.is_set() or playback_abort.is_set():
                    return False
                await websocket.send_bytes(message)
            return True

        def feed_render_reference(pcm: bytes) -> None:
            # Keep the AEC render level synchronized with physical playback.
            # Candidates remain at unity until semantic verification succeeds,
            # so self-echo cannot repeatedly modulate Stack-chan's voice.
            if not physical_render_reference:
                echo.feed_render_24k(
                    pcm,
                    gain=settings.barge_in_duck_gain if playback_ducked else 1.0,
                )

        def is_speaking() -> bool:
            return asyncio.get_running_loop().time() < playback_until

        def played_audio_ms() -> int:
            if playback_started_at <= 0:
                return 0
            end = playback_pause_started or asyncio.get_running_loop().time()
            return max(0, int((end - playback_started_at) * 1000))

        async def finish_stopped_tasks(tasks: list[asyncio.Task[None]]) -> None:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        async def await_stopped_producer(task: asyncio.Task[None] | None) -> None:
            if task is None or task.done():
                return
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        async def stop_playback(reason: str) -> None:
            nonlocal playback_until, sensor_task, turn_task, playback_ducked
            nonlocal pending_duck_request_id, pending_duck_started_ns
            started_ns = time.perf_counter_ns()
            playback_abort.set()
            pipeline.cancel(played_audio_ms())
            if sensor_llm is not None:
                sensor_llm.cancel()
            pending = [
                task for task in (turn_task, sensor_task) if task is not None and not task.done()
            ]
            # Do not cancel a task while it may be awaiting websocket.send_*().
            # Cancelling the ASGI send can abort an otherwise healthy device
            # transport. Both producers observe these cooperative stop signals
            # at audio-frame boundaries (normally within 20 ms).
            sensor_cancel.set()
            resume_playback_stream()
            playback_until = 0.0
            # Flush the physical queue before waiting for producer cleanup.
            # A TTS HTTP iterator may take hundreds of milliseconds to unwind;
            # that must not become audible interruption latency.
            request_id = secrets.token_hex(8)
            pending_playback_flushes[request_id] = (
                started_ns,
                started_ns,
                reason,
                None,
            )
            await send_text(
                control("playback.flush", request_id=request_id, reason=reason).encode()
            )
            playback_ducked = False
            pending_duck_request_id = None
            pending_duck_started_ns = 0
            echo.end_render()
            if pending:
                asyncio.create_task(finish_stopped_tasks(pending))

        def record_barge_in(reason: str, started_ns: int, flush_ms: float) -> None:
            trace.record("barge_in", started_ns, reason=reason, flush_ms=flush_ms)
            if device_id:
                results_for(device_id).append(
                    {
                        "type": "telemetry",
                        "component": "barge_in",
                        "reason": reason,
                        "flush_ms": round(flush_ms, 3),
                        "received_monotonic_ns": time.perf_counter_ns(),
                    }
                )

        async def interrupt_playback(reason: str) -> None:
            await stop_playback(reason)

        async def begin_voice_barge_candidate(reason: str) -> None:
            nonlocal playback_ducked, playback_listening_window_used
            nonlocal voice_barge_listening_started_ns
            nonlocal voice_barge_listening_acoustic
            nonlocal pending_duck_request_id, pending_duck_started_ns
            nonlocal voice_barge_preroll, voice_barge_clean_preroll
            nonlocal voice_barge_raw_preroll
            nonlocal voice_barge_started_ns, voice_barge_flush_ms
            nonlocal voice_barge_probe_attempts
            nonlocal voice_barge_last_probe_cued
            nonlocal voice_barge_candidate_raw_rms
            nonlocal voice_barge_candidate_render_correlation
            nonlocal voice_barge_verification_task, voice_barge_verification_audio
            nonlocal voice_barge_verification_clean_audio
            nonlocal voice_barge_verification_raw_audio
            if voice_barge_verification_task is not None:
                voice_barge_verification_task.cancel()
                await asyncio.gather(voice_barge_verification_task, return_exceptions=True)
            voice_barge_verification_task = None
            voice_barge_verification_audio = b""
            voice_barge_verification_clean_audio = b""
            voice_barge_verification_raw_audio = b""
            voice_barge_started_ns = time.perf_counter_ns()
            preroll_bytes = settings.input_sample_rate * 2 * settings.barge_in_preroll_ms // 1000
            voice_barge_preroll = bytes(barge_microphone[-preroll_bytes:])
            voice_barge_clean_preroll = bytes(barge_clean_microphone[-preroll_bytes:])
            voice_barge_raw_preroll = bytes(barge_raw_microphone[-preroll_bytes:])
            voice_barge_probe_attempts = 0
            voice_barge_listening_started_ns = 0
            voice_barge_listening_acoustic = False
            voice_barge_last_probe_cued = False
            voice_barge_candidate_render_correlation = echo.capture_render_correlation
            voice_barge_candidate_raw_rms = echo.raw_rms
            barge_probe_history.clear()
            clean_ratio = echo.clean_rms / echo.raw_rms if echo.raw_rms else 0.0
            # A very strong, separated near-end signal may open the one bounded
            # attenuation window before semantic verification. This never
            # authorizes a flush: a decoded Stop/Wait cue plus an independently
            # decoded replacement request remain mandatory. The high raw-level
            # gate avoids the low-energy self-echo candidates seen on hardware.
            open_listening_window = (
                not playback_listening_window_used
                and should_open_acoustic_listening_window(
                    enabled=settings.barge_in_acoustic_preduck_enabled,
                    raw_rms=echo.raw_rms,
                    clean_rms=echo.clean_rms,
                    render_correlation=echo.capture_render_correlation,
                    minimum_raw_rms=settings.barge_in_early_listening_min_raw_rms,
                    maximum_render_correlation=(settings.barge_in_listening_max_render_correlation),
                    minimum_clean_ratio=settings.barge_in_listening_min_clean_ratio,
                    maximum_clean_ratio=settings.barge_in_max_clean_ratio,
                )
            )
            playback_ducked = False
            voice_barge_flush_ms = (time.perf_counter_ns() - voice_barge_started_ns) / 1_000_000
            trace.record(
                "barge_candidate",
                voice_barge_started_ns,
                reason=reason,
                flush_ms=voice_barge_flush_ms,
                raw_rms=round(echo.raw_rms, 2),
                clean_rms=round(echo.clean_rms, 2),
                clean_ratio=round(clean_ratio, 4),
                render_correlation=round(echo.capture_render_correlation, 4),
                render_lag_ms=echo.capture_render_lag_ms,
                applied_aec_delay_ms=echo.applied_delay_ms,
                playback_affected=open_listening_window,
            )
            if open_listening_window:
                voice_barge_listening_acoustic = True
                pause_playback_stream()
                pending_duck_request_id = secrets.token_hex(8)
                pending_duck_started_ns = time.perf_counter_ns()
                await send_text(
                    control(
                        "playback.duck",
                        request_id=pending_duck_request_id,
                        enabled=True,
                        gain=settings.barge_in_duck_gain,
                    ).encode()
                )
                playback_listening_window_used = True
                trace.record(
                    "barge_listening_window_requested",
                    pending_duck_started_ns,
                    attenuation_db=26,
                    preliminary_cue=False,
                    acoustic_near_end=True,
                )
            # Preserve post-candidate audio for a fixed semantic probe. A
            # second frame-by-frame AEC gate lost real speech whenever the
            # robot was loud, while Whisper can reject that echo by content.
            microphone.clear()
            barge_microphone.clear()
            barge_clean_microphone.clear()
            barge_raw_microphone.clear()
            turn_detector.reset()
            turn_detector.set_start_ms(0)
            duck_detector.reset()

        async def reject_voice_barge_candidate(reason: str) -> None:
            nonlocal playback_ducked, voice_barge_started_ns, voice_barge_flush_ms
            nonlocal pending_duck_request_id, pending_duck_started_ns
            nonlocal voice_barge_listening_started_ns
            nonlocal voice_barge_listening_acoustic
            nonlocal voice_barge_preroll, voice_barge_clean_preroll
            nonlocal voice_barge_raw_preroll
            nonlocal voice_barge_probe_attempts
            nonlocal voice_barge_last_probe_cued
            nonlocal voice_barge_candidate_raw_rms
            nonlocal voice_barge_candidate_render_correlation
            nonlocal barge_rejected_until
            nonlocal voice_barge_verification_task, voice_barge_verification_audio
            nonlocal voice_barge_verification_clean_audio
            nonlocal voice_barge_verification_raw_audio
            if voice_barge_verification_task is not None:
                voice_barge_verification_task.cancel()
                await asyncio.gather(voice_barge_verification_task, return_exceptions=True)
            voice_barge_verification_task = None
            voice_barge_verification_audio = b""
            voice_barge_verification_clean_audio = b""
            voice_barge_verification_raw_audio = b""
            if playback_ducked or pending_duck_request_id is not None:
                await send_text(
                    control(
                        "playback.duck",
                        request_id=secrets.token_hex(8),
                        enabled=False,
                        gain=settings.barge_in_duck_gain,
                    ).encode()
                )
            playback_ducked = False
            pending_duck_request_id = None
            pending_duck_started_ns = 0
            resume_playback_stream()
            voice_barge_started_ns = 0
            voice_barge_flush_ms = 0.0
            voice_barge_preroll = b""
            voice_barge_clean_preroll = b""
            voice_barge_raw_preroll = b""
            voice_barge_probe_attempts = 0
            voice_barge_listening_started_ns = 0
            voice_barge_listening_acoustic = False
            voice_barge_last_probe_cued = False
            voice_barge_candidate_render_correlation = 0.0
            voice_barge_candidate_raw_rms = 0.0
            barge_probe_history.clear()
            microphone.clear()
            barge_microphone.clear()
            barge_clean_microphone.clear()
            barge_raw_microphone.clear()
            turn_detector.reset()
            turn_detector.set_start_ms(settings.vad_start_ms)
            barge_rejected_until = asyncio.get_running_loop().time() + 0.2
            trace.record("barge_rejected", time.perf_counter_ns(), reason=reason)

        async def verify_voice_barge(
            projected_audio: bytes, clean_audio: bytes, raw_audio: bytes
        ) -> bool:
            """Decode the AEC stream first and retain projection as a fallback.

            The connected microphone can alternate between two failure modes:
            WebRTC AEC can suppress a close speaker, while the lighter render
            projection can leave Stack-chan's voice dominant. Decode the clean
            stream first, but only trust it early when it contains an explicit
            playback cue (or a request anchored by an earlier cue). Otherwise
            fall back to the projected stream and apply the same semantic and
            acoustic authorization rules.
            """
            nonlocal verified_barge_language, verified_barge_audio_source
            nonlocal voice_barge_last_probe_cued
            voice_barge_last_probe_cued = False
            if not isinstance(stt, BilingualWhisperSTT):
                return True
            started_ns = time.perf_counter_ns()
            aec_audio_artifact = trace.capture_pcm16(
                clean_audio, settings.input_sample_rate, label="barge-aec"
            )
            projected_audio_artifact = trace.capture_pcm16(
                projected_audio,
                settings.input_sample_rate,
                label="barge-projected",
            )
            raw_audio_artifact = trace.capture_pcm16(
                raw_audio, settings.input_sample_rate, label="barge-raw"
            )
            continuation_window_active = playback_ducked and has_prior_explicit_barge_cue(
                barge_probe_history
            )
            # Once a cue has opened the bounded listening window, the raw
            # preferred-language continuation is the highest-value decode: it
            # can contain the replacement request that AEC suppresses. Run it
            # first because the clean cue fallback can use the same resident
            # large model; launching both made Japanese requests queue behind
            # redundant work until the confirmation deadline expired.
            raw_probes: list[tuple[str, WhisperTranscription]] = []
            raw_actionable: list[tuple[str, WhisperTranscription]] = []
            if continuation_window_active:
                raw_probes = await decode_raw_barge_continuation(
                    stt,
                    raw_audio,
                    settings.input_sample_rate,
                    preferred_language,
                )
                raw_actionable = [
                    item
                    for item in raw_probes
                    if is_actionable_barge_continuation(
                        item[1].text,
                        item[0],
                        item[1].average_log_probability,
                        current_render_text,
                        barge_probe_history,
                        confidence_threshold=(settings.barge_in_continuation_confidence_threshold),
                        # The forced alternate-language lane can recover an
                        # English Stop as Japanese 待って (and vice versa). The
                        # raw robust lane still decodes the request in the
                        # preferred language, inside the one-shot ducked window.
                        allow_cross_language_anchor=True,
                    )
                ]
            clean_probes: list[tuple[str, WhisperTranscription]] = []
            if not raw_actionable:
                clean_probes = await decode_barge_probes(
                    stt,
                    clean_audio,
                    settings.input_sample_rate,
                    preferred_language,
                    current_render_text,
                    # The preferred decoder already short-circuits on a cue and
                    # the alternate decoder still runs when that result is neither
                    # a cue nor render echo. Forcing both here serialized redundant
                    # local Whisper work ahead of the rolling replacement window.
                    always_decode_both=False,
                )
            preliminary_clean_cue = select_preliminary_barge_cue(
                clean_probes,
                current_render_text,
                confidence_threshold=(settings.barge_in_preliminary_cue_confidence_threshold),
            )
            if not continuation_window_active and preliminary_clean_cue is not None:
                # AEC turned the spoken count "seven, eight" into "Wait" on
                # real hardware and opened a 26 dB duck despite no human voice.
                # Decode the raw lane once before allowing any attenuation.
                raw_probes = await decode_raw_barge_continuation(
                    stt,
                    raw_audio,
                    settings.input_sample_rate,
                    preferred_language,
                )
            clean_selected = (
                select_barge_probe(
                    clean_probes,
                    preferred_language,
                    confidence_threshold=settings.barge_in_confidence_threshold,
                    render_text=current_render_text,
                )
                if clean_probes
                else None
            )
            clean_confidence = (
                clean_selected[1].average_log_probability if clean_selected is not None else None
            )
            clean_is_actionable = bool(
                clean_selected
                and not looks_like_render_echo(clean_selected[1].text, current_render_text)
                and (
                    (
                        has_explicit_barge_cue(clean_selected[1].text, clean_selected[0])
                        and (
                            clean_confidence is None
                            or clean_confidence >= settings.barge_in_explicit_confidence_threshold
                        )
                    )
                    or (
                        has_prior_explicit_barge_cue(barge_probe_history)
                        and has_replacement_request_cue(clean_selected[1].text, clean_selected[0])
                        and (
                            clean_confidence is None
                            or clean_confidence >= settings.barge_in_confidence_threshold
                        )
                    )
                )
            )
            raw_actionable_evidence = False
            projected_probes: list[tuple[str, WhisperTranscription]] = []
            # Projection is useful before a cue to distinguish render echo.
            # Inside the validated continuation window, however, waiting for a
            # projected retry after the robust raw lane delays the next rolling
            # probe until a Japanese replacement phrase has already passed.
            if not clean_is_actionable and not raw_actionable and not continuation_window_active:
                projected_probes = await decode_barge_probes(
                    stt,
                    projected_audio,
                    settings.input_sample_rate,
                    preferred_language,
                    current_render_text,
                    always_decode_both=False,
                )
            if raw_actionable:
                probes = raw_actionable
                audio_source = "raw"
                raw_actionable_evidence = True
            elif clean_is_actionable:
                probes = clean_probes
                audio_source = "aec"
            else:
                probes = projected_probes
                audio_source = "projected"
            if not probes:
                probes = clean_probes
                audio_source = "aec"
            if probes:
                language, probe = select_barge_probe(
                    probes,
                    preferred_language,
                    confidence_threshold=settings.barge_in_confidence_threshold,
                    render_text=current_render_text,
                )
                transcript = probe.text
                confidence = probe.average_log_probability
            else:
                probes = []
                transcript, language = await stt.fast.transcribe(
                    clean_audio, settings.input_sample_rate
                )
                confidence = None
            render_echo_languages = [
                probe_language
                for probe_language, candidate in probes
                if looks_like_render_echo(candidate.text, current_render_text)
            ]
            render_echo = looks_like_render_echo(transcript, current_render_text)
            language_switch = language != preferred_language
            # Double-talk must contain an unmistakable human cue in either
            # language. Active-language decoder hallucinations are otherwise
            # able to authorize Stack-chan's own speech as an interruption.
            switch_cued = has_explicit_barge_cue(transcript, language)
            probe_stable = is_stable_barge_language_switch(
                language, transcript, barge_probe_history
            )
            switch_stable = not language_switch or probe_stable
            intent_evidence = has_barge_intent_evidence(
                language, transcript, preferred_language, barge_probe_history
            )
            same_language_anchored_continuation = bool(
                playback_ducked
                and has_prior_explicit_barge_cue(barge_probe_history, language)
                and not switch_cued
                and not render_echo
                and not language_switch
                and is_actionable_barge_continuation(
                    transcript,
                    language,
                    confidence,
                    current_render_text,
                    barge_probe_history,
                    confidence_threshold=(settings.barge_in_continuation_confidence_threshold),
                )
                and voice_barge_candidate_render_correlation
                <= settings.barge_in_natural_max_render_correlation
            )
            cross_decoder_anchored_continuation = bool(
                playback_ducked
                and language == preferred_language
                and not switch_cued
                and is_actionable_barge_continuation(
                    transcript,
                    language,
                    confidence,
                    current_render_text,
                    barge_probe_history,
                    confidence_threshold=(settings.barge_in_continuation_confidence_threshold),
                )
            )
            anchored_continuation = (
                same_language_anchored_continuation
                or cross_decoder_anchored_continuation
                or raw_actionable_evidence
            )
            raw_control_cue_support = has_raw_control_cue_support(
                raw_probes,
                current_render_text,
                confidence_threshold=settings.barge_in_confidence_threshold,
            )
            independent_same_language_cue = bool(
                language == preferred_language
                and independent_same_language_cue_confirmed(
                    clean_probes,
                    preferred_language=preferred_language,
                    rendered_text=current_render_text,
                    render_correlation=(voice_barge_candidate_render_correlation),
                    maximum_render_correlation=(settings.barge_in_listening_max_render_correlation),
                    confidence_threshold=(settings.barge_in_explicit_confidence_threshold),
                )
            )
            cue_confirmation_supported = (
                not switch_cued
                or anchored_continuation
                or raw_control_cue_support
                or independent_same_language_cue
            )
            switch_stable = switch_stable or anchored_continuation
            intent_evidence = intent_evidence or anchored_continuation
            uncued_natural_correction = not switch_cued and not language_switch
            acoustic_intent_evidence = anchored_continuation or (
                not uncued_natural_correction
                or (
                    is_substantial_natural_barge(transcript, language)
                    and voice_barge_candidate_render_correlation
                    <= settings.barge_in_natural_max_render_correlation
                )
            )
            explicit_cue_acoustic_evidence = not switch_cued or (
                cross_language_barge_has_acoustic_support(
                    language_switch,
                    voice_barge_candidate_raw_rms,
                    settings.barge_in_cross_language_min_raw_rms,
                    preferred_decoder_is_render_echo=(preferred_language in render_echo_languages),
                )
                and voice_barge_candidate_render_correlation
                <= settings.barge_in_explicit_max_render_correlation
                and (
                    confidence is None
                    or confidence >= settings.barge_in_explicit_confidence_threshold
                    or (probe_stable and confidence >= settings.barge_in_confidence_threshold)
                )
            )
            # A narrow prompt can recover Stop/Wait from double-talk. A strong
            # same-language cue may flush immediately below; weaker or
            # cross-language evidence can only open one 26 dB listening window
            # and still needs a stable semantic retry.
            voice_barge_last_probe_cued = bool(
                switch_cued
                and is_meaningful_barge_transcript(transcript)
                and not render_echo
                and explicit_cue_acoustic_evidence
            )
            preliminary_cue_supported = bool(
                preliminary_clean_cue is not None
                and voice_barge_candidate_render_correlation
                <= settings.barge_in_explicit_max_render_correlation
                and preliminary_cue_has_independent_support(
                    raw_control_cue_supported=raw_control_cue_support,
                    cross_language_acoustic_supported=(
                        cross_language_barge_has_acoustic_support(
                            preliminary_clean_cue[0] != preferred_language,
                            voice_barge_candidate_raw_rms,
                            settings.barge_in_cross_language_min_raw_rms,
                        )
                    ),
                )
            )
            voice_barge_last_probe_cued = semantic_cue_can_open_listening_window(
                clean_cue_supported=(
                    voice_barge_last_probe_cued or preliminary_cue_supported
                ),
                raw_control_cue_supported=raw_control_cue_support,
            )
            # A person normally says Stop once, but physical speaker-only
            # regression proved that one high-confidence semantic probe can
            # still hallucinate that exact cue from Stack-chan's own render.
            # Only a same-language cue repeated inside the one-shot ducked
            # window may take this accelerated confirmation path.
            strong_single_cue = bool(
                playback_ducked
                and not language_switch
                and switch_cued
                and not render_echo
                and probe_stable
                and confidence is not None
                and confidence >= -0.25
                and voice_barge_candidate_render_correlation
                <= settings.barge_in_listening_max_render_correlation
            )
            intent_evidence = intent_evidence or strong_single_cue
            intent_evidence = intent_evidence or independent_same_language_cue
            switch_stable = switch_stable or strong_single_cue or independent_same_language_cue
            approved = (
                is_meaningful_barge_transcript(transcript)
                and not render_echo
                and intent_evidence
                and acoustic_intent_evidence
                and explicit_cue_acoustic_evidence
                and cue_confirmation_supported
                and switch_stable
                and cross_language_control_cue_can_confirm(
                    language_switch,
                    switch_cued,
                    playback_ducked=playback_ducked,
                    probe_stable=probe_stable,
                )
                and barge_confidence_is_sufficient(
                    confidence,
                    anchored_continuation=anchored_continuation,
                    general_threshold=settings.barge_in_confidence_threshold,
                    continuation_threshold=(settings.barge_in_continuation_confidence_threshold),
                )
            )
            if preliminary_cue_supported and preliminary_clean_cue is not None:
                preliminary_language, preliminary_probe = preliminary_clean_cue
                preliminary_history_item = (
                    preliminary_language,
                    preliminary_probe.text.casefold().strip(),
                )
                if preliminary_history_item != (
                    language,
                    transcript.casefold().strip(),
                ):
                    barge_probe_history.append(preliminary_history_item)
            barge_probe_history.append((language, transcript.casefold().strip()))
            del barge_probe_history[:-4]
            if approved:
                # A forced alternate-language decoder is useful as a robust
                # Stop/Wait detector, but a cue alone must not force the whole
                # replacement request into that language. Let the ordinary
                # bilingual router decide after a cross-language cue. This is
                # especially important when the Japanese decoder recovers
                # English double-talk as ``待って``.
                verified_barge_language = barge_turn_language(
                    preferred_language,
                    language,
                    explicit_cue=switch_cued,
                )
                # Preserve source fidelity: the stream that supplied the
                # actionable request must also supply the committed prefix.
                # Falling back to projected audio after an AEC/raw approval can
                # replace the person's request with Stack-chan's render echo.
                verified_barge_audio_source = audio_source
            trace.record(
                "barge_verification",
                started_ns,
                transcript=transcript,
                language=language,
                confidence=confidence,
                candidate_count=len(probes),
                probe_candidates=[
                    {
                        "source": source,
                        "language": probe_language,
                        "transcript": candidate.text,
                        "confidence": candidate.average_log_probability,
                    }
                    for source, source_probes in (
                        ("aec", clean_probes),
                        ("projected", projected_probes),
                        ("raw", raw_probes),
                    )
                    for probe_language, candidate in source_probes
                ],
                aec_audio_artifact=(aec_audio_artifact.name if aec_audio_artifact else None),
                projected_audio_artifact=(
                    projected_audio_artifact.name if projected_audio_artifact else None
                ),
                raw_audio_artifact=(raw_audio_artifact.name if raw_audio_artifact else None),
                audio_source=audio_source,
                render_echo=render_echo,
                render_echo_languages=render_echo_languages,
                language_switch=language_switch,
                switch_cued=switch_cued,
                switch_stable=switch_stable,
                probe_stable=probe_stable,
                intent_evidence=intent_evidence,
                acoustic_intent_evidence=acoustic_intent_evidence,
                explicit_cue_acoustic_evidence=explicit_cue_acoustic_evidence,
                raw_actionable_evidence=raw_actionable_evidence,
                raw_control_cue_support=raw_control_cue_support,
                independent_same_language_cue=independent_same_language_cue,
                cue_confirmation_supported=cue_confirmation_supported,
                preliminary_clean_cue=(
                    preliminary_clean_cue[1].text if preliminary_clean_cue is not None else None
                ),
                preliminary_cue_supported=preliminary_cue_supported,
                strong_single_cue=strong_single_cue,
                anchored_continuation=anchored_continuation,
                candidate_render_correlation=voice_barge_candidate_render_correlation,
                candidate_raw_rms=voice_barge_candidate_raw_rms,
                render_text=current_render_text,
                approved=approved,
            )
            return approved

        async def confirm_voice_barge() -> None:
            nonlocal turn_task, sensor_task, barge_turn_active, playback_until
            nonlocal playback_ducked
            nonlocal pending_duck_request_id, pending_duck_started_ns
            started_ns = time.perf_counter_ns()
            playback_abort.set()
            pipeline.cancel(played_audio_ms())
            sensor_cancel.set()
            resume_playback_stream()
            pending = [
                task for task in (turn_task, sensor_task) if task is not None and not task.done()
            ]
            playback_until = 0.0
            request_id = secrets.token_hex(8)
            pending_playback_flushes[request_id] = (
                started_ns,
                voice_barge_started_ns,
                "voice_barge_in_confirmed",
                verified_barge_language,
            )
            await send_text(
                control(
                    "playback.flush",
                    request_id=request_id,
                    reason="voice_barge_in",
                ).encode()
            )
            playback_ducked = False
            pending_duck_request_id = None
            pending_duck_started_ns = 0
            barge_probe_history.clear()
            echo.end_render()
            if pending:
                asyncio.create_task(finish_stopped_tasks(pending))
            # Keep the remainder of this same interrupt utterance outside the
            # playback/tail detector. Otherwise a motor or short acoustic gap
            # can split one phrase into two confirmed barge-ins.
            barge_turn_active = True
            turn_detector.set_stop_ms(settings.barge_in_silence_ms)

        async def send_turn(audio: bytes) -> None:
            nonlocal playback_started_at, playback_until, preferred_language
            nonlocal current_render_text, playback_listening_window_used
            current_render_text = ""
            playback_listening_window_used = False
            await send_text(
                control(
                    "playback.configure",
                    start_frames=(
                        settings.playback_realtime_start_frames
                        if settings.provider == "speech_to_speech"
                        else settings.playback_cascade_start_frames
                    ),
                ).encode()
            )
            loop = asyncio.get_running_loop()
            playback_started: float | None = None
            audio_sent_seconds = 0.0
            playback_lead_seconds = settings.playback_lead_ms / 1_000
            pause_baseline = playback_pause_total
            event_stream = pipeline.run_turn(audio, settings.input_sample_rate)
            try:
                async for event in event_stream:
                    if event.control:
                        tool_future: asyncio.Future[dict] | None = None
                        request_id = event.control.request_id
                        if request_id and event.control.type in {
                            "face.set",
                            "lights.set",
                            "motion.set",
                            "routine.play",
                            "camera.capture",
                        }:
                            tool_future = loop.create_future()
                            pending_tool_results[request_id] = tool_future
                        if event.control.type == "transcript.final":
                            preferred_language = str(
                                event.control.payload.get("language", preferred_language)
                            )
                        elif event.control.type == "response.text.delta":
                            current_render_text += str(event.control.payload.get("text", ""))
                        elif event.control.type == "response.text.done":
                            current_render_text = str(
                                event.control.payload.get("text", current_render_text)
                            )
                        await send_text(event.control.encode())
                        if event.control.type == "routine.play" and event.control.payload.get(
                            "music"
                        ):
                            routine = str(event.control.payload.get("name", "greet"))
                            music_frames = signature_jingle(routine)
                            if device_id:
                                results_for(device_id).append(
                                    {
                                        "type": "telemetry",
                                        "component": "routine_music",
                                        "name": routine,
                                        "frames": len(music_frames),
                                        "duration_ms": round(
                                            music_duration_seconds(routine) * 1_000
                                        ),
                                        "received_monotonic_ns": time.perf_counter_ns(),
                                    }
                                )
                            for sequence, pcm in enumerate(music_frames):
                                if playback_started is None:
                                    playback_started = loop.time()
                                    playback_started_at = playback_started
                                target = (
                                    playback_started
                                    + audio_sent_seconds
                                    - playback_lead_seconds
                                    + playback_pause_total
                                    - pause_baseline
                                )
                                playback_started, target = rebase_pacing_after_gap(
                                    playback_started,
                                    target,
                                    loop.time(),
                                    maximum_lag_seconds=(settings.audio_frame_ms * 2 / 1_000),
                                )
                                if target > loop.time():
                                    await asyncio.sleep(target - loop.time())
                                flags = AudioFlags.NONE
                                if sequence == 0:
                                    flags |= AudioFlags.START
                                if sequence == len(music_frames) - 1:
                                    flags |= AudioFlags.END
                                frame = AudioFrame(
                                    stream=AudioStream.SPEAKER,
                                    flags=flags,
                                    sequence=sequence,
                                    timestamp_ms=(int(loop.time() * 1000)) & 0xFFFFFFFF,
                                    pcm=pcm,
                                )
                                if not await send_audio(frame.encode()):
                                    return
                                feed_render_reference(pcm)
                                audio_sent_seconds += len(pcm) / 2 / settings.output_sample_rate
                                playback_until = max(
                                    playback_until,
                                    playback_started
                                    + audio_sent_seconds
                                    + playback_pause_total
                                    - pause_baseline
                                    + playback_tail_seconds,
                                )
                        if tool_future is not None and request_id:
                            try:
                                result = await asyncio.wait_for(tool_future, timeout=6.0)
                            except TimeoutError:
                                result = {
                                    "success": False,
                                    "stage": "timeout",
                                    "detail": "no correlated terminal firmware result",
                                }
                            finally:
                                pending_tool_results.pop(request_id, None)
                            complete_tool = getattr(pipeline, "complete_tool_result", None)
                            if callable(complete_tool):
                                complete_tool(request_id, result)
                    elif event.audio:
                        if playback_started is None:
                            playback_started = loop.time()
                            playback_started_at = playback_started
                        target = (
                            playback_started
                            + audio_sent_seconds
                            - playback_lead_seconds
                            + playback_pause_total
                            - pause_baseline
                        )
                        playback_started, target = rebase_pacing_after_gap(
                            playback_started,
                            target,
                            loop.time(),
                            maximum_lag_seconds=settings.audio_frame_ms * 2 / 1_000,
                        )
                        delay = target - loop.time()
                        if delay > 0:
                            await asyncio.sleep(delay)
                        if not await send_audio(event.audio.encode()):
                            return
                        feed_render_reference(event.audio.pcm)
                        audio_sent_seconds += len(event.audio.pcm) / 2 / settings.output_sample_rate
                        playback_until = max(
                            playback_until,
                            playback_started
                            + audio_sent_seconds
                            + playback_pause_total
                            - pause_baseline
                            + playback_tail_seconds,
                        )
            except WebSocketDisconnect:
                pipeline.cancel(played_audio_ms())
            except asyncio.CancelledError:
                pipeline.cancel(played_audio_ms())
                raise
            except Exception as error:
                await send_text(control("error", code="turn_failed", detail=str(error)).encode())
                await send_text(control("session.state", state="idle").encode())
            finally:
                await event_stream.aclose()

        async def execute_embodied_control(
            command_type: str, payload: dict, *, deadline_seconds: float = 8.0
        ) -> dict:
            request_id = secrets.token_hex(16)
            result_future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
            pending_tool_results[request_id] = result_future
            try:
                await send_text(
                    control(command_type, request_id=request_id, **payload).encode()
                )
                return await asyncio.wait_for(result_future, timeout=deadline_seconds)
            except TimeoutError:
                return {
                    "success": False,
                    "stage": "timeout",
                    "detail": "no correlated terminal firmware result",
                }
            finally:
                pending_tool_results.pop(request_id, None)

        async def send_sensor_reaction(
            gesture: str, scheduled: Schedule | None = None
        ) -> bool:
            nonlocal current_render_text, playback_started_at, playback_until
            reactions = {
                "touch": {
                    "routine": "curious",
                    "en": (
                        "Someone just gave the sensor on top of your head a playful "
                        "little headbutt. React naturally as Stack-chan in one cute, "
                        "short sentence. Do not describe an action as completed."
                    ),
                    "ja": (
                        "頭の上のセンサーを、誰かが遊び心のある小さな頭突きで触りました。"
                        "スタックちゃんとして、かわいく自然な一言で反応してください。"
                        "動作が完了したとは言わないでください。"
                    ),
                },
                "hold": {
                    "routine": "comfort",
                    "en": (
                        "Someone is gently petting and holding the sensor on top of "
                        "your head. React naturally as Stack-chan in one cute, short sentence."
                    ),
                    "ja": (
                        "誰かが頭の上をやさしく長くなでています。スタックちゃんとして、"
                        "かわいく自然な一言で反応してください。"
                    ),
                },
                "swipe_forward": {
                    "routine": "dance",
                    "en": (
                        "Someone playfully swiped a hand across the top of your head. "
                        "React naturally as Stack-chan in one energetic, cute, short sentence."
                    ),
                    "ja": (
                        "誰かが頭の上を遊ぶようにすっとなぞりました。スタックちゃんとして、"
                        "元気でかわいい自然な一言で反応してください。"
                    ),
                },
                "swipe_backward": {
                    "routine": "curious",
                    "en": (
                        "Someone playfully swiped a hand backward across the top of your head. "
                        "React naturally as Stack-chan in one curious, cute, short sentence."
                    ),
                    "ja": (
                        "誰かが頭の上を逆向きにすっとなぞりました。スタックちゃんとして、"
                        "不思議そうでかわいい自然な一言で反応してください。"
                    ),
                },
            }
            reaction = reactions.get(gesture)
            if scheduled is not None:
                reaction = {
                    "routine": scheduled.routine,
                    "en": scheduled.prompt,
                    "ja": scheduled.prompt,
                    "music": scheduled.music,
                    "capture_photo": scheduled.capture_photo,
                    "source": "schedule",
                }
            if reaction is None:
                return False
            language = (
                scheduled.language
                if scheduled is not None
                else preferred_language if preferred_language in {"en", "ja"} else "en"
            )
            event_context = str(reaction[language])
            action_results = [
                f"The {reaction['routine']} routine is planned and will begin "
                "together with this spoken reaction."
            ]
            if bool(reaction.get("capture_photo", False)):
                pose_result = await execute_embodied_control(
                    "motion.set",
                    {"yaw_deg": 0.0, "pitch_deg": 45.0, "duration_ms": 550},
                )
                if not bool(pose_result.get("success", False)):
                    raise RuntimeError(
                        f"scheduled camera pose failed: {pose_result.get('detail', 'unknown')}"
                    )
                capture_result = await execute_embodied_control(
                    "camera.capture", {"quality": 70}, deadline_seconds=12.0
                )
                if not bool(capture_result.get("success", False)):
                    raise RuntimeError(
                        f"scheduled photo failed: {capture_result.get('detail', 'unknown')}"
                    )
                vision = capture_result.get("vision")
                if not isinstance(vision, dict) or not vision.get("summary"):
                    raise RuntimeError("scheduled photo has no grounded local-vision result")
                action_results.append(
                    "One explicitly authorized visible still was captured for this occurrence. "
                    f"Local Vision reports: {vision['summary']}"
                )
            model_generated = settings.provider != "mock"
            reaction_llm = sensor_llm if sensor_llm is not None else llm
            sensor_model = settings.eve_model if model_generated else "mock"
            generated = ""
            with trace.span(
                "sensor_llm",
                gesture=gesture,
                language=language,
                provider=settings.provider,
                model=sensor_model,
                model_generated=model_generated,
            ) as attrs:
                async for piece in reaction_llm.generate(
                    TurnContext(
                        transcript=event_context,
                        language=language,
                        # A physical gesture is fully described by event_context.
                        # General conversation episodes can only distract or
                        # contaminate this short embodied reaction.
                        memories=[],
                        action_results=tuple(action_results),
                    )
                ):
                    if sensor_cancel.is_set():
                        reaction_llm.cancel()
                        return False
                    generated += piece
                if sensor_cancel.is_set():
                    return False
                text, _ = take_speakable_phrase(generated, language, force=True)
                attrs["response"] = text
            if not text:
                raise RuntimeError("local LLM returned an empty sensor reaction")
            # Generate first, then start motion and audio together. Dispatching
            # before local generation let short routines finish seconds before
            # the dialogue began.
            await send_text(
                control(
                    "routine.play",
                    name=reaction["routine"],
                    intensity=0.8,
                    music=bool(reaction.get("music", reaction["routine"] == "dance")),
                ).encode()
            )
            await send_text(
                control(
                    "playback.configure",
                    start_frames=settings.playback_cascade_start_frames,
                ).encode()
            )
            current_render_text = text
            if device_id:
                results_for(device_id).append(
                    {
                        "type": "telemetry",
                        "component": "sensor_reaction",
                        "gesture": gesture,
                        **({"schedule_id": scheduled.id} if scheduled is not None else {}),
                        "routine": reaction["routine"],
                        "text": text,
                        "language": language,
                        "provider": settings.provider,
                        "model": sensor_model,
                        "llm_generated": model_generated,
                        "received_monotonic_ns": time.perf_counter_ns(),
                    }
                )
            await send_text(control("session.state", state="speaking").encode())
            loop = asyncio.get_running_loop()
            playback_started: float | None = None
            audio_sent_seconds = 0.0
            lead_seconds = settings.playback_lead_ms / 1_000
            pause_baseline = playback_pause_total
            sequence = 0
            pending_pcm: bytes | None = None
            try:
                if bool(reaction.get("music", reaction["routine"] == "dance")):
                    music_frames = signature_jingle(str(reaction["routine"]))
                    if device_id:
                        results_for(device_id).append(
                            {
                                "type": "telemetry",
                                "component": "routine_music",
                                "name": reaction["routine"],
                                "frames": len(music_frames),
                                "duration_ms": round(
                                    music_duration_seconds(str(reaction["routine"])) * 1_000
                                ),
                                "received_monotonic_ns": time.perf_counter_ns(),
                            }
                        )
                    for pcm in music_frames:
                        if sensor_cancel.is_set():
                            return False
                        playback_started = playback_started or loop.time()
                        if playback_started_at == 0 or not is_speaking():
                            playback_started_at = playback_started
                        target = (
                            playback_started
                            + audio_sent_seconds
                            - lead_seconds
                            + playback_pause_total
                            - pause_baseline
                        )
                        if target > loop.time():
                            await asyncio.sleep(target - loop.time())
                        frame = AudioFrame(
                            stream=AudioStream.SPEAKER,
                            flags=AudioFlags.START if sequence == 0 else AudioFlags.NONE,
                            sequence=sequence,
                            timestamp_ms=(int(loop.time() * 1000)) & 0xFFFFFFFF,
                            pcm=pcm,
                        )
                        if not await send_audio(frame.encode()):
                            return False
                        feed_render_reference(frame.pcm)
                        audio_sent_seconds += len(frame.pcm) / 2 / settings.output_sample_rate
                        playback_until = max(
                            playback_until,
                            playback_started
                            + audio_sent_seconds
                            + playback_pause_total
                            - pause_baseline
                            + playback_tail_seconds,
                        )
                        sequence += 1
                async for pcm in tts.synthesize(text, language):
                    if sensor_cancel.is_set():
                        return False
                    if pending_pcm is not None:
                        frame = AudioFrame(
                            stream=AudioStream.SPEAKER,
                            flags=AudioFlags.START if sequence == 0 else AudioFlags.NONE,
                            sequence=sequence,
                            timestamp_ms=(int(loop.time() * 1000)) & 0xFFFFFFFF,
                            pcm=pending_pcm,
                        )
                        playback_started = playback_started or loop.time()
                        if playback_started_at == 0 or not is_speaking():
                            playback_started_at = playback_started
                        target = (
                            playback_started
                            + audio_sent_seconds
                            - lead_seconds
                            + playback_pause_total
                            - pause_baseline
                        )
                        if target > loop.time():
                            await asyncio.sleep(target - loop.time())
                        if not await send_audio(frame.encode()):
                            return False
                        feed_render_reference(frame.pcm)
                        audio_sent_seconds += len(frame.pcm) / 2 / settings.output_sample_rate
                        playback_until = max(
                            playback_until,
                            playback_started
                            + audio_sent_seconds
                            + playback_pause_total
                            - pause_baseline
                            + playback_tail_seconds,
                        )
                        sequence += 1
                    pending_pcm = pcm
                if pending_pcm is not None:
                    if sensor_cancel.is_set():
                        return False
                    flags = AudioFlags.END
                    if sequence == 0:
                        flags |= AudioFlags.START
                    frame = AudioFrame(
                        stream=AudioStream.SPEAKER,
                        flags=flags,
                        sequence=sequence,
                        timestamp_ms=(int(loop.time() * 1000)) & 0xFFFFFFFF,
                        pcm=pending_pcm,
                    )
                    playback_started = playback_started or loop.time()
                    if playback_started_at == 0 or not is_speaking():
                        playback_started_at = playback_started
                    target = (
                        playback_started
                        + audio_sent_seconds
                        - lead_seconds
                        + playback_pause_total
                        - pause_baseline
                    )
                    if target > loop.time():
                        await asyncio.sleep(target - loop.time())
                    if not await send_audio(frame.encode()):
                        return False
                    feed_render_reference(frame.pcm)
                    audio_sent_seconds += len(frame.pcm) / 2 / settings.output_sample_rate
                    playback_until = max(
                        playback_until,
                        playback_started
                        + audio_sent_seconds
                        + playback_pause_total
                        - pause_baseline
                        + playback_tail_seconds,
                    )
                await send_text(
                    control(
                        "response.text.done",
                        text=text,
                        source=("schedule" if scheduled is not None else "sensor.head"),
                        llm_generated=True,
                    ).encode()
                )
                drain_deadline = loop.time() + 30.0
                while not physical_playback_is_drained(
                    device_playback_active=device_playback_active,
                    now=loop.time(),
                    playback_until=playback_until,
                ):
                    if sensor_cancel.is_set():
                        return False
                    if loop.time() >= drain_deadline:
                        raise RuntimeError(
                            "physical playback did not become idle before the deadline"
                        )
                    await asyncio.sleep(0.05)
                await send_text(control("session.state", state="idle").encode())
                return True
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if device_id:
                    results_for(device_id).append(
                        {
                            "type": "telemetry",
                            "component": "sensor_reaction_error",
                            "gesture": gesture,
                            "detail": str(error),
                            "received_monotonic_ns": time.perf_counter_ns(),
                        }
                    )
                await send_text(
                    control("error", code="sensor_reaction_failed", detail=str(error)).encode()
                )
                await send_text(control("session.state", state="idle").encode())
                return False

        async def run_scheduled_events(queue: asyncio.Queue[Schedule]) -> None:
            nonlocal sensor_task
            while True:
                scheduled = await queue.get()
                reaction_task: asyncio.Task[bool] | None = None
                try:
                    # Codex owns the device while its control surface is open.
                    # Preserve due work in the queue until Stack-chan resumes.
                    await conversation_resumed.wait()
                    deadline = asyncio.get_running_loop().time() + 60.0
                    while (
                        (turn_task is not None and not turn_task.done())
                        or (sensor_task is not None and not sensor_task.done())
                        or is_speaking()
                    ):
                        if asyncio.get_running_loop().time() >= deadline:
                            schedules.release(
                                scheduled.id,
                                "busy_retry",
                                retry_at=time.time() + settings.schedule_retry_seconds,
                            )
                            break
                        await asyncio.sleep(0.25)
                    else:
                        sensor_cancel.clear()
                        playback_abort.clear()
                        resume_playback_stream()
                        reaction_task = asyncio.create_task(
                            send_sensor_reaction(
                                f"schedule:{scheduled.id}", scheduled=scheduled
                            )
                        )
                        sensor_task = reaction_task
                        completed = await reaction_task
                        if completed:
                            schedules.complete(scheduled.id)
                            if device_id:
                                results_for(device_id).append(
                                    {
                                        "type": "telemetry",
                                        "component": "schedule_completed",
                                        "schedule_id": scheduled.id,
                                        "label": scheduled.label,
                                        "capture_photo": scheduled.capture_photo,
                                        "received_monotonic_ns": time.perf_counter_ns(),
                                    }
                                )
                        else:
                            schedules.release(
                                scheduled.id,
                                "interrupted_retry",
                                retry_at=time.time() + settings.schedule_retry_seconds,
                            )
                except asyncio.CancelledError:
                    if reaction_task is not None and not reaction_task.done():
                        reaction_task.cancel()
                        await asyncio.gather(reaction_task, return_exceptions=True)
                    schedules.release(
                        scheduled.id,
                        "disconnected_retry",
                        retry_at=time.time() + settings.schedule_retry_seconds,
                    )
                    raise
                except Exception as error:
                    schedules.release(
                        scheduled.id,
                        "failed_retry",
                        retry_at=time.time() + settings.schedule_retry_seconds,
                    )
                    if device_id:
                        results_for(device_id).append(
                            {
                                "type": "telemetry",
                                "component": "schedule_failed",
                                "schedule_id": scheduled.id,
                                "detail": str(error),
                                "received_monotonic_ns": time.perf_counter_ns(),
                            }
                        )
                finally:
                    queue.task_done()

        async def start_turn(audio: bytes) -> None:
            nonlocal sensor_task, turn_task
            if conversation_suspended:
                return
            if len(audio) < settings.input_sample_rate // 2:
                await send_text(
                    control("error", code="empty_turn", detail="not enough speech audio").encode()
                )
                return
            if turn_task and not turn_task.done():
                pipeline.cancel(played_audio_ms())
                await await_stopped_producer(turn_task)
            if sensor_task and not sensor_task.done():
                sensor_cancel.set()
                if sensor_llm is not None:
                    sensor_llm.cancel()
                await await_stopped_producer(sensor_task)
            playback_abort.clear()
            resume_playback_stream()
            turn_task = asyncio.create_task(send_turn(audio))

        try:
            while True:
                try:
                    message = (
                        await websocket.receive()
                        if authenticated
                        else await asyncio.wait_for(websocket.receive(), timeout=3.0)
                    )
                except TimeoutError:
                    await websocket.close(code=1008, reason="hello timeout")
                    break
                if message.get("type") == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    if not authenticated:
                        await websocket.close(code=1008, reason="pairing required")
                        break
                    if conversation_suspended:
                        # Firmware also stops capture, but discard an in-flight
                        # frame at the source boundary instead of letting it
                        # seed VAD after resume.
                        continue
                    binary = message["bytes"]
                    if binary.startswith(b"STKI"):
                        image = ImageFrame.decode(binary)
                        if image.format != ImageFormat.JPEG or not (
                            image.data.startswith(b"\xff\xd8")
                            and image.data.endswith(b"\xff\xd9")
                        ):
                            await send_text(
                                control(
                                    "error",
                                    code="invalid_camera_frame",
                                    request_id=image.request_id,
                                ).encode()
                            )
                            continue
                        safe_device = re.sub(r"[^A-Za-z0-9_-]", "-", device_id or "device")
                        captured_ns = time.time_ns()
                        capture_path = captures_dir / (
                            f"{safe_device}-{captured_ns}-{image.request_id}.jpg"
                        )
                        capture_path.write_bytes(image.data)
                        vision = await vision_analyzer.analyze(capture_path)
                        capture = {
                            "request_id": image.request_id,
                            "width": image.width,
                            "height": image.height,
                            "format": "jpeg",
                            "bytes": len(image.data),
                            "captured_unix_ns": captured_ns,
                            "vision": vision,
                            "path": str(capture_path),
                        }
                        if device_id:
                            device_captures[device_id] = capture
                            results_for(device_id).append(
                                {
                                    "type": "telemetry",
                                    "component": "camera_capture",
                                    **{
                                        key: value
                                        for key, value in capture.items()
                                        if key != "path"
                                    },
                                    "artifact": str(
                                        capture_path.relative_to(settings.trace_dir.parent)
                                    ),
                                    "received_monotonic_ns": time.perf_counter_ns(),
                                }
                            )
                        continue
                    frame = AudioFrame.decode(binary)
                    if frame.stream == AudioStream.PHYSICAL_RENDER:
                        if not physical_render_reference:
                            await send_text(
                                control(
                                    "error", code="unexpected_render_reference"
                                ).encode()
                            )
                            continue
                        echo.feed_physical_render_16k(frame.pcm)
                        continue
                    if frame.stream != AudioStream.MICROPHONE:
                        error = control("error", code="wrong_audio_stream")
                        await send_text(error.encode())
                        continue
                    clean_pcm = echo.process_capture_16k(frame.pcm)
                    barge_pcm = echo.remove_aligned_render(frame.pcm)
                    microphone.extend(clean_pcm)
                    barge_microphone.extend(barge_pcm)
                    barge_clean_microphone.extend(clean_pcm)
                    barge_raw_microphone.extend(frame.pcm)
                    if len(microphone) > max_microphone_bytes:
                        del microphone[:-max_microphone_bytes]
                    if len(barge_microphone) > max_microphone_bytes:
                        del barge_microphone[:-max_microphone_bytes]
                    if len(barge_clean_microphone) > max_microphone_bytes:
                        del barge_clean_microphone[:-max_microphone_bytes]
                    if len(barge_raw_microphone) > max_microphone_bytes:
                        del barge_raw_microphone[:-max_microphone_bytes]
                    if device_id and echo.render_recent and echo.frames_processed % 100 == 0:
                        results_for(device_id).append(
                            {
                                "type": "telemetry",
                                "component": "aec",
                                "raw_rms": round(echo.raw_rms, 2),
                                "clean_rms": round(echo.clean_rms, 2),
                                "reduction_db": round(echo.reduction_db, 2),
                                "delay_ms": settings.aec_delay_ms,
                                "measured_render_lag_ms": echo.capture_render_lag_ms,
                                "render_correlation": round(echo.capture_render_correlation, 4),
                            }
                        )
                    if auto_turn_detection:
                        loop_now = asyncio.get_running_loop().time()
                        physical_playback_or_tail = device_playback_active or (
                            device_playback_ended_at > 0
                            and loop_now - device_playback_ended_at < playback_tail_seconds
                        )
                        speaking = (
                            False
                            if voice_barge_started_ns or barge_turn_active
                            else is_speaking() or physical_playback_or_tail
                        )
                        if (
                            voice_barge_started_ns
                            # A completed verifier must be consumed below before
                            # enforcing the overall probe deadline. Otherwise a
                            # valid result that finishes on the deadline frame is
                            # discarded as ``confirmation_timeout``.
                            and (
                                voice_barge_verification_task is None
                                or not voice_barge_verification_task.done()
                            )
                            and time.perf_counter_ns()
                            - (
                                voice_barge_listening_started_ns
                                or voice_barge_started_ns
                            )
                            > settings.barge_in_confirmation_timeout_ms * 1_000_000
                        ):
                            await reject_voice_barge_candidate("confirmation_timeout")
                        motion_guarded = motion_capture_is_guarded(
                            active_motion_request_ids,
                            now=loop_now,
                            guarded_until=(
                                motion_capture_guard_until.get(device_id, 0.0)
                                if device_id
                                else 0.0
                            ),
                        )
                        motion_voice_strong = motion_capture_allows_speech_start(
                            active_motion_request_ids,
                            guarded=motion_guarded,
                            clean_rms=pcm16_rms(clean_pcm),
                            minimum_rms=settings.motion_vad_start_rms,
                            voice_frame=speech_gate(clean_pcm),
                        )
                        if voice_barge_started_ns:
                            # Preserve raw candidate audio for the bilingual
                            # semantic verifier. The AEC stream continues to
                            # drive the ordinary acoustic detector at unity gain.
                            turn_detector.feed(frame.pcm, allow_start=False)
                            if pending_duck_request_id is not None:
                                if time.perf_counter_ns() - pending_duck_started_ns > 1_000_000_000:
                                    await reject_voice_barge_candidate("duck_ack_timeout")
                                continue
                            probe_interval_ms = barge_probe_interval_ms(
                                preferred_language,
                                english_ms=settings.barge_in_probe_ms,
                                japanese_ms=settings.barge_in_probe_ja_ms,
                            )
                            if playback_ducked and voice_barge_listening_started_ns:
                                # The first verifier can finish after the cue
                                # utterance has ended. Schedule continuation
                                # probes from the correlated physical duck ACK,
                                # not from the original candidate edge, so the
                                # replacement phrase has a real 420/700 ms
                                # capture window before local Whisper starts.
                                elapsed_ms = (
                                    time.perf_counter_ns() - voice_barge_listening_started_ns
                                ) / 1_000_000
                                next_probe_ms = probe_interval_ms * max(
                                    1, voice_barge_probe_attempts
                                )
                            else:
                                elapsed_ms = (
                                    time.perf_counter_ns() - voice_barge_started_ns
                                ) / 1_000_000
                                next_probe_ms = settings.barge_in_initial_probe_ms * (
                                    voice_barge_probe_attempts + 1
                                )
                            if voice_barge_verification_task is not None:
                                if not voice_barge_verification_task.done():
                                    continue
                                probe_audio = voice_barge_verification_audio
                                probe_clean_audio = voice_barge_verification_clean_audio
                                probe_raw_audio = voice_barge_verification_raw_audio
                                try:
                                    approved_barge = voice_barge_verification_task.result()
                                except Exception as error:
                                    approved_barge = False
                                    trace.record(
                                        "barge_verification_error",
                                        time.perf_counter_ns(),
                                        detail=str(error),
                                    )
                                voice_barge_verification_task = None
                                voice_barge_verification_audio = b""
                                voice_barge_verification_clean_audio = b""
                                voice_barge_verification_raw_audio = b""
                                if not approved_barge:
                                    # A real interruption begins with an explicit
                                    # playback-control word. If the first 420 ms
                                    # probe contains no such cue, retrying three
                                    # more Whisper passes only starves the audio
                                    # sender while decoding Stack-chan's echo.
                                    prior_control_cue = has_prior_explicit_barge_cue(
                                        barge_probe_history
                                    )
                                    if not voice_barge_last_probe_cued and not (
                                        playback_ducked and prior_control_cue
                                    ):
                                        await reject_voice_barge_candidate(
                                            "semantic_no_control_cue"
                                        )
                                        continue
                                    if not playback_ducked and playback_listening_window_used:
                                        await reject_voice_barge_candidate(
                                            "listening_window_exhausted"
                                        )
                                        continue
                                    if not playback_ducked:
                                        # Stop adding outbound frames while the
                                        # bounded cue-anchored continuation is
                                        # decoded. The device can drain its
                                        # existing lead at the correlated duck
                                        # gain, keeping the replacement request
                                        # out of fresh render audio.
                                        pause_playback_stream()
                                        pending_duck_request_id = secrets.token_hex(8)
                                        pending_duck_started_ns = time.perf_counter_ns()
                                        await send_text(
                                            control(
                                                "playback.duck",
                                                request_id=pending_duck_request_id,
                                                enabled=True,
                                                gain=settings.barge_in_duck_gain,
                                            ).encode()
                                        )
                                        playback_listening_window_used = True
                                        trace.record(
                                            "barge_listening_window_requested",
                                            pending_duck_started_ns,
                                            attenuation_db=26,
                                            preliminary_cue=True,
                                        )
                                    voice_barge_probe_attempts += 1
                                    if (
                                        voice_barge_probe_attempts
                                        < settings.barge_in_probe_attempts
                                    ):
                                        trace.record(
                                            "barge_probe_retry",
                                            time.perf_counter_ns(),
                                            attempt=voice_barge_probe_attempts + 1,
                                            next_probe_ms=probe_interval_ms
                                            * (voice_barge_probe_attempts + 1),
                                        )
                                        continue
                                    await reject_voice_barge_candidate("semantic_echo")
                                    continue
                                # The candidate buffer contains every frame from
                                # the detector trigger through verifier latency.
                                # Start endpointing after that prefix without
                                # feeding the approval frame a second time.
                                turn_detector.begin_turn()
                                if verified_barge_audio_source == "aec":
                                    confirmed_preroll = retain_semantic_window_and_suffix(
                                        probe_clean_audio,
                                        voice_barge_clean_preroll + bytes(barge_clean_microphone),
                                    )
                                elif verified_barge_audio_source == "raw":
                                    confirmed_preroll = retain_semantic_window_and_suffix(
                                        probe_raw_audio,
                                        voice_barge_raw_preroll + bytes(barge_raw_microphone),
                                    )
                                else:
                                    confirmed_preroll = retain_semantic_window_and_suffix(
                                        probe_audio,
                                        voice_barge_preroll + bytes(barge_microphone),
                                    )
                                await confirm_voice_barge()
                                voice_barge_started_ns = 0
                                voice_barge_flush_ms = 0.0
                                voice_barge_probe_attempts = 0
                                voice_barge_listening_started_ns = 0
                                voice_barge_listening_acoustic = False
                                confirmed_barge_preroll = confirmed_preroll
                                voice_barge_preroll = b""
                                voice_barge_clean_preroll = b""
                                voice_barge_raw_preroll = b""
                                barge_microphone.clear()
                                barge_clean_microphone.clear()
                                barge_raw_microphone.clear()
                                await send_text(
                                    control("session.state", state="listening").encode()
                                )
                                continue
                            if elapsed_ms < next_probe_ms:
                                continue
                            probe_audio = voice_barge_preroll + bytes(barge_microphone)
                            probe_clean_audio = voice_barge_clean_preroll + bytes(
                                barge_clean_microphone
                            )
                            probe_raw_audio = voice_barge_raw_preroll + bytes(barge_raw_microphone)
                            max_probe_bytes = (
                                settings.input_sample_rate
                                * 2
                                * settings.barge_in_probe_window_ms
                                // 1000
                            )
                            if len(probe_audio) > max_probe_bytes:
                                probe_audio = probe_audio[-max_probe_bytes:]
                            if len(probe_clean_audio) > max_probe_bytes:
                                probe_clean_audio = probe_clean_audio[-max_probe_bytes:]
                            raw_probe_bytes = (
                                settings.input_sample_rate
                                * 2
                                * settings.barge_in_raw_continuation_window_ms
                                // 1000
                            )
                            if len(probe_raw_audio) > raw_probe_bytes:
                                probe_raw_audio = probe_raw_audio[-raw_probe_bytes:]
                            voice_barge_verification_audio = probe_audio
                            voice_barge_verification_clean_audio = probe_clean_audio
                            voice_barge_verification_raw_audio = probe_raw_audio
                            voice_barge_verification_task = asyncio.create_task(
                                verify_voice_barge(probe_audio, probe_clean_audio, probe_raw_audio)
                            )
                            # Keep receiving microphone frames while Whisper
                            # runs; the completed task is consumed above.
                            continue
                        allow_speech_start = ordinary_capture_allows_speech_start(
                            turn_active=bool(
                                (turn_task and not turn_task.done())
                                or (sensor_task and not sensor_task.done())
                            ),
                            motion_allows_start=motion_voice_strong,
                        )
                        if speaking:
                            playback_age_ms = (
                                asyncio.get_running_loop().time() - playback_started_at
                            ) * 1000
                            confident = echo.confident_near_end(
                                minimum_clean_rms=max(
                                    settings.barge_in_min_rms,
                                    settings.motion_vad_start_rms if motion_guarded else 0,
                                    (
                                        settings.barge_in_physical_min_clean_rms
                                        if physical_render_reference
                                        else 0
                                    ),
                                ),
                                minimum_clean_ratio=(
                                    max(
                                        settings.barge_in_min_clean_ratio,
                                        settings.barge_in_physical_min_clean_ratio,
                                    )
                                    if physical_render_reference
                                    else settings.barge_in_min_clean_ratio
                                ),
                                maximum_clean_ratio=settings.barge_in_max_clean_ratio,
                                maximum_render_correlation=(
                                    min(
                                        settings.barge_in_max_render_correlation,
                                        settings.barge_in_physical_max_render_correlation,
                                    )
                                    if physical_render_reference
                                    else settings.barge_in_max_render_correlation
                                ),
                            )
                            confident_speech = (
                                motion_voice_strong
                                and loop_now >= barge_rejected_until
                                and playback_age_ms >= settings.barge_in_guard_ms
                                and confident
                                and speech_gate(clean_pcm)
                            )
                            if duck_detector.feed(confident_speech):
                                await begin_voice_barge_candidate("voice_barge_in_early")
                                continue
                            else:
                                allow_speech_start = confident_speech
                        else:
                            duck_detector.reset()
                        detection = turn_detector.feed(clean_pcm, allow_start=allow_speech_start)
                        if detection.speech_started:
                            if speaking:
                                await begin_voice_barge_candidate("voice_barge_in")
                                continue
                            await send_text(control("session.state", state="listening").encode())
                        if detection.completed_audio is not None:
                            barge_turn_active = False
                            # A verified barge temporarily sets the inactive
                            # start threshold to zero. EnergyTurnDetector.reset()
                            # intentionally preserves tuning, so restore the
                            # ordinary gate before any following capture frame.
                            turn_detector.set_start_ms(settings.vad_start_ms)
                            turn_detector.set_stop_ms(settings.vad_silence_ms)
                            microphone.clear()
                            barge_microphone.clear()
                            barge_clean_microphone.clear()
                            barge_raw_microphone.clear()
                            committed_audio = merge_audio_without_overlap(
                                confirmed_barge_preroll,
                                detection.completed_audio,
                                frame_bytes=settings.input_sample_rate
                                * 2
                                * settings.audio_frame_ms
                                // 1000,
                            )
                            confirmed_barge_preroll = b""
                            await start_turn(committed_audio)
                    continue

                raw = message.get("text")
                if raw is None:
                    continue
                command = ControlMessage.decode(raw)
                if command.type == "hello":
                    supplied_response = str(command.payload.get("auth_response", ""))
                    expected_token = (
                        settings.device_token.get_secret_value()
                        if settings.device_token is not None
                        else ""
                    )
                    proposed_device_id = str(command.payload.get("device_id", "unknown-device"))
                    device_nonce = str(command.payload.get("device_nonce", ""))
                    if not pairing_response_matches(
                        expected_token,
                        challenge_nonce,
                        device_nonce,
                        proposed_device_id,
                        supplied_response,
                    ):
                        await websocket.close(code=1008, reason="invalid pairing token")
                        break
                    existing = active_devices.get(proposed_device_id)
                    if existing is not None and existing is not websocket:
                        await websocket.close(code=1008, reason="device already connected")
                        break
                    device_id = proposed_device_id
                    device_send_locks[device_id] = send_lock
                    active_devices[device_id] = websocket
                    proactive_queue = proactive_queues.setdefault(
                        device_id, asyncio.Queue(maxsize=2)
                    )
                    scheduled_worker_task = asyncio.create_task(
                        run_scheduled_events(proactive_queue)
                    )
                    if isinstance(llm, EveLLM):
                        await llm.bind_device(device_id)
                        warmup_started_ns = time.perf_counter_ns()
                        try:
                            await llm.warm_session()
                        except (httpx.HTTPError, RuntimeError, TimeoutError) as error:
                            await llm.aclose()
                            results_for(device_id).append(
                                {
                                    "type": "telemetry",
                                    "component": "eve_session_warmup",
                                    "status": "failed",
                                    "error": type(error).__name__,
                                    "duration_ms": (
                                        time.perf_counter_ns() - warmup_started_ns
                                    )
                                    / 1_000_000,
                                }
                            )
                        else:
                            results_for(device_id).append(
                                {
                                    "type": "telemetry",
                                    "component": "eve_session_warmup",
                                    "status": "ready",
                                    "duration_ms": (
                                        time.perf_counter_ns() - warmup_started_ns
                                    )
                                    / 1_000_000,
                                }
                            )
                    if sensor_llm is not None:
                        await sensor_llm.bind_device(device_id)
                    authenticated = True
                    physical_render_reference = bool(
                        command.payload.get("physical_render_reference", False)
                    )
                    if physical_render_reference:
                        # Discard any estimated render state from before the
                        # authenticated capability handshake. Reverse frames
                        # now arrive beside the captured microphone frame, so
                        # the legacy queue-delay compensation must be disabled.
                        echo.set_delay_ms(0)
                    pipeline.memory_enabled = not bool(command.payload.get("test_session", False))
                    public_info = dict(command.payload)
                    public_info.pop("auth_response", None)
                    public_info.pop("device_nonce", None)
                    device_info[device_id] = public_info
                    results_for(device_id)
                    if command.payload.get("turn_detection") == "manual":
                        auto_turn_detection = False
                    await send_text(
                        control(
                            "hello.ack",
                            protocol_version=1,
                            input_sample_rate=settings.input_sample_rate,
                            output_sample_rate=settings.output_sample_rate,
                            trace_id=trace.trace_id,
                            server_response=pairing_proof(
                                expected_token,
                                "server",
                                challenge_nonce,
                                device_nonce,
                                proposed_device_id,
                            ),
                        ).encode()
                    )
                    await send_text(control("session.state", state="idle").encode())
                elif not authenticated:
                    await websocket.close(code=1008, reason="pairing required")
                    break
                elif command.type == "conversation.suspend":
                    conversation_suspended = True
                    conversation_resumed.clear()
                    microphone.clear()
                    barge_microphone.clear()
                    barge_clean_microphone.clear()
                    barge_raw_microphone.clear()
                    turn_detector.reset()
                    duck_detector.reset()
                    await stop_playback("codex_mode")
                    await send_text(
                        control("session.state", state="suspended", owner="codex").encode()
                    )
                    if device_id:
                        results_for(device_id).append(
                            {
                                "type": "telemetry",
                                "component": "conversation_session",
                                "state": "suspended",
                                "owner": "codex",
                                "received_monotonic_ns": time.perf_counter_ns(),
                            }
                        )
                elif command.type == "codex.focused":
                    index = command.payload.get("index")
                    if conversation_suspended and isinstance(index, int) and 0 <= index < 6:
                        # The BLE focus gesture reaches Codex before this Wi-Fi
                        # message. Give the desktop a short window to persist
                        # the focused thread's new recency, then bind only that
                        # verified title to the physical slot. The Micro status
                        # protocol itself contains no thread IDs or labels.
                        await asyncio.sleep(0.25)
                        titles = await asyncio.to_thread(recent_codex_titles, 1)
                        if titles:
                            await send_text(
                                control(
                                    "codex.session", index=index, title=titles[0]
                                ).encode()
                            )
                elif command.type == "conversation.resume":
                    conversation_suspended = False
                    microphone.clear()
                    barge_microphone.clear()
                    barge_clean_microphone.clear()
                    barge_raw_microphone.clear()
                    turn_detector.reset()
                    duck_detector.reset()
                    playback_abort.clear()
                    sensor_cancel.clear()
                    resume_playback_stream()
                    conversation_resumed.set()
                    await send_text(control("session.state", state="idle").encode())
                    if device_id:
                        results_for(device_id).append(
                            {
                                "type": "telemetry",
                                "component": "conversation_session",
                                "state": "resumed",
                                "owner": "stackchan",
                                "received_monotonic_ns": time.perf_counter_ns(),
                            }
                        )
                elif command.type == "turn.commit":
                    audio = bytes(microphone)
                    microphone.clear()
                    turn_detector.reset()
                    await start_turn(audio)
                elif command.type == "barge_in":
                    await interrupt_playback(str(command.payload.get("reason", "barge_in")))
                elif command.type == "audio.clear":
                    microphone.clear()
                    turn_detector.reset()
                elif command.type == "ping":
                    await send_text(control("pong", **command.payload).encode())
                elif command.type == "sensor.head" and conversation_suspended:
                    if device_id:
                        results_for(device_id).append(
                            {
                                "type": "telemetry",
                                "component": "sensor_head_suppressed",
                                "gesture": str(command.payload.get("gesture", "touch")),
                                "reason": "conversation_suspended",
                                "received_monotonic_ns": time.perf_counter_ns(),
                            }
                        )
                elif command.type == "sensor.head":
                    gesture = str(command.payload.get("gesture", "touch"))
                    loop_now = asyncio.get_running_loop().time()
                    playback_sensor_guard = (
                        device_playback_active
                        or is_speaking()
                        or (
                            device_playback_ended_at > 0
                            and loop_now - device_playback_ended_at < playback_tail_seconds
                        )
                    )
                    if device_id:
                        results_for(device_id).append(
                            {
                                "type": "telemetry",
                                "component": "sensor_head",
                                "gesture": gesture,
                                "zone": int(command.payload.get("zone", 0)),
                                "strength": int(command.payload.get("strength", 0)),
                                "received_monotonic_ns": time.perf_counter_ns(),
                            }
                        )
                        if playback_sensor_guard and gesture != "interrupt_hold":
                            results_for(device_id).append(
                                {
                                    "type": "telemetry",
                                    "component": "sensor_head_suppressed",
                                    "gesture": gesture,
                                    "reason": "playback_or_tail",
                                    "received_monotonic_ns": time.perf_counter_ns(),
                                }
                            )
                    if gesture in {"touch", "hold", "swipe_forward", "swipe_backward"}:
                        gesture_accepted = should_accept_head_gesture(
                            now=loop_now,
                            last_accepted_at=last_head_gesture_accepted_at,
                            cooldown_seconds=settings.head_gesture_cooldown_ms / 1_000,
                        )
                        if device_id and not playback_sensor_guard and not gesture_accepted:
                            results_for(device_id).append(
                                {
                                    "type": "telemetry",
                                    "component": "sensor_head_suppressed",
                                    "gesture": gesture,
                                    "reason": "gesture_coalesced",
                                    "received_monotonic_ns": time.perf_counter_ns(),
                                }
                            )
                        if not playback_sensor_guard and gesture_accepted:
                            last_head_gesture_accepted_at = loop_now
                            if (turn_task and not turn_task.done()) or (
                                sensor_task and not sensor_task.done()
                            ):
                                await stop_playback("sensor_head")
                                await await_stopped_producer(turn_task)
                                await await_stopped_producer(sensor_task)
                            sensor_cancel.clear()
                            playback_abort.clear()
                            resume_playback_stream()
                            sensor_task = asyncio.create_task(send_sensor_reaction(gesture))
                    elif gesture == "interrupt_hold" and playback_sensor_guard:
                        microphone.clear()
                        barge_microphone.clear()
                        barge_clean_microphone.clear()
                        barge_raw_microphone.clear()
                        turn_detector.reset()
                        duck_detector.reset()
                        await interrupt_playback("sensor_head_interrupt")
                        await send_text(control("session.state", state="listening").encode())
                        if device_id:
                            results_for(device_id).append(
                                {
                                    "type": "telemetry",
                                    "component": "sensor_head_interrupt",
                                    "gesture": gesture,
                                    "confirmed": True,
                                    "received_monotonic_ns": time.perf_counter_ns(),
                                }
                            )
                elif command.type == "playback.duck.state":
                    received_ns = time.perf_counter_ns()
                    enabled = bool(command.payload.get("enabled", False))
                    gain = float(command.payload.get("gain", 1.0))
                    if device_id:
                        results_for(device_id).append(
                            {
                                "type": "telemetry",
                                "component": "playback_duck",
                                "enabled": enabled,
                                "gain": gain,
                                **(
                                    {"request_id": command.request_id} if command.request_id else {}
                                ),
                                "received_monotonic_ns": received_ns,
                            }
                        )
                    if command.request_id and command.request_id == pending_duck_request_id:
                        request_started_ns = pending_duck_started_ns
                        pending_duck_request_id = None
                        pending_duck_started_ns = 0
                        if enabled and gain <= settings.barge_in_duck_gain + 0.001:
                            playback_ducked = True
                            voice_barge_listening_started_ns = received_ns
                            # The verifier can take long enough that the person
                            # has already spoken part (or all) of the replacement
                            # request before the physical duck acknowledgement.
                            # Preserve that bounded raw tail for the robust,
                            # cue-anchored continuation lane. Clean/projected
                            # audio still starts at the attenuation boundary so
                            # pre-duck render echo cannot crowd out its probes.
                            voice_barge_preroll = b""
                            voice_barge_clean_preroll = b""
                            voice_barge_raw_preroll = retain_recent_pcm16(
                                bytes(barge_raw_microphone),
                                settings.input_sample_rate,
                                settings.barge_in_raw_continuation_window_ms,
                            )
                            barge_microphone.clear()
                            barge_clean_microphone.clear()
                            barge_raw_microphone.clear()
                            trace.record(
                                "barge_listening_window",
                                request_started_ns,
                                attenuation_db=26,
                                preliminary_cue=(not voice_barge_listening_acoustic),
                                acoustic_near_end=voice_barge_listening_acoustic,
                                acknowledged=True,
                                acknowledgement_ms=(received_ns - request_started_ns) / 1_000_000,
                            )
                        else:
                            await reject_voice_barge_candidate("duck_ack_invalid")
                elif command.type == "playback.flush.state":
                    received_ns = time.perf_counter_ns()
                    success = bool(command.payload.get("success", False))
                    active = bool(command.payload.get("active", True))
                    firmware_duration_us = int(command.payload.get("duration_us", 0))
                    if device_id:
                        results_for(device_id).append(
                            {
                                "type": "telemetry",
                                "component": "playback_flush",
                                "success": success,
                                "active": active,
                                "firmware_duration_us": firmware_duration_us,
                                **(
                                    {"request_id": command.request_id} if command.request_id else {}
                                ),
                                "received_monotonic_ns": received_ns,
                            }
                        )
                    pending_flush = (
                        pending_playback_flushes.pop(command.request_id, None)
                        if command.request_id
                        else None
                    )
                    if pending_flush is not None:
                        (
                            flush_started_ns,
                            barge_started_ns,
                            reason,
                            robust_next_language,
                        ) = pending_flush
                        if success and not active:
                            if robust_next_language is not None and isinstance(
                                stt, BilingualWhisperSTT
                            ):
                                # Only bias the committed near-end turn after
                                # firmware has confirmed that playback really
                                # stopped. A failed/missing ack leaves ordinary
                                # STT routing untouched.
                                stt.prefer_robust_next_turn(robust_next_language)
                            record_barge_in(
                                reason,
                                barge_started_ns,
                                (received_ns - flush_started_ns) / 1_000_000,
                            )
                        else:
                            trace.record(
                                "playback_flush_failed",
                                flush_started_ns,
                                reason=reason,
                                success=success,
                                active=active,
                                firmware_duration_us=firmware_duration_us,
                            )
                            barge_turn_active = False
                            confirmed_barge_preroll = b""
                            turn_detector.reset()
                            await send_text(
                                control(
                                    "error",
                                    code="playback_flush_failed",
                                    detail="firmware did not confirm a stopped speaker",
                                ).encode()
                            )
                elif command.type == "playback.state":
                    playback_active = bool(command.payload.get("active", False))
                    if device_playback_active and not playback_active:
                        device_playback_ended_at = asyncio.get_running_loop().time()
                    device_playback_active = playback_active
                    if device_id:
                        results_for(device_id).append(
                            {
                                "type": "telemetry",
                                "component": "playback_state",
                                "active": playback_active,
                                "received_monotonic_ns": time.perf_counter_ns(),
                            }
                        )
                elif command.type in {"tool.result", "telemetry"}:
                    observed_payload = dict(command.payload)
                    if (
                        command.type == "tool.result"
                        and command.request_id
                        and device_id
                        and command.payload.get("tool") == "capture_photo"
                    ):
                        capture = device_captures.get(device_id)
                        if capture and capture.get("request_id") == command.request_id:
                            vision = capture.get("vision")
                            if isinstance(vision, dict) and vision.get("summary"):
                                observed_payload["vision"] = vision
                                observed_payload["detail"] = (
                                    f"{observed_payload.get('detail', 'photo captured')}; "
                                    f"{vision['summary']}"
                                )
                    if command.type == "tool.result" and command.request_id:
                        if tool_result_is_terminal(observed_payload):
                            if command.request_id in active_motion_request_ids:
                                active_motion_request_ids.discard(command.request_id)
                                if device_id:
                                    motion_capture_guard_until[device_id] = max(
                                        motion_capture_guard_until.get(device_id, 0.0),
                                        asyncio.get_running_loop().time()
                                        + settings.motion_capture_tail_ms / 1_000,
                                    )
                            pending = pending_tool_results.get(command.request_id)
                            if pending is not None and not pending.done():
                                pending.set_result(observed_payload)
                            bound_pending = bound_control_waiters.get(
                                (device_id, command.request_id)
                            ) if device_id else None
                            if bound_pending is not None and not bound_pending.done():
                                bound_pending.set_result(observed_payload)
                    if device_id:
                        if (
                            command.type == "telemetry"
                            and command.payload.get("component") == "audio"
                        ):
                            playback_active = bool(command.payload.get("playback_active", False))
                            if device_playback_active and not playback_active:
                                device_playback_ended_at = asyncio.get_running_loop().time()
                            device_playback_active = playback_active
                        results_for(device_id).append(
                            {
                                "type": command.type,
                                **(
                                    {"request_id": command.request_id} if command.request_id else {}
                                ),
                                "received_monotonic_ns": time.perf_counter_ns(),
                                **observed_payload,
                            }
                        )
                else:
                    await send_text(
                        control("error", code="unknown_message", received=command.type).encode()
                    )
        except WebSocketDisconnect:
            pipeline.cancel(played_audio_ms())
        finally:
            pipeline.cancel(played_audio_ms())
            sensor_cancel.set()
            pending = [
                task
                for task in (
                    turn_task,
                    sensor_task,
                    voice_barge_verification_task,
                    scheduled_worker_task,
                )
                if task is not None
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await pipeline.aclose()
            if sensor_llm is not None:
                await sensor_llm.aclose()
            if device_id and active_devices.get(device_id) is websocket:
                for (bound_id, _), waiter in list(bound_control_waiters.items()):
                    if bound_id == device_id and not waiter.done():
                        waiter.set_result(
                            {
                                "success": False,
                                "stage": "disconnected",
                                "detail": "device disconnected before the terminal result",
                            }
                        )
                active_devices.pop(device_id, None)
                device_send_locks.pop(device_id, None)
                proactive_queues.pop(device_id, None)
                device_info.pop(device_id, None)
                stale_sessions = [
                    session_id
                    for session_id, bound_id in eve_session_devices.items()
                    if bound_id == device_id
                ]
                for session_id in stale_sessions:
                    eve_session_devices.pop(session_id, None)

    return app


def main() -> None:
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )
