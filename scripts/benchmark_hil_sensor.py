#!/usr/bin/env python3
"""Verify a physical top-sensor gesture and its coordinated reaction."""

import argparse
import asyncio
import json
import time
import urllib.request
from pathlib import Path


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


async def benchmark(
    base_url: str, timeout_s: float, expected_gesture: str | None = None
) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    device_id = devices[0]
    started_ns = time.perf_counter_ns()
    deadline = time.monotonic() + timeout_s
    latest: list[dict] = []
    event: dict | None = None
    while time.monotonic() < deadline:
        payload = await asyncio.to_thread(
            fetch_json, f"{base_url}/v1/devices/{device_id}/results"
        )
        latest = [
            item
            for item in payload.get("results", [])
            if item.get("received_monotonic_ns", 0) >= started_ns
        ]
        sensor = [item for item in latest if item.get("component") == "sensor_head"]
        if event is None and sensor:
            matching = [
                item
                for item in sensor
                if expected_gesture is None or item.get("gesture") == expected_gesture
            ]
            if matching:
                event = matching[-1]
        if event is not None:
            event_ns = int(event.get("received_monotonic_ns", started_ns))
            playback = [
                item
                for item in latest
                if item.get("component") == "playback_state"
                and item.get("received_monotonic_ns", 0) >= event_ns
            ]
            motion = [
                item
                for item in latest
                if item.get("type") == "tool.result"
                and item.get("tool") == "play_routine"
                and item.get("received_monotonic_ns", 0) >= event_ns
            ]
            reactions = [
                item
                for item in latest
                if item.get("component") == "sensor_reaction"
                and item.get("gesture") == event.get("gesture")
                and item.get("received_monotonic_ns", 0) >= event_ns
            ]
            health = [item for item in latest if item.get("component") == "head_sensor"]
            reaction = reactions[-1] if reactions else None
            reaction_routine = reaction.get("routine") if reaction else None
            matching_motion = [
                item for item in motion if item.get("routine") == reaction_routine
            ]
            dispatched_motion = next(
                (
                    item
                    for item in matching_motion
                    if item.get("stage") == "dispatched" and item.get("success") is True
                ),
                None,
            )
            completed_motion = next(
                (
                    item
                    for item in reversed(matching_motion)
                    if item.get("stage") == "completed" and item.get("success") is True
                ),
                None,
            )
            motion_failed = any(item.get("success") is False for item in matching_motion)
            playback_start = next(
                (item for item in playback if item.get("active") is True), None
            )
            playback_end = next(
                (
                    item
                    for item in playback
                    if item.get("active") is False
                    and playback_start
                    and item.get("received_monotonic_ns", 0)
                    >= playback_start.get("received_monotonic_ns", 0)
                ),
                None,
            )
            motion_playback_overlap = bool(
                dispatched_motion
                and completed_motion
                and playback_start
                and playback_end
                and playback_start.get("received_monotonic_ns", 0)
                <= completed_motion.get("received_monotonic_ns", 0)
                and playback_end.get("received_monotonic_ns", 0)
                >= dispatched_motion.get("received_monotonic_ns", 0)
            )
            report = {
                "method": "physical Si12T top-sensor gesture",
                "device_id": device_id,
                "sensor_event": event,
                "sensor_health": health[-1] if health else None,
                "llm_reaction": reaction,
                "motion_dispatched": dispatched_motion,
                "physical_playback_started": any(
                    item.get("active") is True for item in playback
                ),
                "physical_playback_drained": any(
                    item.get("active") is False for item in playback
                ),
                "motion_completed": completed_motion,
                "motion_playback_overlap": motion_playback_overlap,
            }
            report["passed"] = bool(
                event.get("gesture") in {
                    "touch",
                    "hold",
                    "swipe_forward",
                    "swipe_backward",
                }
                and event.get("strength", 0) > 0
                and reaction
                and reaction.get("llm_generated") is True
                and reaction.get("provider") in {"cascade", "speech_to_speech"}
                and str(reaction.get("model", "")).strip()
                and reaction.get("model") != "mock"
                and str(reaction.get("text", "")).strip()
                and completed_motion
                and completed_motion.get("success") is True
                and dispatched_motion
                and not motion_failed
                and health
                and health[-1].get("present") is True
                and health[-1].get("ready") is True
                and health[-1].get("read_ok") is True
                and report["physical_playback_started"]
                and report["physical_playback_drained"]
                and motion_playback_overlap
            )
            if report["passed"]:
                return report
        await asyncio.sleep(0.1)
    health = [item for item in latest if item.get("component") == "head_sensor"]
    return {
        "method": "physical Si12T top-sensor gesture",
        "device_id": device_id,
        "sensor_event": event,
        "sensor_health": health[-1] if health else None,
        "passed": False,
        "detail": (
            f"no complete physical sensor reaction observed within {timeout_s:g} seconds"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument(
        "--gesture",
        choices=("touch", "hold", "swipe_forward", "swipe_backward"),
        help="require a specific physical gesture",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/hil-sensor-latest.json"),
    )
    args = parser.parse_args()
    report = asyncio.run(benchmark(args.base_url, args.timeout, args.gesture))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
