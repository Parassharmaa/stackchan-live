"""MaAI subprocess entrypoint using length-prefixed PCM16 over stdio."""

from __future__ import annotations

import argparse
import contextlib
import json
import struct
import sys
import threading
import time
from typing import Any, BinaryIO

_PACKET_HEADER = struct.Struct("<QIIQ")


def _read_exact(stream: BinaryIO, size: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def run(frame_rate: float) -> None:
    protocol_out = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        from maai import MaaiInput, MaaiMultiple

        class PushAudio(MaaiInput.Base):
            def start(self) -> None:
                self._is_thread_started = True

            def push(self, pcm: bytes) -> None:
                samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
                self._put_to_all_queues(samples.tolist())

        microphone = PushAudio()
        system_render = PushAudio()
        model = MaaiMultiple(
            configs=[
                {"mode": "bc", "lang": "jp", "label": "backchannel_ja"},
                {"mode": "bc", "lang": "en", "label": "backchannel_en"},
                {"mode": "nod", "lang": "jp", "label": "nod_jp"},
            ],
            audio_ch1=microphone,
            audio_ch2=system_render,
            frame_rate=frame_rate,
            context_len_sec=20,
            device="cpu",
            model_type="normal",
        )
        last_inference_ms = 0.0
        inference_generation = 0
        inference_condition = threading.Condition()
        original_process = model.process

        def timed_process(audio_ch1: Any, audio_ch2: Any) -> None:
            nonlocal inference_generation, last_inference_ms
            started_ns = time.perf_counter_ns()
            previous_result_time = model.process_time_abs
            original_process(audio_ch1, audio_ch2)
            # MaAI updates process_time_abs only when a result is emitted.
            if model.process_time_abs != previous_result_time:
                with inference_condition:
                    last_inference_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
                    inference_generation += 1
                    inference_condition.notify_all()

        model.process = timed_process
        model.start()

    # MaAI prints one probability line per inference. Keep stdout exclusively
    # for the JSON protocol for the entire lifetime of its worker thread.
    sys.stdout = sys.stderr

    latest_sequence = 0
    latest_captured_ns = 0

    def emit_results() -> None:
        emitted_generation = 0
        while True:
            result = model.get_result()
            with inference_condition:
                if inference_generation <= emitted_generation:
                    inference_condition.wait(timeout=0.2)
                emitted_generation = inference_generation
            completed_ns = time.perf_counter_ns()
            payload = {
                "type": "inference",
                "sequence": latest_sequence,
                "inference_ms": round(last_inference_ms, 3),
                "queue_lag_ms": round((completed_ns - latest_captured_ns) / 1_000_000, 3),
                "result": _jsonable(result),
            }
            print(json.dumps(payload, separators=(",", ":")), file=protocol_out, flush=True)

    result_thread = threading.Thread(target=emit_results, daemon=True)
    result_thread.start()
    print(
        json.dumps({"type": "ready", "frame_rate": frame_rate}),
        file=protocol_out,
        flush=True,
    )

    stream = sys.stdin.buffer
    while header := _read_exact(stream, _PACKET_HEADER.size):
        sequence, mic_size, render_size, captured_ns = _PACKET_HEADER.unpack(header)
        if mic_size > 128_000 or render_size > 128_000:
            raise ValueError("audio packet exceeds safety limit")
        mic = _read_exact(stream, mic_size)
        render = _read_exact(stream, render_size)
        if mic is None or render is None:
            break
        latest_sequence = sequence
        latest_captured_ns = captured_ns
        microphone.push(mic)
        system_render.push(render)

    with contextlib.redirect_stdout(sys.stderr):
        model.stop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frame-rate", type=float, default=10.0)
    args = parser.parse_args()
    try:
        run(args.frame_rate)
    except Exception as error:  # sidecar boundary: serialize failures to parent
        print(
            json.dumps({"type": "error", "detail": f"{type(error).__name__}: {error}"}),
            file=sys.__stdout__,
            flush=True,
        )
        raise


if __name__ == "__main__":
    main()
