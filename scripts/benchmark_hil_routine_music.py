#!/usr/bin/env python3
"""Verify a voice-triggered face/light/head/music routine on physical Stack-chan."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from benchmark_hil_voice import (
    fetch_json,
    latest_drop_count,
    latest_starvation_count,
    new_trace_events,
    say,
    trace_offsets,
)

PROMPTS = {
    "en": ("Samantha", "Please play music and dance."),
    "ja": ("Kyoko", "スタックちゃん、音楽で踊ってください。"),
}


async def wait_for_evidence(base_url: str, device_id: str, start_ns: int, wait_s: float) -> dict:
    deadline = time.monotonic() + wait_s
    latest: dict = {"results": []}
    while time.monotonic() < deadline:
        latest = await asyncio.to_thread(
            fetch_json, f"{base_url}/v1/devices/{device_id}/results"
        )
        current = [
            item
            for item in latest.get("results", [])
            if item.get("received_monotonic_ns", 0) >= start_ns
        ]
        music = any(
            item.get("component") == "routine_music"
            and item.get("name") == "dance"
            and item.get("frames", 0) > 0
            for item in current
        )
        motion = any(
            item.get("type") == "tool.result"
            and item.get("tool") == "play_routine"
            and item.get("routine") == "dance"
            and item.get("stage") == "completed"
            and item.get("success") is True
            and "LED frame" in str(item.get("detail", ""))
            for item in current
        )
        playback_started = any(
            item.get("component") == "playback_state" and item.get("active") is True
            for item in current
        )
        playback_drained = any(
            item.get("component") == "playback_state" and item.get("active") is False
            for item in current
        )
        if music and motion and playback_started and playback_drained:
            return latest
        await asyncio.sleep(0.1)
    return latest


async def run(base_url: str, language: str, wait_s: float) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no Stack-chan is connected")
    device_id = devices[0]
    device = fetch_json(f"{base_url}/v1/devices/{device_id}")
    before = fetch_json(f"{base_url}/v1/devices/{device_id}/results")
    before_drops = latest_drop_count(before.get("results", []))
    before_starvations = latest_starvation_count(before.get("results", []))
    offsets = trace_offsets()
    start_ns = time.perf_counter_ns()
    voice, prompt = PROMPTS[language]
    await say(voice, prompt, gain=1.5 if language == "en" else 1.0)
    after = await wait_for_evidence(base_url, device_id, start_ns, wait_s)
    current = [
        item
        for item in after.get("results", [])
        if item.get("received_monotonic_ns", 0) >= start_ns
    ]
    events = new_trace_events(offsets)
    transcripts = [
        event.get("attributes", {}).get("transcript", "")
        for event in events
        if event.get("name") == "stt"
    ]
    music = [
        item
        for item in current
        if item.get("component") == "routine_music" and item.get("name") == "dance"
    ]
    motion = [
        item
        for item in current
        if item.get("type") == "tool.result"
        and item.get("tool") == "play_routine"
        and item.get("routine") == "dance"
        and item.get("stage") == "completed"
    ]
    playback = [
        item for item in current if item.get("component") == "playback_state"
    ]
    after_drops = latest_drop_count(
        after.get("results", []), after_ns=start_ns, default=before_drops
    )
    new_drops = after_drops - before_drops if after_drops >= before_drops else after_drops
    after_starvations = latest_starvation_count(
        after.get("results", []), after_ns=start_ns, default=before_starvations
    )
    new_starvations = (
        after_starvations - before_starvations
        if after_starvations >= before_starvations
        else after_starvations
    )
    intent_recognized = any(
        "music" in transcript.casefold() and "dance" in transcript.casefold()
        for transcript in transcripts
    )
    case = {
        "language": language,
        "prompt": prompt,
        "transcripts": transcripts,
        "intent_recognized": intent_recognized,
        "music_events": music,
        "motion_completed": motion,
        "physical_playback_started": any(item.get("active") is True for item in playback),
        "physical_playback_drained": any(item.get("active") is False for item in playback),
        "newly_dropped_playback_frames": new_drops,
        "new_playback_starvation_events": new_starvations,
    }
    return {
        "device": device,
        "method": "physical acoustic voice trigger with routine music telemetry",
        "case": case,
        "passed": bool(
            transcripts
            and intent_recognized
            and music
            and all(item.get("frames", 0) > 0 for item in music)
            and any(
                item.get("success") is True
                and item.get("request_id")
                and "LED frame" in str(item.get("detail", ""))
                for item in motion
            )
            and case["physical_playback_started"]
            and case["physical_playback_drained"]
            and new_drops == 0
            and new_starvations == 0
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--language", choices=tuple(PROMPTS), default="en")
    parser.add_argument("--wait", type=float, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run(args.base_url, args.language, args.wait))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
