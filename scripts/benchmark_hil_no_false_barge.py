#!/usr/bin/env python3
"""Prove Stack-chan does not interrupt itself during bilingual playback."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from benchmark_hil_voice import (
    CASES,
    audible_fixture_output,
    fetch_json,
    intent_recognized,
    latest_drop_count,
    latest_starvation_count,
    new_trace_events,
    say,
    trace_offsets,
    wait_for_device_idle,
    wait_for_device_playback,
)
from stackchan_agent.pipeline import meaningful_transcript


async def run_case(
    language: str, *, base_url: str, device_id: str, response_wait_s: float
) -> dict:
    case = CASES[language]
    idle_before_prompt = await wait_for_device_idle(base_url, device_id)
    offsets = trace_offsets()
    after_ns = time.perf_counter_ns()
    before = fetch_json(f"{base_url}/v1/devices/{device_id}/results")
    before_drops = latest_drop_count(before.get("results", []))
    before_starvations = latest_starvation_count(before.get("results", []))

    await say(case["voice"], case["prompt"], gain=case["prompt_gain"])
    playback_started = await wait_for_device_playback(
        base_url, device_id, after_ns, timeout_s=max(12, response_wait_s)
    )
    await asyncio.sleep(response_wait_s)

    after = fetch_json(f"{base_url}/v1/devices/{device_id}/results")
    current = [
        result
        for result in after.get("results", [])
        if result.get("received_monotonic_ns", 0) >= after_ns
    ]
    states = [
        result for result in current if result.get("component") == "playback_state"
    ]
    unexpected_ducks = [
        result
        for result in current
        if result.get("component") == "playback_duck" and result.get("enabled") is True
    ]
    events = new_trace_events(offsets)
    raw_transcripts = [
        event.get("attributes", {}).get("transcript", "")
        for event in events
        if event.get("name") == "stt"
    ]
    # Whisper can emit a traced empty/non-speech hypothesis that the runtime
    # correctly rejects. It is not a self-interruption and must not fail this
    # hardware regression; retain it separately as diagnostic evidence.
    transcripts = [item for item in raw_transcripts if meaningful_transcript(item)]
    barges = [event for event in events if event.get("name") == "barge_in"]
    llm = [event for event in events if event.get("name") == "llm"]
    tts = [event for event in events if event.get("name") == "tts"]
    current_drops = latest_drop_count(
        current, after_ns=after_ns, default=before_drops
    )
    current_starvations = latest_starvation_count(
        current, after_ns=after_ns, default=before_starvations
    )
    result = {
        "language": language,
        "idle_before_prompt": idle_before_prompt,
        "raw_transcripts": raw_transcripts,
        "transcripts": transcripts,
        "prompt_intent_recognized": bool(transcripts)
        and intent_recognized(
            transcripts[0], case["prompt_intent_terms"]
        ),
        "physical_playback_started": playback_started
        and any(state.get("active") is True for state in states),
        "physical_playback_drained": any(
            state.get("active") is False for state in states
        ),
        "unexpected_barge_ins": len(barges),
        "unexpected_playback_ducks": len(unexpected_ducks),
        "response_completed": bool(llm and tts),
        "newly_dropped_playback_frames": max(0, current_drops - before_drops),
        "new_playback_starvation_events": max(
            0, current_starvations - before_starvations
        ),
    }
    result["passed"] = bool(
        result["idle_before_prompt"]
        and len(transcripts) == 1
        and result["prompt_intent_recognized"]
        and result["physical_playback_started"]
        and result["physical_playback_drained"]
        and result["unexpected_barge_ins"] == 0
        and result["unexpected_playback_ducks"] == 0
        and result["response_completed"]
        and result["newly_dropped_playback_frames"] == 0
        and result["new_playback_starvation_events"] == 0
    )
    return result


async def benchmark(base_url: str, response_wait_s: float) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    device_id = devices[0]
    device = fetch_json(f"{base_url}/v1/devices/{device_id}")
    cases = []
    for index, language in enumerate(CASES):
        if index:
            await asyncio.sleep(2)
        cases.append(
            await run_case(
                language,
                base_url=base_url,
                device_id=device_id,
                response_wait_s=response_wait_s,
            )
        )
    return {
        "device": device,
        "method": "physical bilingual prompt and speaker-only playback safety",
        "cases": cases,
        "passed": all(case["passed"] for case in cases),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--response-wait", type=float, default=25)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with audible_fixture_output():
        result = asyncio.run(benchmark(args.base_url, args.response_wait))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
