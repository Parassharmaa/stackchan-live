import json
import re
import time
import uuid
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any


@dataclass(slots=True)
class Span:
    trace_id: str
    name: str
    start_ns: int
    end_ns: int
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return (self.end_ns - self.start_ns) / 1_000_000

    def as_json(self) -> dict[str, Any]:
        return {**asdict(self), "duration_ms": self.duration_ms}


class TraceRecorder:
    def __init__(
        self,
        output_dir: Path,
        trace_id: str | None = None,
        *,
        capture_audio: bool = False,
    ) -> None:
        self.trace_id = trace_id or str(uuid.uuid4())
        self.output_dir = output_dir
        self.capture_audio = capture_audio
        output_dir.mkdir(parents=True, exist_ok=True)
        self.path = output_dir / f"{self.trace_id}.jsonl"
        self._lock = Lock()
        self._audio_sequence = 0

    def capture_pcm16(
        self, pcm: bytes, sample_rate: int, *, label: str = "clean"
    ) -> Path | None:
        """Persist cleaned turn audio only when explicitly enabled for local QA."""
        if not self.capture_audio:
            return None
        if re.fullmatch(r"[a-z0-9-]+", label) is None:
            raise ValueError("audio artifact label must be lowercase ASCII")
        with self._lock:
            self._audio_sequence += 1
            path = self.output_dir / (
                f"{self.trace_id}-turn-{self._audio_sequence:03d}-{label}.wav"
            )
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(sample_rate)
                handle.writeframes(pcm)
        return path

    def record(self, name: str, start_ns: int, **attributes: Any) -> Span:
        span = Span(self.trace_id, name, start_ns, time.perf_counter_ns(), attributes)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(span.as_json(), ensure_ascii=False) + "\n")
        return span

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[dict[str, Any]]:
        start_ns = time.perf_counter_ns()
        mutable = dict(attributes)
        try:
            yield mutable
        finally:
            self.record(name, start_ns, **mutable)
