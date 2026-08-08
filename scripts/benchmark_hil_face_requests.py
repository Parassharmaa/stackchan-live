#!/usr/bin/env python3
"""Verify supported and unsupported bilingual face requests on physical Stack-chan."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from benchmark_hil_voice import (
    audible_fixture_output,
    fetch_json,
    latest_drop_count,
    latest_starvation_count,
    new_trace_events,
    say,
    trace_offsets,
    wait_for_device_idle,
    wait_for_device_playback,
)

CASES = (
    {
        "name": "english_sad",
        "voice": "Samantha",
        "prompt": "Can you make a sad face?",
        "intent_terms": ("sad", "face"),
        "expected_emotion": "sad",
    },
    {
        "name": "english_surprising_alias",
        "voice": "Samantha",
        "prompt": "Can you make a surprising face?",
        "intent_terms": ("surprising", "face"),
        "expected_emotion": "surprised",
    },
    {
        "name": "japanese_sad",
        "voice": "Kyoko",
        "prompt": "悲しい顔を見せてください。",
        "intent_terms": ("悲しい", "顔"),
        "expected_emotion": "sad",
    },
    {
        "name": "english_crying",
        "voice": "Samantha",
        "prompt": "Can you make a crying face?",
        "intent_terms": ("crying", "face"),
        "expected_emotion": "crying",
    },
    {
        "name": "japanese_crying",
        "voice": "Kyoko",
        "prompt": "泣いている顔を見せてください。",
        "intent_terms": ("泣いて", "顔"),
        "expected_emotion": "crying",
    },
)


async def wait_for_response(offsets: dict[Path, int], timeout_s: float) -> list[dict]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        events = new_trace_events(offsets)
        if any(event.get("name") == "llm" for event in events) and any(
            event.get("name") == "tts" for event in events
        ):
            return events
        await asyncio.sleep(0.15)
    return new_trace_events(offsets)


async def run_case(base_url: str, device_id: str, case: dict, timeout_s: float) -> dict:
    await wait_for_device_idle(base_url, device_id, stable_s=0.7, timeout_s=10)
    before = fetch_json(f"{base_url}/v1/devices/{device_id}/results").get("results", [])
    before_drops = latest_drop_count(before)
    before_starvations = latest_starvation_count(before)
    offsets = trace_offsets()
    started_ns = time.perf_counter_ns()
    await say(case["voice"], case["prompt"])
    events = await wait_for_response(offsets, timeout_s)
    playback_started = await wait_for_device_playback(
        base_url, device_id, started_ns, timeout_s
    )
    playback_drained = await wait_for_device_idle(
        base_url, device_id, stable_s=0.7, timeout_s=timeout_s
    )
    after = fetch_json(f"{base_url}/v1/devices/{device_id}/results").get("results", [])
    current = [
        item for item in after if item.get("received_monotonic_ns", 0) >= started_ns
    ]
    transcripts = [
        str(event.get("attributes", {}).get("transcript", ""))
        for event in events
        if event.get("name") == "stt"
    ]
    meaningful = [
        transcript
        for transcript in transcripts
        if transcript.strip()
        and not (
            transcript.strip()[0] in "([*" and transcript.strip()[-1] in ")]*"
        )
    ]
    responses = [
        str(event.get("attributes", {}).get("response", ""))
        for event in events
        if event.get("name") == "llm"
    ]
    tool_results = [item for item in current if item.get("tool") == "set_face"]
    held_faces = [item for item in current if item.get("component") == "face_hold"]
    intent_verified = bool(meaningful) and all(
        term.casefold() in meaningful[0].casefold() for term in case["intent_terms"]
    )
    unsupported = bool(case.get("unsupported"))
    if unsupported:
        response = responses[-1] if responses else ""
        behavior_verified = bool(
            not tool_results
            and any(term.casefold() in response.casefold() for term in case["reply_any"])
            and case["alternative"].casefold() in response.casefold()
        )
    else:
        emotion = case["expected_emotion"]
        behavior_verified = bool(
            any(
                item.get("stage") == "completed"
                and item.get("success") is True
                and item.get("emotion") == emotion
                for item in tool_results
            )
            and any(item.get("emotion") == emotion for item in held_faces)
        )
    after_drops = latest_drop_count(after, default=before_drops)
    after_starvations = latest_starvation_count(after, default=before_starvations)
    new_drops = after_drops - before_drops if after_drops >= before_drops else after_drops
    new_starvations = (
        after_starvations - before_starvations
        if after_starvations >= before_starvations
        else after_starvations
    )
    passed = bool(
        intent_verified
        and len(meaningful) == 1
        and len(responses) == 1
        and behavior_verified
        and playback_started
        and playback_drained
        and new_drops == 0
        and new_starvations == 0
    )
    return {
        **case,
        "transcripts": transcripts,
        "responses": responses,
        "set_face_results": tool_results,
        "face_hold_events": held_faces,
        "intent_verified": intent_verified,
        "behavior_verified": behavior_verified,
        "physical_playback_started": playback_started,
        "physical_playback_drained": playback_drained,
        "newly_dropped_playback_frames": new_drops,
        "new_playback_starvation_events": new_starvations,
        "passed": passed,
    }


async def benchmark(base_url: str, timeout_s: float, selected: set[str]) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    device_id = str(devices[0])
    results = []
    inventory = {case["name"] for case in CASES}
    unknown = selected - inventory
    if unknown:
        raise ValueError(f"unknown face HIL cases: {sorted(unknown)}")
    for case in CASES:
        if selected and case["name"] not in selected:
            continue
        results.append(await run_case(base_url, device_id, case, timeout_s))
        await asyncio.sleep(0.8)
    return {
        "device": fetch_json(f"{base_url}/v1/devices/{device_id}"),
        "method": "bilingual acoustic face requests with correlated firmware telemetry",
        "cases": results,
        "passed": all(case["passed"] for case in results),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/hil-face-requests-latest.json"),
    )
    args = parser.parse_args()
    with audible_fixture_output():
        result = asyncio.run(benchmark(args.base_url, args.timeout, set(args.case)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output, flush=True)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
