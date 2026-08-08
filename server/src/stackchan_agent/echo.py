import math
import time
from array import array
from collections import deque

from aec_audio_processing import AudioProcessor


class EchoCanceller:
    """WebRTC AEC adapter for Stack-chan's 24 kHz render/16 kHz capture path."""

    sample_rate = 16_000
    frame_samples = 160
    frame_bytes = frame_samples * 2

    def __init__(self, *, delay_ms: int = 160, enabled: bool = True) -> None:
        self.enabled = enabled
        self.delay_ms = delay_ms
        self._processor = self._new_processor()
        self._render_buffer = bytearray()
        self._render_history: deque[bytes] = deque(maxlen=100)
        self._last_render_ns = 0
        self.frames_processed = 0
        self.raw_rms = 0.0
        self.clean_rms = 0.0
        self.capture_render_correlation = 0.0
        self.capture_render_lag_ms = 0

    def _new_processor(self) -> AudioProcessor:
        self._processor = AudioProcessor(
            enable_aec=self.enabled,
            enable_ns=self.enabled,
            enable_agc=False,
            enable_vad=False,
        )
        self._processor.set_stream_format(self.sample_rate, 1)
        self._processor.set_reverse_stream_format(self.sample_rate, 1)
        self._processor.set_stream_delay(self.delay_ms)
        return self._processor

    def reset(self) -> None:
        self._processor = self._new_processor()
        self._render_buffer.clear()
        self._render_history.clear()
        self._last_render_ns = 0
        self.raw_rms = 0.0
        self.clean_rms = 0.0
        self.capture_render_correlation = 0.0
        self.capture_render_lag_ms = 0

    def end_render(self) -> None:
        """Mark playback stopped while preserving the adapted echo filter."""
        self._render_buffer.clear()
        self._last_render_ns = 0

    @staticmethod
    def resample_24k_to_16k(pcm: bytes) -> bytes:
        if len(pcm) % 2:
            raise ValueError("PCM16 payload must contain complete samples")
        source = array("h")
        source.frombytes(pcm)
        output = array("h")
        output_samples = len(source) * 2 // 3
        for index in range(output_samples):
            position_numerator = index * 3
            base = position_numerator // 2
            if position_numerator & 1 and base + 1 < len(source):
                output.append((source[base] + source[base + 1]) // 2)
            else:
                output.append(source[base])
        return output.tobytes()

    @staticmethod
    def _rms(pcm: bytes) -> float:
        samples = memoryview(pcm).cast("h")
        if not samples:
            return 0.0
        return math.sqrt(sum(sample * sample for sample in samples) / len(samples))

    @staticmethod
    def _correlation(left: bytes, right: bytes) -> float:
        left_samples = memoryview(left).cast("h")
        right_samples = memoryview(right).cast("h")
        if not left_samples or len(left_samples) != len(right_samples):
            return 0.0
        dot = sum(a * b for a, b in zip(left_samples, right_samples, strict=True))
        left_energy = sum(sample * sample for sample in left_samples)
        right_energy = sum(sample * sample for sample in right_samples)
        if not left_energy or not right_energy:
            return 0.0
        return abs(dot) / math.sqrt(left_energy * right_energy)

    @staticmethod
    def _remove_projection(capture: bytes, render: bytes) -> bytes:
        """Remove the best scalar render component from one aligned frame."""
        capture_samples = memoryview(capture).cast("h")
        render_samples = memoryview(render).cast("h")
        if not capture_samples or len(capture_samples) != len(render_samples):
            return capture
        render_energy = sum(sample * sample for sample in render_samples)
        if not render_energy:
            return capture
        dot = sum(
            sample * reference
            for sample, reference in zip(
                capture_samples, render_samples, strict=True
            )
        )
        coefficient = max(-4.0, min(4.0, dot / render_energy))
        residual = array("h")
        for sample, reference in zip(
            capture_samples, render_samples, strict=True
        ):
            value = round(sample - coefficient * reference)
            residual.append(max(-32768, min(32767, value)))
        return residual.tobytes()

    @property
    def render_recent(self) -> bool:
        return time.perf_counter_ns() - self._last_render_ns < 500_000_000

    @property
    def reduction_db(self) -> float:
        if self.raw_rms <= 0:
            return 0.0
        return 20 * math.log10(max(self.raw_rms, 1.0) / max(self.clean_rms, 1.0))

    def feed_render_24k(self, pcm: bytes, *, gain: float = 1.0) -> None:
        if not self.enabled:
            return
        if not 0.0 <= gain <= 1.0:
            raise ValueError("render gain must be between 0 and 1")
        self._last_render_ns = time.perf_counter_ns()
        render = self.resample_24k_to_16k(pcm)
        if gain != 1.0:
            samples = array("h")
            samples.frombytes(render)
            for index, sample in enumerate(samples):
                samples[index] = round(sample * gain)
            render = samples.tobytes()
        self._render_buffer.extend(render)
        while len(self._render_buffer) >= self.frame_bytes:
            frame = bytes(self._render_buffer[: self.frame_bytes])
            del self._render_buffer[: self.frame_bytes]
            self._render_history.append(frame)
            self._processor.process_reverse_stream(frame)

    def process_capture_16k(self, pcm: bytes) -> bytes:
        if not self.enabled:
            return pcm
        if not self.render_recent:
            self.frames_processed += len(pcm) // self.frame_bytes
            self.raw_rms = self._rms(pcm)
            self.clean_rms = self.raw_rms
            return pcm
        clean = bytearray()
        correlations: list[float] = []
        lags_ms: list[int] = []
        complete_bytes = len(pcm) - len(pcm) % self.frame_bytes
        for offset in range(0, complete_bytes, self.frame_bytes):
            frame = pcm[offset : offset + self.frame_bytes]
            # Server pacing, the device lead buffer, I2S DMA, and acoustic
            # propagation move the effective lag. Search a bounded 400 ms
            # history rather than trusting one nominal delay sample.
            if self._render_history:
                history = tuple(self._render_history)[-40:]
                frame_correlations = [
                    self._correlation(frame, reference) for reference in history
                ]
                best_index = max(
                    range(len(frame_correlations)), key=frame_correlations.__getitem__
                )
                correlations.append(frame_correlations[best_index])
                lags_ms.append((len(history) - 1 - best_index) * 10)
            clean.extend(self._processor.process_stream(frame))
            self.frames_processed += 1
        clean.extend(pcm[complete_bytes:])
        cleaned = bytes(clean)
        self.raw_rms = self._rms(pcm)
        self.clean_rms = self._rms(cleaned)
        self.capture_render_correlation = max(correlations, default=0.0)
        if correlations:
            strongest = max(range(len(correlations)), key=correlations.__getitem__)
            self.capture_render_lag_ms = lags_ms[strongest]
        return cleaned

    def remove_aligned_render(self, pcm: bytes) -> bytes:
        """Create a render-projected buffer for semantic barge verification.

        WebRTC AEC remains the normal capture path. This bounded projection is
        useful only for double-talk verification, where recent rendered speech
        otherwise dominates a short Whisper window and hides Stop/Wait.
        """
        if not self.enabled or not self.render_recent or not self._render_history:
            return pcm
        output = bytearray()
        complete_bytes = len(pcm) - len(pcm) % self.frame_bytes
        history = tuple(self._render_history)[-40:]
        for offset in range(0, complete_bytes, self.frame_bytes):
            frame = pcm[offset : offset + self.frame_bytes]
            reference = max(history, key=lambda item: self._correlation(frame, item))
            if self._correlation(frame, reference) < 0.12:
                output.extend(frame)
            else:
                output.extend(self._remove_projection(frame, reference))
        output.extend(pcm[complete_bytes:])
        return bytes(output)

    def confident_near_end(
        self,
        *,
        minimum_clean_rms: float = 600,
        minimum_clean_ratio: float = 0.08,
        maximum_clean_ratio: float = 1.25,
        maximum_render_correlation: float = 0.92,
    ) -> bool:
        """Reject correlated echo and unstable AEC amplification during playback."""
        if self.clean_rms < minimum_clean_rms or self.raw_rms <= 0:
            return False
        if self.capture_render_correlation > maximum_render_correlation:
            return False
        ratio = self.clean_rms / self.raw_rms
        return minimum_clean_ratio <= ratio <= maximum_clean_ratio
