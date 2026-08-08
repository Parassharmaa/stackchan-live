import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import webrtcvad


@dataclass(frozen=True, slots=True)
class TurnDetection:
    speech_started: bool = False
    completed_audio: bytes | None = None


def pcm16_rms(pcm16: bytes) -> int:
    if not pcm16:
        return 0
    values = memoryview(pcm16).cast("h")
    return math.isqrt(sum(value * value for value in values) // len(values))


class EnergyTurnDetector:
    """Low-cost streaming endpoint detector for 16-bit mono PCM."""

    def __init__(
        self,
        sample_rate: int,
        *,
        start_rms: int = 520,
        stop_rms: int = 320,
        start_ms: int = 80,
        stop_ms: int = 560,
        pre_roll_ms: int = 240,
        min_turn_ms: int = 260,
        max_turn_ms: int = 15_000,
        voice_gate: Callable[[bytes], bool] | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.start_rms = start_rms
        self.stop_rms = stop_rms
        self.start_samples = sample_rate * start_ms // 1000
        self.stop_samples = sample_rate * stop_ms // 1000
        self.pre_roll_samples = sample_rate * pre_roll_ms // 1000
        self.min_turn_samples = sample_rate * min_turn_ms // 1000
        self.max_turn_samples = sample_rate * max_turn_ms // 1000
        self.voice_gate = voice_gate
        self._pre_roll: deque[bytes] = deque()
        self._pre_roll_count = 0
        self._voiced_samples = 0
        self._silence_samples = 0
        self._turn_samples = 0
        self._active = False
        self._audio = bytearray()

    @property
    def active(self) -> bool:
        return self._active

    def set_start_ms(self, start_ms: int) -> None:
        """Adjust only the next inactive speech-start gate."""
        self.start_samples = self.sample_rate * max(0, start_ms) // 1000

    def set_stop_ms(self, stop_ms: int) -> None:
        """Adjust the silence needed to finish the current or next turn."""
        self.stop_samples = self.sample_rate * max(0, stop_ms) // 1000

    def reset(self) -> None:
        self._pre_roll.clear()
        self._pre_roll_count = 0
        self._voiced_samples = 0
        self._silence_samples = 0
        self._turn_samples = 0
        self._active = False
        self._audio.clear()

    def snapshot_audio(self) -> bytes:
        """Return the bounded audio accumulated for the current start decision."""
        if self._active:
            return bytes(self._audio)
        return b"".join(self._pre_roll)

    def begin_turn(self) -> None:
        """Activate endpointing after an independently verified audio prefix."""
        self._pre_roll.clear()
        self._pre_roll_count = 0
        self._voiced_samples = 0
        self._silence_samples = 0
        self._turn_samples = 0
        self._audio.clear()
        self._active = True

    def _remember_pre_roll(self, pcm16: bytes, samples: int) -> None:
        self._pre_roll.append(pcm16)
        self._pre_roll_count += samples
        while self._pre_roll and self._pre_roll_count > self.pre_roll_samples:
            removed = self._pre_roll.popleft()
            self._pre_roll_count -= len(removed) // 2

    def feed(self, pcm16: bytes, *, allow_start: bool = True) -> TurnDetection:
        if not pcm16:
            return TurnDetection()
        samples = len(pcm16) // 2
        rms = pcm16_rms(pcm16)
        speech_like = self.voice_gate(pcm16) if self.voice_gate else True
        if not self._active:
            self._remember_pre_roll(pcm16, samples)
            self._voiced_samples = (
                self._voiced_samples + samples
                if allow_start and rms >= self.start_rms and speech_like
                else 0
            )
            if self._voiced_samples < self.start_samples:
                return TurnDetection()
            self._active = True
            self._audio.extend(b"".join(self._pre_roll))
            self._turn_samples = self._pre_roll_count
            self._pre_roll.clear()
            self._pre_roll_count = 0
            return TurnDetection(speech_started=True)

        self._audio.extend(pcm16)
        self._turn_samples += samples
        self._silence_samples = (
            self._silence_samples + samples if rms <= self.stop_rms or not speech_like else 0
        )
        complete = (
            self._turn_samples >= self.min_turn_samples
            and self._silence_samples >= self.stop_samples
        ) or self._turn_samples >= self.max_turn_samples
        if not complete:
            return TurnDetection()
        audio = bytes(self._audio)
        self.reset()
        return TurnDetection(completed_audio=audio)


class WebRtcSpeechGate:
    """WebRTC speech classifier for exact 10/20/30 ms PCM16 frames."""

    def __init__(self, sample_rate: int = 16_000, aggressiveness: int = 2) -> None:
        self.sample_rate = sample_rate
        self._vad = webrtcvad.Vad(aggressiveness)

    def __call__(self, pcm16: bytes) -> bool:
        try:
            return self._vad.is_speech(pcm16, self.sample_rate)
        except webrtcvad.Error:
            return False


class ConsecutiveSpeechDetector:
    """Fire once after a short uninterrupted run of confident speech frames."""

    def __init__(self, required_ms: int, frame_ms: int = 20) -> None:
        self.required_frames = max(1, (required_ms + frame_ms - 1) // frame_ms)
        self.frames = 0
        self.fired = False

    def reset(self) -> None:
        self.frames = 0
        self.fired = False

    def feed(self, speech_like: bool) -> bool:
        if self.fired:
            return False
        self.frames = self.frames + 1 if speech_like else 0
        if self.frames < self.required_frames:
            return False
        self.fired = True
        return True


class WindowedSpeechDetector:
    """Fire when enough voiced frames occur inside a bounded recent window."""

    def __init__(self, required_ms: int, window_ms: int, frame_ms: int = 20) -> None:
        self.required_frames = max(1, (required_ms + frame_ms - 1) // frame_ms)
        self.window_frames = max(self.required_frames, (window_ms + frame_ms - 1) // frame_ms)
        self.frames: deque[bool] = deque(maxlen=self.window_frames)
        self.fired = False

    def reset(self) -> None:
        self.frames.clear()
        self.fired = False

    def feed(self, speech_like: bool) -> bool:
        if self.fired:
            return False
        self.frames.append(speech_like)
        if sum(self.frames) < self.required_frames:
            return False
        self.fired = True
        return True
