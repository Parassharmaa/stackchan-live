#!/usr/bin/env python3
"""Long, capability-wide hardware acceptance suite for custom Stack-chan."""

import argparse
import asyncio
import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from benchmark_hil_memory import benchmark as benchmark_memory
from benchmark_hil_sensor import benchmark as benchmark_sensor
from benchmark_hil_voice import (
    fetch_json,
    latest_drop_count,
    latest_starvation_count,
    new_trace_events,
    say,
    trace_offsets,
    wait_for_device_idle,
    wait_for_device_playback,
)
from benchmark_hil_voice import (
    run_case as run_barge_case,
)
from soak_hil_voice import soak as voice_soak
from stackchan_agent.config import Settings

FACE_CASES = (
    {"state": "idle", "emotion": "neutral", "intensity": 0.5},
    {"state": "happy", "emotion": "joy", "intensity": 1.0},
    {"state": "thinking", "emotion": "curious", "intensity": 0.8},
    {"state": "happy", "emotion": "gentle", "intensity": 0.65},
    {"state": "happy", "emotion": "playful", "intensity": 1.0},
)

LIGHT_CASES = (
    {"red": 255, "green": 30, "blue": 20, "brightness": 0.2, "animation": "solid"},
    {"red": 20, "green": 255, "blue": 60, "brightness": 0.22, "animation": "pulse"},
    {"red": 30, "green": 90, "blue": 255, "brightness": 0.24, "animation": "chase"},
    {"red": 255, "green": 50, "blue": 150, "brightness": 0.26, "animation": "twinkle"},
    {"red": 160, "green": 50, "blue": 255, "brightness": 0.28, "animation": "rainbow"},
)

MOTION_CASES = (
    {"name": "left", "yaw_deg": -30, "pitch_deg": 45, "duration_ms": 650},
    {"name": "right", "yaw_deg": 30, "pitch_deg": 45, "duration_ms": 650},
    {"name": "up", "yaw_deg": 0, "pitch_deg": 20, "duration_ms": 650},
    {"name": "down", "yaw_deg": 0, "pitch_deg": 70, "duration_ms": 650},
    {"name": "center", "yaw_deg": 0, "pitch_deg": 45, "duration_ms": 650},
)

ROUTINE_STEPS = {
    "greet": 3,
    "celebrate": 3,
    "curious": 4,
    "comfort": 4,
    "dance": 5,
}

VOICE_ACTIONS = (
    {
        "name": "english_head_towards_left_recent_regression",
        "voice": "Samantha",
        "prompt": "Head towards left.",
        "tool": "move_head",
        "intent_terms": ("left",),
        "expected_yaw_deg": -24.0,
        "expected_pitch_deg": 45.0,
    },
    {
        "name": "english_head_left",
        "voice": "Samantha",
        "prompt": "Please turn your head to the left side.",
        "tool": "move_head",
        "intent_terms": ("left",),
        "expected_yaw_deg": -24.0,
        "expected_pitch_deg": 45.0,
    },
    {
        "name": "english_head_right",
        "voice": "Samantha",
        "prompt": "Turn your head to the right side.",
        "tool": "move_head",
        "intent_terms": ("right",),
        "expected_yaw_deg": 24.0,
        "expected_pitch_deg": 45.0,
    },
    {
        "name": "japanese_head_up",
        "voice": "Kyoko",
        "prompt": "スタックちゃん、上を向いてください。",
        "tool": "move_head",
        "intent_terms": ("上",),
        "expected_yaw_deg": 0.0,
        "expected_pitch_deg": 25.0,
    },
    {
        "name": "japanese_head_center",
        "voice": "Kyoko",
        "prompt": "スタックちゃん、正面を向いてください。",
        "tool": "move_head",
        "intent_terms": ("正面",),
        "expected_yaw_deg": 0.0,
        "expected_pitch_deg": 45.0,
    },
    {
        "name": "english_blue_lights",
        "voice": "Samantha",
        "prompt": "Stack Chan, make the lights blue.",
        "tool": "set_lights",
    },
    {
        "name": "english_blink_lights_recent_regression",
        "voice": "Samantha",
        "prompt": "Can you actually blink your lights very fast?",
        "tool": "set_lights",
    },
    {
        "name": "japanese_pink_lights",
        "voice": "Kyoko",
        "prompt": "スタックちゃん、ライトをピンクにしてください。",
        "tool": "set_lights",
    },
    {
        "name": "english_dance_music",
        "voice": "Samantha",
        "prompt": "Stack Chan, dance with music.",
        "tool": "play_routine",
        "routine": "dance",
        "music": True,
    },
)

CONVERSATIONS = (
    ("en", "Samantha", "Stack Chan, tell me one cheerful sentence."),
    ("ja", "Kyoko", "スタックちゃん、短く元気な一言を言ってください。"),
)


def announce(message: str) -> None:
    print(f"CAPABILITY_PHASE {message}", flush=True)


def post_control(base_url: str, device_id: str, message_type: str, payload: dict) -> dict:
    body = json.dumps({"type": message_type, "payload": payload}).encode()
    request = urllib.request.Request(
        f"{base_url}/v1/devices/{urllib.parse.quote(device_id, safe='')}/control",
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.load(response)


def device_results(base_url: str, device_id: str) -> list[dict]:
    encoded = urllib.parse.quote(device_id, safe="")
    return fetch_json(f"{base_url}/v1/devices/{encoded}/results").get("results", [])


async def wait_for_result(
    base_url: str,
    device_id: str,
    after_ns: int,
    predicate: Callable[[dict], bool],
    timeout_s: float,
) -> tuple[dict | None, list[dict]]:
    deadline = time.monotonic() + timeout_s
    current: list[dict] = []
    while time.monotonic() < deadline:
        current = [
            item
            for item in await asyncio.to_thread(device_results, base_url, device_id)
            if item.get("received_monotonic_ns", 0) >= after_ns
        ]
        match = next((item for item in reversed(current) if predicate(item)), None)
        if match is not None:
            return match, current
        await asyncio.sleep(0.1)
    return None, current


def values_match(result: dict, expected: dict[str, Any]) -> bool:
    for key, value in expected.items():
        actual = result.get(key)
        if isinstance(value, float):
            if actual is None or abs(float(actual) - value) > 0.011:
                return False
        elif actual != value:
            return False
    return True


async def run_direct_faces(base_url: str, device_id: str) -> dict:
    cases = []
    for payload in FACE_CASES:
        started_ns = time.perf_counter_ns()
        dispatched = await asyncio.to_thread(
            post_control, base_url, device_id, "face.set", payload
        )
        result, _ = await wait_for_result(
            base_url,
            device_id,
            started_ns,
            lambda item: item.get("tool") == "set_face"
            and item.get("stage") == "completed",
            3,
        )
        passed = bool(
            dispatched.get("status") == "dispatched"
            and result
            and result.get("success") is True
            and values_match(result, payload)
        )
        cases.append({"requested": payload, "result": result, "passed": passed})
        await asyncio.sleep(0.15)
    return {"cases": cases, "passed": all(case["passed"] for case in cases)}


async def run_direct_lights(base_url: str, device_id: str) -> dict:
    cases = []
    for payload in LIGHT_CASES:
        started_ns = time.perf_counter_ns()
        dispatched = await asyncio.to_thread(
            post_control, base_url, device_id, "lights.set", payload
        )
        result, _ = await wait_for_result(
            base_url,
            device_id,
            started_ns,
            lambda item: item.get("tool") == "set_lights"
            and item.get("stage") == "completed",
            3,
        )
        passed = bool(
            dispatched.get("status") == "dispatched"
            and result
            and result.get("success") is True
            and values_match(result, payload)
        )
        cases.append({"requested": payload, "result": result, "passed": passed})
        await asyncio.sleep(0.25)
    return {"cases": cases, "passed": all(case["passed"] for case in cases)}


async def run_direct_motion(base_url: str, device_id: str) -> dict:
    cases = []
    for payload in MOTION_CASES:
        started_ns = time.perf_counter_ns()
        request_payload = {key: value for key, value in payload.items() if key != "name"}
        dispatched = await asyncio.to_thread(
            post_control, base_url, device_id, "motion.set", request_payload
        )
        result, current = await wait_for_result(
            base_url,
            device_id,
            started_ns,
            lambda item: item.get("tool") == "move_head"
            and item.get("stage") == "completed",
            5,
        )
        passed = bool(
            dispatched.get("status") == "dispatched"
            and result
            and result.get("success") is True
            and result.get("yaw_error_raw", 999) <= 24
            and result.get("pitch_error_raw", 999) <= 24
        )
        cases.append(
            {
                "requested": payload,
                "events": [item for item in current if item.get("tool") == "move_head"],
                "passed": passed,
            }
        )
        await asyncio.sleep(0.2)
    return {"cases": cases, "passed": all(case["passed"] for case in cases)}


async def run_direct_routines(base_url: str, device_id: str) -> dict:
    cases = []
    for routine, expected_steps in ROUTINE_STEPS.items():
        started_ns = time.perf_counter_ns()
        dispatched = await asyncio.to_thread(
            post_control,
            base_url,
            device_id,
            "routine.play",
            {"name": routine, "intensity": 0.8, "music": routine == "dance"},
        )
        result, current = await wait_for_result(
            base_url,
            device_id,
            started_ns,
            lambda item, routine=routine: item.get("tool") == "play_routine"
            and item.get("routine") == routine
            and item.get("stage") == "completed",
            12,
        )
        events = [
            item
            for item in current
            if item.get("tool") == "play_routine" and item.get("routine") == routine
        ]
        steps = [item for item in events if item.get("stage") == "step_completed"]
        distinct_targets = {
            (item.get("yaw_target_raw"), item.get("pitch_target_raw")) for item in steps
        }
        passed = bool(
            dispatched.get("status") == "dispatched"
            and result
            and result.get("success") is True
            and len(steps) == expected_steps
            and all(item.get("success") is True for item in steps)
            and len(distinct_targets) >= min(3, expected_steps)
        )
        cases.append(
            {
                "routine": routine,
                "expected_steps": expected_steps,
                "events": events,
                "passed": passed,
            }
        )
        await asyncio.sleep(0.3)
    return {"cases": cases, "passed": all(case["passed"] for case in cases)}


async def wait_for_voice_action(
    base_url: str,
    device_id: str,
    started_ns: int,
    offsets: dict[Path, int],
    expected_tool: str,
    expected_routine: str | None,
    timeout_s: float,
) -> tuple[list[dict], list[dict]]:
    deadline = time.monotonic() + timeout_s
    current: list[dict] = []
    events: list[dict] = []
    while time.monotonic() < deadline:
        current = [
            item
            for item in await asyncio.to_thread(device_results, base_url, device_id)
            if item.get("received_monotonic_ns", 0) >= started_ns
        ]
        events = new_trace_events(offsets)
        tool_done = any(
            item.get("tool") == expected_tool
            and item.get("stage") == "completed"
            and item.get("success") is True
            and (expected_routine is None or item.get("routine") == expected_routine)
            for item in current
        )
        response_done = any(event.get("name") == "llm" for event in events) and any(
            event.get("name") == "tts" for event in events
        )
        if tool_done and response_done:
            await wait_for_device_idle(base_url, device_id, stable_s=0.6, timeout_s=8)
            return current, new_trace_events(offsets)
        await asyncio.sleep(0.15)
    return current, events


async def run_voice_actions(
    base_url: str, device_id: str, action_cases: tuple[dict, ...] = VOICE_ACTIONS
) -> dict:
    cases = []
    for case in action_cases:
        await wait_for_device_idle(base_url, device_id, stable_s=0.6, timeout_s=10)
        before = await asyncio.to_thread(device_results, base_url, device_id)
        before_drops = latest_drop_count(before)
        before_starvations = latest_starvation_count(before)
        offsets = trace_offsets()
        started_ns = time.perf_counter_ns()
        await say(case["voice"], case["prompt"])
        current, events = await wait_for_voice_action(
            base_url,
            device_id,
            started_ns,
            offsets,
            case["tool"],
            case.get("routine"),
            25,
        )
        transcripts = [
            event.get("attributes", {}).get("transcript", "")
            for event in events
            if event.get("name") == "stt"
        ]
        meaningful_transcripts = [
            transcript
            for transcript in transcripts
            if transcript.strip()
            and not (
                transcript.strip()[0] in "([*"
                and transcript.strip()[-1] in ")]*"
            )
        ]
        unexpected_transcripts = meaningful_transcripts[1:]
        unexpected_sensor_events = [
            item for item in current if item.get("component") == "sensor_head"
        ]
        llm_events = [event for event in events if event.get("name") == "llm"]
        action_memory_isolated = bool(llm_events) and all(
            event.get("attributes", {}).get("memory_count") == 0
            for event in llm_events
        )
        unexpected_model_turns = [
            event for event in events if event.get("name") == "sensor_llm"
        ]
        if len(llm_events) > 1:
            unexpected_model_turns.extend(llm_events[1:])
        tool_results = [item for item in current if item.get("tool") == case["tool"]]
        drops = latest_drop_count(current, default=before_drops)
        new_drops = drops - before_drops if drops >= before_drops else drops
        starvations = latest_starvation_count(current, default=before_starvations)
        new_starvations = (
            starvations - before_starvations
            if starvations >= before_starvations
            else starvations
        )
        music_ok = not case.get("music") or any(
            item.get("component") == "routine_music" for item in current
        )
        intent_ok = all(
            any(term.casefold() in transcript.casefold() for transcript in transcripts)
            for term in case.get("intent_terms", ())
        )
        motion_request_ok = case["tool"] != "move_head" or any(
            item.get("stage") == "dispatched"
            and item.get("yaw_deg") == case.get("expected_yaw_deg")
            and item.get("pitch_deg") == case.get("expected_pitch_deg")
            for item in tool_results
        )
        motion_feedback_ok = case["tool"] != "move_head" or any(
            item.get("stage") == "completed"
            and item.get("success") is True
            and item.get("yaw_error_raw", 999) <= 24
            and item.get("pitch_error_raw", 999) <= 24
            for item in tool_results
        )
        passed = bool(
            transcripts
            and intent_ok
            and motion_request_ok
            and motion_feedback_ok
            and any(
                item.get("stage") == "completed" and item.get("success") is True
                for item in tool_results
            )
            and music_ok
            and not unexpected_transcripts
            and not unexpected_sensor_events
            and not unexpected_model_turns
            and action_memory_isolated
            and new_drops == 0
            and new_starvations == 0
        )
        cases.append(
            {
                **case,
                "transcripts": transcripts,
                "unexpected_transcripts": unexpected_transcripts,
                "unexpected_sensor_events": unexpected_sensor_events,
                "unexpected_model_turn_count": len(unexpected_model_turns),
                "llm_responses": [
                    event.get("attributes", {}).get("response") for event in llm_events
                ],
                "llm_memory_counts": [
                    event.get("attributes", {}).get("memory_count") for event in llm_events
                ],
                "action_memory_isolated": action_memory_isolated,
                "tool_results": tool_results,
                "intent_verified": intent_ok,
                "motion_request_verified": motion_request_ok,
                "motion_feedback_verified": motion_feedback_ok,
                "music_verified": music_ok,
                "newly_dropped_playback_frames": new_drops,
                "new_playback_starvation_events": new_starvations,
                "passed": passed,
            }
        )
        await asyncio.sleep(0.8)
    return {"cases": cases, "passed": all(case["passed"] for case in cases)}


async def run_conversations(base_url: str, device_id: str) -> dict:
    cases = []
    for language, voice, prompt in CONVERSATIONS:
        await wait_for_device_idle(base_url, device_id, stable_s=0.6, timeout_s=10)
        before = await asyncio.to_thread(device_results, base_url, device_id)
        before_drops = latest_drop_count(before)
        before_starvations = latest_starvation_count(before)
        offsets = trace_offsets()
        started_ns = time.perf_counter_ns()
        await say(voice, prompt)
        physical_playback_started = await wait_for_device_playback(
            base_url, device_id, started_ns, 20
        )
        deadline = time.monotonic() + 20
        events: list[dict] = []
        while time.monotonic() < deadline:
            events = new_trace_events(offsets)
            if any(event.get("name") == "llm" for event in events) and any(
                event.get("name") == "tts" for event in events
            ):
                break
            await asyncio.sleep(0.15)
        stt = [event for event in events if event.get("name") == "stt"]
        llm = [event for event in events if event.get("name") == "llm"]
        tts = [event for event in events if event.get("name") == "tts"]
        first_audio = tts[-1].get("attributes", {}).get("first_audio_ms") if tts else None
        semantic_audio = (
            tts[-1].get("attributes", {}).get("semantic_first_audio_ms") if tts else None
        )
        no_spoken_backchannel = bool(
            first_audio is not None
            and semantic_audio is not None
            and abs(first_audio - semantic_audio) < 5
        )
        physical_playback_drained = await wait_for_device_idle(
            base_url, device_id, stable_s=0.6, timeout_s=10
        )
        after = await asyncio.to_thread(device_results, base_url, device_id)
        after_drops = latest_drop_count(after, default=before_drops)
        new_drops = (
            after_drops - before_drops if after_drops >= before_drops else after_drops
        )
        after_starvations = latest_starvation_count(after, default=before_starvations)
        new_starvations = (
            after_starvations - before_starvations
            if after_starvations >= before_starvations
            else after_starvations
        )
        cases.append(
            {
                "language": language,
                "prompt": prompt,
                "transcripts": [event.get("attributes", {}).get("transcript") for event in stt],
                "responses": [event.get("attributes", {}).get("response") for event in llm],
                "tts_first_audio_ms": first_audio,
                "semantic_first_audio_ms": semantic_audio,
                "no_spoken_backchannel": no_spoken_backchannel,
                "physical_playback_started": physical_playback_started,
                "physical_playback_drained": physical_playback_drained,
                "newly_dropped_playback_frames": new_drops,
                "new_playback_starvation_events": new_starvations,
                "passed": bool(
                    stt
                    and llm
                    and tts
                    and no_spoken_backchannel
                    and physical_playback_started
                    and physical_playback_drained
                    and new_drops == 0
                    and new_starvations == 0
                ),
            }
        )
        await asyncio.sleep(0.6)
    return {"cases": cases, "passed": all(case["passed"] for case in cases)}


async def run_suite(args: argparse.Namespace) -> dict:
    started_monotonic = time.monotonic()
    devices = fetch_json(f"{args.base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    device_id = devices[0]
    encoded_id = urllib.parse.quote(device_id, safe="")
    initial_device = fetch_json(f"{args.base_url}/v1/devices/{encoded_id}")
    initial_results = device_results(args.base_url, device_id)
    initial_drops = latest_drop_count(initial_results)
    initial_starvations = latest_starvation_count(initial_results)

    report: dict[str, Any] = {
        "method": "long capability-wide physical Stack-chan acceptance suite",
        "initial_device": initial_device,
        "categories": {},
    }
    announce("direct faces")
    report["categories"]["direct_faces"] = await run_direct_faces(
        args.base_url, device_id
    )
    announce("direct lights")
    report["categories"]["direct_lights"] = await run_direct_lights(
        args.base_url, device_id
    )
    announce("direct head poses")
    report["categories"]["direct_motion"] = await run_direct_motion(
        args.base_url, device_id
    )
    announce("all five coordinated routines")
    report["categories"]["direct_routines"] = await run_direct_routines(
        args.base_url, device_id
    )
    announce("voice to head, lights, routine, and music")
    report["categories"]["voice_actions"] = await run_voice_actions(
        args.base_url, device_id
    )
    announce("ordinary bilingual conversation without spoken backchannels")
    report["categories"]["conversations"] = await run_conversations(
        args.base_url, device_id
    )
    announce("English and Japanese barge-in")
    barge_cases = [
        await run_barge_case(language, args.response_wait, args.base_url, device_id)
        for language in ("en", "ja")
    ]
    report["categories"]["bilingual_barge_in"] = {
        "cases": barge_cases,
        "passed": all(
            case.get("observed_two_turns")
            and case.get("observed_barge_in")
            and case.get("interrupt_intent_recognized")
            and case.get("response_completed")
            and not case.get("unexpected_barge_ins")
            and case.get("newly_dropped_playback_frames") == 0
            and case.get("new_playback_starvation_events") == 0
            for case in barge_cases
        ),
    }
    announce("durable bilingual memory")
    report["categories"]["durable_memory"] = await benchmark_memory(
        args.base_url, args.turn_timeout
    )
    announce("physical top sensor - pet or wave over Stack-chan now")
    report["categories"]["head_sensor"] = await benchmark_sensor(
        args.base_url, args.sensor_timeout
    )
    if args.soak_duration > 0:
        announce(f"alternating bilingual interruption soak for {args.soak_duration:g}s")
        report["categories"]["voice_soak"] = await voice_soak(
            args.base_url,
            args.soak_duration,
            args.response_wait,
            args.soak_interval,
        )

    final_devices = fetch_json(f"{args.base_url}/v1/devices").get("devices", [])
    final_device = (
        fetch_json(f"{args.base_url}/v1/devices/{encoded_id}")
        if device_id in final_devices
        else {}
    )
    final_results = device_results(args.base_url, device_id) if final_device else []
    final_drops = latest_drop_count(final_results, default=initial_drops)
    final_starvations = latest_starvation_count(
        final_results, default=initial_starvations
    )
    report.update(
        {
            "final_device": final_device,
            "actual_duration_s": round(time.monotonic() - started_monotonic, 3),
            "boot_stable": final_device.get("boot_count")
            == initial_device.get("boot_count"),
            "device_remained_connected": bool(final_device),
            "initial_playback_dropped_frames": initial_drops,
            "final_playback_dropped_frames": final_drops,
            "new_playback_dropped_frames": max(0, final_drops - initial_drops),
            "initial_playback_starvation_events": initial_starvations,
            "final_playback_starvation_events": final_starvations,
            "new_playback_starvation_events": max(
                0, final_starvations - initial_starvations
            ),
            "provider_inventory": {
                "live_provider": Settings().provider,
                "cascade_local": True,
                "speech_to_speech_configured": Settings().openai_api_key is not None,
                "speech_to_speech_live_tested": False,
            },
        }
    )
    required = list(report["categories"].values())
    report["passed"] = bool(
        required
        and all(category.get("passed") is True for category in required)
        and report["boot_stable"]
        and report["device_remained_connected"]
        and report["new_playback_dropped_frames"] == 0
        and report["new_playback_starvation_events"] == 0
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--response-wait", type=float, default=9)
    parser.add_argument("--turn-timeout", type=float, default=18)
    parser.add_argument("--sensor-timeout", type=float, default=30)
    parser.add_argument("--soak-duration", type=float, default=120)
    parser.add_argument("--soak-interval", type=float, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/hil-capabilities-latest.json"),
    )
    args = parser.parse_args()
    if min(
        args.response_wait,
        args.turn_timeout,
        args.sensor_timeout,
        args.soak_duration,
        args.soak_interval,
    ) < 0:
        raise SystemExit("timeouts and durations must be non-negative")
    report = asyncio.run(run_suite(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(args.output, flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
