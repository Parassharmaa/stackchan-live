"""Optional, failure-isolated MaAI realtime behavior integration.

The main server never imports Torch or MaAI.  Audio is sent to a subprocess
through a bounded stdio protocol, so slow inference cannot delay capture, VAD,
STT, playback, or barge-in.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import struct
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from .config import PROJECT_ROOT, Settings

_PACKET_HEADER = struct.Struct("<QIIQ")


def _probability(value: Any) -> float:
    """Return the strongest finite probability in a nested model output."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number == number and abs(number) != float("inf") else 0.0
    if isinstance(value, dict):
        return max((_probability(item) for item in value.values()), default=0.0)
    if isinstance(value, (list, tuple)):
        return max((_probability(item) for item in value), default=0.0)
    return 0.0


@dataclass(frozen=True, slots=True)
class MaaiDecision:
    behavior: str
    probability: float
    pitch_deg: float | None = None
    duration_ms: int = 450


class MaaiBehaviorArbiter:
    """Turn noisy continuous predictions into sparse, safe body behaviors."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._last_backchannel_at = -1e9
        self._last_nod_at = -1e9

    def decide(
        self,
        result: dict[str, Any],
        *,
        language: str,
        user_speaking: bool,
        robot_speaking: bool,
        conversation_suspended: bool,
        motion_busy: bool,
        now: float | None = None,
    ) -> MaaiDecision | None:
        if not user_speaking or robot_speaking or conversation_suspended or motion_busy:
            return None
        now = time.monotonic() if now is None else now

        # The nod checkpoint is Japanese-only. Never apply it to English by
        # pretending that its scores are language independent.
        nod = result.get("nod_jp", {})
        if language == "ja" and isinstance(nod, dict):
            scores = {
                "short": _probability(nod.get("p_nod_short")),
                "long": _probability(nod.get("p_nod_long")),
                "long_p": _probability(nod.get("p_nod_long_p")),
            }
            nod_kind, nod_probability = max(scores.items(), key=lambda item: item[1])
            if (
                nod_probability >= self.settings.maai_nod_threshold
                and now - self._last_nod_at >= self.settings.maai_nod_cooldown_ms / 1_000
            ):
                self._last_nod_at = now
                pitch, duration = {
                    "short": (51.0, 300),
                    "long": (57.0, 520),
                    "long_p": (39.0, 600),
                }[nod_kind]
                return MaaiDecision(
                    behavior=f"nod_{nod_kind}",
                    probability=nod_probability,
                    pitch_deg=pitch,
                    duration_ms=duration,
                )

        backchannel = result.get(
            "backchannel_ja" if language == "ja" else "backchannel_en", {}
        )
        if isinstance(backchannel, dict):
            # p_bc is the documented timing posterior. p_bc_detect is an
            # auxiliary training/detection head and must not independently
            # authorize a physical action.
            probability = _probability(backchannel.get("p_bc"))
            if (
                probability >= self.settings.maai_backchannel_threshold
                and now - self._last_backchannel_at
                >= self.settings.maai_backchannel_cooldown_ms / 1_000
            ):
                self._last_backchannel_at = now
                return MaaiDecision("backchannel", probability)
        return None


class MaaiRuntime:
    """Non-blocking client for the optional MaAI inference subprocess."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._process: asyncio.subprocess.Process | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._frames: asyncio.Queue[tuple[int, bytes, bytes, int]] = asyncio.Queue(maxsize=1)
        self._results: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        self._render: deque[tuple[float, bytes]] = deque(maxlen=25)
        self._mic_accumulator = bytearray()
        self._render_accumulator = bytearray()
        self._window_bytes = round(
            settings.input_sample_rate * 2 / settings.maai_frame_rate
        )
        self._sequence = 0
        self.ready = False
        self.error: str | None = None
        self.frames_submitted = 0
        self.frames_dropped = 0
        self.results_received = 0
        self.last_inference_ms: float | None = None
        self.last_queue_lag_ms: float | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.maai_enabled

    async def start(self) -> None:
        if not self.enabled or self._process is not None:
            return
        pixi = shutil.which("pixi")
        if pixi is None:
            self.error = "pixi executable not found"
            return
        log_path = self.settings.trace_dir.parent / "logs/maai.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("ab", buffering=0)
        command = [
            pixi,
            "run",
            "-e",
            "maai",
            "python",
            "-m",
            "stackchan_agent.maai_sidecar",
            "--frame-rate",
            str(self.settings.maai_frame_rate),
        ]
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                cwd=PROJECT_ROOT,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=log_file,
            )
        except OSError as error:
            log_file.close()
            self.error = str(error)
            return
        self._writer_task = asyncio.create_task(self._write_loop())
        self._reader_task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        tasks = [task for task in (self._writer_task, self._reader_task) if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=3)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
        self._process = None
        self.ready = False

    def feed_render(self, pcm: bytes) -> None:
        if self.enabled and pcm:
            self._render.append((time.monotonic(), pcm))

    def feed_capture(self, pcm: bytes) -> None:
        if not self.enabled or not pcm:
            return
        now = time.monotonic()
        while self._render and now - self._render[0][0] > 0.12:
            self._render.popleft()
        render = self._render.popleft()[1] if self._render else bytes(len(pcm))
        if len(render) != len(pcm):
            render = (render + bytes(len(pcm)))[: len(pcm)]
        self._mic_accumulator.extend(pcm)
        self._render_accumulator.extend(render)
        if len(self._mic_accumulator) < self._window_bytes:
            return
        mic = bytes(self._mic_accumulator[: self._window_bytes])
        render = bytes(self._render_accumulator[: self._window_bytes])
        del self._mic_accumulator[: self._window_bytes]
        del self._render_accumulator[: self._window_bytes]
        self._sequence += 1
        packet = (self._sequence, mic, render, time.perf_counter_ns())
        if self._frames.full():
            try:
                self._frames.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.frames_dropped += 1
        self._frames.put_nowait(packet)
        self.frames_submitted += 1

    def take_result(self) -> dict[str, Any] | None:
        latest = None
        while True:
            try:
                latest = self._results.get_nowait()
            except asyncio.QueueEmpty:
                return latest

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "shadow_mode": self.settings.maai_shadow_mode,
            "error": self.error,
            "frames_submitted": self.frames_submitted,
            "frames_dropped": self.frames_dropped,
            "results_received": self.results_received,
            "last_inference_ms": self.last_inference_ms,
            "last_queue_lag_ms": self.last_queue_lag_ms,
        }

    async def _write_loop(self) -> None:
        assert self._process is not None and self._process.stdin is not None
        try:
            while True:
                sequence, mic, render, captured_ns = await self._frames.get()
                self._process.stdin.write(
                    _PACKET_HEADER.pack(sequence, len(mic), len(render), captured_ns)
                    + mic
                    + render
                )
                await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as error:
            self.error = type(error).__name__
            self.ready = False

    async def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        while line := await self._process.stdout.readline():
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if payload.get("type") == "ready":
                self.ready = True
                self.error = None
                continue
            if payload.get("type") == "error":
                self.error = str(payload.get("detail", "MaAI sidecar error"))
                self.ready = False
                continue
            if payload.get("type") != "inference":
                continue
            self.results_received += 1
            self.last_inference_ms = float(payload.get("inference_ms", 0.0))
            self.last_queue_lag_ms = float(payload.get("queue_lag_ms", 0.0))
            result = payload.get("result")
            if not isinstance(result, dict):
                continue
            result["_meta"] = {
                "sequence": payload.get("sequence"),
                "inference_ms": self.last_inference_ms,
                "queue_lag_ms": self.last_queue_lag_ms,
            }
            if self._results.full():
                try:
                    self._results.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self._results.put_nowait(result)
        if self._process.returncode not in {None, 0}:
            self.error = f"sidecar exited with {self._process.returncode}"
        self.ready = False


__all__ = ["MaaiBehaviorArbiter", "MaaiDecision", "MaaiRuntime", "_PACKET_HEADER"]
