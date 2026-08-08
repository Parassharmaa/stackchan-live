#!/usr/bin/env python3
"""Repeated physical bilingual turns for transport and false-barge soak testing."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from benchmark_hil_voice import (
    CASES,
    fetch_json,
    intent_recognized,
    latest_drop_count,
    latest_starvation_count,
    run_case,
)
from stackchan_agent.metrics import speech_error_rate


def case_passed(case: dict) -> bool:
    return bool(
        case.get("idle_before_prompt", False)
        and case["observed_two_turns"]
        and case.get("unexpected_turns", 0) == 0
        and case["playback_observed_before_interrupt"]
        and case.get("physical_playback_started", False)
        and case.get("physical_playback_drained", False)
        and case.get("newly_dropped_playback_frames", 1) == 0
        and case.get("new_playback_starvation_events", 1) == 0
        and case.get("observed_barge_in", False)
        and case["unexpected_barge_ins"] == 0
        and case["response_completed"]
        and max(case["barge_in_flush_ms"], default=0) < 50
        and case.get("prompt_intent_recognized", False)
        and case.get("interrupt_intent_recognized", False)
    )


async def soak(
    base_url: str, duration_s: float, response_wait_s: float, interval_s: float
) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    device_id = devices[0]
    initial_device = fetch_json(f"{base_url}/v1/devices/{device_id}")
    initial_results = fetch_json(f"{base_url}/v1/devices/{device_id}/results")
    initial_playback_drops = latest_drop_count(initial_results.get("results", []))
    initial_starvations = latest_starvation_count(initial_results.get("results", []))
    started = time.monotonic()
    deadline = started + duration_s
    cases: list[dict] = []
    disconnects = 0
    maximum_playback_drops = initial_playback_drops
    maximum_starvations = initial_starvations
    index = 0
    while time.monotonic() < deadline:
        language = tuple(CASES)[index % len(CASES)]
        case = await run_case(language, response_wait_s, base_url, device_id)
        case["passed"] = case_passed(case)
        cases.append(case)
        index += 1
        try:
            current = fetch_json(f"{base_url}/v1/devices/{device_id}")
            if current.get("boot_count") != initial_device.get("boot_count"):
                disconnects += 1
            results = fetch_json(f"{base_url}/v1/devices/{device_id}/results")
            maximum_playback_drops = max(
                maximum_playback_drops,
                latest_drop_count(
                    results.get("results", []), default=maximum_playback_drops
                ),
            )
            maximum_starvations = max(
                maximum_starvations,
                latest_starvation_count(
                    results.get("results", []), default=maximum_starvations
                ),
            )
        except (OSError, TimeoutError, json.JSONDecodeError):
            disconnects += 1
        remaining = deadline - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(interval_s, remaining))

    final_devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    final_device = (
        fetch_json(f"{base_url}/v1/devices/{device_id}")
        if device_id in final_devices
        else {}
    )
    return summarize(
        {
            "method": "alternating physical bilingual acoustic interruption soak",
            "requested_duration_s": duration_s,
            "actual_duration_s": round(time.monotonic() - started, 3),
            "device_id": device_id,
            "initial_device": initial_device,
            "final_device": final_device,
            "disconnect_observations": disconnects,
            "initial_playback_dropped_frames": initial_playback_drops,
            "maximum_playback_dropped_frames": maximum_playback_drops,
            "initial_playback_starvation_events": initial_starvations,
            "maximum_playback_starvation_events": maximum_starvations,
            "new_playback_dropped_frames": max(
                0, maximum_playback_drops - initial_playback_drops
            ),
            "new_playback_starvation_events": max(
                0, maximum_starvations - initial_starvations
            ),
            "cases": cases,
        }
    )


def summarize(report: dict) -> dict:
    cases = report.get("cases", [])
    for case in cases:
        language = case.get("language", "en")
        transcripts = case.get("transcripts", [])
        expected = CASES.get(language, CASES["en"])
        case["prompt_error_rate"] = round(
            speech_error_rate(expected["prompt"], transcripts[0], language)
            if transcripts
            else 1.0,
            3,
        )
        case["interrupt_error_rate"] = round(
            speech_error_rate(expected["interrupt"], transcripts[1], language)
            if len(transcripts) > 1
            else 1.0,
            3,
        )
        case["prompt_intent_recognized"] = bool(transcripts) and intent_recognized(
            transcripts[0], expected["prompt_intent_terms"]
        )
        case["interrupt_intent_recognized"] = (
            len(transcripts) > 1
            and intent_recognized(
                transcripts[1], expected["interrupt_intent_terms"]
            )
        )
        case["passed"] = case_passed(case)
    initial_device = report.get("initial_device", {})
    final_device = report.get("final_device", {})
    boot_stable = final_device.get("boot_count") == initial_device.get("boot_count")
    false_barges = sum(case["unexpected_barge_ins"] for case in cases)
    confirmed_barges = sum(len(case["barge_in_flush_ms"]) for case in cases)
    completed = sum(bool(case["response_completed"]) for case in cases)
    passed_cases = sum(bool(case["passed"]) for case in cases)
    initial_playback_drops = int(report.get("initial_playback_dropped_frames", 0))
    maximum_playback_drops = int(
        report.get("maximum_playback_dropped_frames", initial_playback_drops)
    )
    new_playback_drops = max(0, maximum_playback_drops - initial_playback_drops)
    initial_starvations = int(report.get("initial_playback_starvation_events", 0))
    maximum_starvations = int(
        report.get("maximum_playback_starvation_events", initial_starvations)
    )
    new_starvations = max(0, maximum_starvations - initial_starvations)
    report.update(
        {
            "boot_stable": boot_stable,
            "case_count": len(cases),
            "passed_case_count": passed_cases,
            "recognition_pass_rate": (
                round(passed_cases / len(cases), 3) if cases else 0.0
            ),
            "completed_response_count": completed,
            "false_barge_count": false_barges,
            "confirmed_barge_count": confirmed_barges,
            "new_playback_dropped_frames": new_playback_drops,
            "new_playback_starvation_events": new_starvations,
            "passed": bool(
                cases
                and passed_cases / len(cases) >= 0.9
                and completed == len(cases)
                and false_barges == 0
                and confirmed_barges >= 2
                and new_playback_drops == 0
                and new_starvations == 0
                and report.get("disconnect_observations", 0) == 0
                and boot_stable
            ),
        }
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--duration", type=float, default=300)
    parser.add_argument("--response-wait", type=float, default=20)
    parser.add_argument("--interval", type=float, default=4)
    parser.add_argument("--input", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/hil-soak-latest.json"),
    )
    args = parser.parse_args()
    result = (
        summarize(json.loads(args.input.read_text()))
        if args.input
        else asyncio.run(
            soak(args.base_url, args.duration, args.response_wait, args.interval)
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
