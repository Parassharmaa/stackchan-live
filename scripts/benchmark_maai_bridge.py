#!/usr/bin/env python3
"""Benchmark optional MaAI inference without involving voice playback."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import struct
import time
from pathlib import Path

from stackchan_agent.config import Settings
from stackchan_agent.maai_runtime import MaaiRuntime


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]


async def run(duration_seconds: float) -> dict[str, object]:
    runtime = MaaiRuntime(Settings(provider="mock", maai_enabled=True))
    await runtime.start()
    ready_deadline = time.monotonic() + 180
    while not runtime.ready and runtime.error is None and time.monotonic() < ready_deadline:
        await asyncio.sleep(0.1)
    if not runtime.ready:
        await runtime.stop()
        raise RuntimeError(runtime.error or "MaAI did not become ready within 180 seconds")

    inference: list[float] = []
    queue_lag: list[float] = []
    frame_count = round(duration_seconds / 0.02)
    started = time.monotonic()
    for index in range(frame_count):
        samples = [
            int(2500 * math.sin(2 * math.pi * 180 * (index * 320 + offset) / 16_000))
            for offset in range(320)
        ]
        runtime.feed_capture(struct.pack(f"<{len(samples)}h", *samples))
        result = runtime.take_result()
        if result is not None:
            meta = result.get("_meta", {})
            inference.append(float(meta.get("inference_ms", 0.0)))
            queue_lag.append(float(meta.get("queue_lag_ms", 0.0)))
        target = started + (index + 1) * 0.02
        if target > time.monotonic():
            await asyncio.sleep(target - time.monotonic())
    await asyncio.sleep(0.3)
    final = runtime.take_result()
    if final is not None:
        meta = final.get("_meta", {})
        inference.append(float(meta.get("inference_ms", 0.0)))
        queue_lag.append(float(meta.get("queue_lag_ms", 0.0)))
    health = runtime.health()
    await runtime.stop()
    return {
        "method": "16 kHz PCM bridge to isolated MaAI shared encoder",
        "duration_seconds": duration_seconds,
        "frames": frame_count,
        "results": len(inference),
        "inference_ms": {
            "median": statistics.median(inference) if inference else None,
            "p95": percentile(inference, 0.95),
            "maximum": max(inference, default=None),
        },
        "queue_lag_ms": {
            "median": statistics.median(queue_lag) if queue_lag else None,
            "p95": percentile(queue_lag, 0.95),
            "maximum": max(queue_lag, default=None),
        },
        "frames_dropped": health["frames_dropped"],
        "target": {"inference_p95_ms": 50, "queue_lag_p95_ms": 100},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args.duration))
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
