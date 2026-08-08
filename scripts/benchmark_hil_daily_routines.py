#!/usr/bin/env python3
"""Verify new daily embodied presets through direct and bilingual acoustic lanes."""

import argparse
import asyncio
import json
from pathlib import Path

from benchmark_hil_capabilities import (
    VOICE_ACTIONS,
    run_direct_routines,
    run_voice_actions,
)
from benchmark_hil_voice import audible_fixture_output, fetch_json


async def benchmark(base_url: str) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    device_id = str(devices[0])
    selected_names = {
        "english_wake_up",
        "japanese_focus",
        "english_good_night",
        "english_dance_music",
        "japanese_bedtime_music",
    }
    selected = tuple(case for case in VOICE_ACTIONS if case["name"] in selected_names)
    direct = await run_direct_routines(base_url, device_id)
    voice = await run_voice_actions(base_url, device_id, selected)
    return {
        "device_id": device_id,
        "method": "direct firmware telemetry plus bilingual acoustic daily-routine commands",
        "direct_routines": direct,
        "voice_routines": voice,
        "passed": direct["passed"] and voice["passed"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/hil-daily-routines-latest.json"),
    )
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
