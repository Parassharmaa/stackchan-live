#!/usr/bin/env python3
"""Verify physical touchscreen double-tap interruption during playback."""

import argparse
import asyncio
import json
import time
import urllib.parse
from pathlib import Path

from benchmark_hil_voice import (
    fetch_json,
    say,
    wait_for_device_idle,
    wait_for_device_playback,
)


async def benchmark(base_url: str, timeout_s: float) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    device_id = str(devices[0])
    encoded_device = urllib.parse.quote(device_id, safe="")
    result_url = f"{base_url}/v1/devices/{encoded_device}/results"
    idle_before = await wait_for_device_idle(base_url, device_id, timeout_s=timeout_s)
    started_ns = time.perf_counter_ns()

    await say(
        "Samantha",
        "Stack Chan, tell me a detailed story about a friendly robot exploring Japan.",
    )
    playback_started = await wait_for_device_playback(
        base_url, device_id, started_ns, timeout_s
    )

    deadline = time.monotonic() + timeout_s
    observed: list[dict] = []
    while time.monotonic() < deadline:
        results = fetch_json(result_url).get("results", [])
        observed = [
            item
            for item in results
            if item.get("received_monotonic_ns", 0) >= started_ns
        ]
        barge = next(
            (
                item
                for item in observed
                if item.get("component") == "barge_in"
                and item.get("reason") == "screen_double_tap"
            ),
            None,
        )
        playback_ended = any(
            item.get("component") == "playback_state" and item.get("active") is False
            for item in observed
        )
        if barge and playback_ended:
            return {
                "method": "physical touchscreen double tap during playback",
                "device_id": device_id,
                "idle_before_prompt": idle_before,
                "physical_playback_started": playback_started,
                "barge_in": barge,
                "physical_playback_stopped": True,
                "passed": bool(
                    idle_before
                    and playback_started
                    and float(barge.get("flush_ms", 999)) < 50
                ),
            }
        await asyncio.sleep(0.1)

    return {
        "method": "physical touchscreen double tap during playback",
        "device_id": device_id,
        "idle_before_prompt": idle_before,
        "physical_playback_started": playback_started,
        "events": [
            item
            for item in observed
            if item.get("component") in {"barge_in", "playback_state"}
        ],
        "physical_playback_stopped": False,
        "passed": False,
        "detail": f"no screen double-tap interruption within {timeout_s:g} seconds",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(benchmark(args.base_url, args.timeout))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
