#!/usr/bin/env python3
"""Replay the exact recent head/light phrases against physical Stack-chan."""

import argparse
import asyncio
import json
from pathlib import Path

from benchmark_hil_capabilities import VOICE_ACTIONS, fetch_json, run_voice_actions
from benchmark_hil_voice import audible_fixture_output

REGRESSION_CASES = {
    "english_head_towards_left_recent_regression",
    "english_blink_lights_recent_regression",
}


async def benchmark(base_url: str) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    device_id = devices[0]
    cases = tuple(case for case in VOICE_ACTIONS if case["name"] in REGRESSION_CASES)
    if {case["name"] for case in cases} != REGRESSION_CASES:
        raise RuntimeError("recent regression case inventory is incomplete")
    result = await run_voice_actions(base_url, device_id, cases)
    return {
        "device": fetch_json(f"{base_url}/v1/devices/{device_id}"),
        "method": "exact recent phrases through Mac speaker, device microphone, and firmware telemetry",
        **result,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with audible_fixture_output():
        result = asyncio.run(benchmark(args.base_url))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output, flush=True)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
