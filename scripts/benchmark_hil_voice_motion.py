#!/usr/bin/env python3
"""Run the bilingual voice-to-physical-head-motion hardware gate."""

import argparse
import asyncio
import json
from pathlib import Path

from benchmark_hil_capabilities import VOICE_ACTIONS, fetch_json, run_voice_actions


async def benchmark(base_url: str) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no Stack-chan is connected to the local server")
    device_id = devices[0]
    head_cases = tuple(case for case in VOICE_ACTIONS if case["tool"] == "move_head")
    result = await run_voice_actions(base_url, device_id, head_cases)
    return {
        "device": fetch_json(f"{base_url}/v1/devices/{device_id}"),
        "method": "physical Mac speaker to Stack-chan microphone and servo telemetry",
        **result,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/hil-voice-motion-latest.json"),
    )
    args = parser.parse_args()
    result = asyncio.run(benchmark(args.base_url))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output, flush=True)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
