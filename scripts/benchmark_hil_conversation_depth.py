#!/usr/bin/env python3
"""Verify substantive bilingual local-LLM replies through physical Stack-chan audio."""

import argparse
import asyncio
import json
import re
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

CASES = {
    "en": {
        "voice": "Samantha",
        "prompt": "Stack Chan, explain why the sky looks blue in a friendly way.",
        "prompt_terms": (("sky",), ("blue",), ("explain", "why")),
        "response_terms": (
            ("sky",),
            ("blue",),
            ("light",),
            ("scatter", "atmosphere", "air"),
        ),
        "minimum_characters": 90,
    },
    "ja": {
        "voice": "Kyoko",
        "prompt": "スタックちゃん、空が青く見える理由を分かりやすく説明してください。",
        "prompt_terms": (("空",), ("青",), ("理由", "説明")),
        "response_terms": (("空",), ("青",), ("光",), ("散乱", "大気", "空気")),
        "minimum_characters": 45,
    },
}


def semantic_groups_match(text: str, groups: tuple[tuple[str, ...], ...]) -> bool:
    normalized = text.casefold()
    return bool(normalized) and all(
        any(term.casefold() in normalized for term in alternatives)
        for alternatives in groups
    )


def sentence_count(text: str, language: str) -> int:
    terminals = r"[。！？]" if language == "ja" else r"[.!?](?:\s|$)"
    return len(re.findall(terminals, text.strip()))


def latest_audio_value(results: list[dict], key: str, default: int = 0) -> int:
    samples = [
        result
        for result in results
        if result.get("component") == "audio" and key in result
    ]
    if not samples:
        return default
    latest = max(samples, key=lambda result: result.get("received_monotonic_ns", 0))
    return int(latest.get(key, default))


async def wait_for_llm(offsets: dict[Path, int], timeout_s: float) -> list[dict]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        events = new_trace_events(offsets)
        if any(
            event.get("name") == "llm"
            and str(event.get("attributes", {}).get("response", "")).strip()
            for event in events
        ):
            return events
        await asyncio.sleep(0.2)
    return new_trace_events(offsets)


async def run_case(
    language: str,
    case: dict,
    base_url: str,
    device_id: str,
    timeout_s: float,
) -> dict:
    idle_before = await wait_for_device_idle(base_url, device_id, timeout_s=timeout_s)
    before = fetch_json(f"{base_url}/v1/devices/{device_id}/results")
    before_drops = latest_drop_count(before.get("results", []))
    before_starvation = latest_starvation_count(before.get("results", []))
    before_mouth_transitions = latest_audio_value(
        before.get("results", []), "face_speaking_mouth_transitions"
    )
    before_blinks = latest_audio_value(
        before.get("results", []), "face_speaking_blinks"
    )
    offsets = trace_offsets()
    started_ns = time.perf_counter_ns()

    await say(case["voice"], case["prompt"], gain=1.0)
    events = await wait_for_llm(offsets, timeout_s)
    playback_started = await wait_for_device_playback(
        base_url, device_id, started_ns, timeout_s
    )
    playback_drained = await wait_for_device_idle(
        base_url, device_id, stable_s=1.5, timeout_s=timeout_s
    )
    # Read once more after physical drain so TTS and device counter spans are final.
    events = new_trace_events(offsets)
    after = fetch_json(f"{base_url}/v1/devices/{device_id}/results")
    results = [
        result
        for result in after.get("results", [])
        if result.get("received_monotonic_ns", 0) >= started_ns
    ]

    transcripts = [
        str(event.get("attributes", {}).get("transcript", "")).strip()
        for event in events
        if event.get("name") == "stt"
        and str(event.get("attributes", {}).get("transcript", "")).strip()
    ]
    responses = [
        str(event.get("attributes", {}).get("response", "")).strip()
        for event in events
        if event.get("name") == "llm"
        and str(event.get("attributes", {}).get("response", "")).strip()
    ]
    llm_events = [event for event in events if event.get("name") == "llm"]
    tts_events = [event for event in events if event.get("name") == "tts"]
    transcript = transcripts[0] if transcripts else ""
    response = responses[-1] if responses else ""
    count = sentence_count(response, language)
    after_drops = latest_drop_count(results, default=before_drops)
    after_starvation = latest_starvation_count(results, default=before_starvation)
    new_drops = after_drops - before_drops if after_drops >= before_drops else after_drops
    new_starvation = (
        after_starvation - before_starvation
        if after_starvation >= before_starvation
        else after_starvation
    )
    after_mouth_transitions = latest_audio_value(
        results,
        "face_speaking_mouth_transitions",
        before_mouth_transitions,
    )
    after_blinks = latest_audio_value(
        results, "face_speaking_blinks", before_blinks
    )
    new_mouth_transitions = max(0, after_mouth_transitions - before_mouth_transitions)
    new_blinks = max(0, after_blinks - before_blinks)
    sensor_events = [
        result
        for result in results
        if result.get("component") == "sensor_head"
        and result.get("event") not in {None, ""}
    ]
    barge_events = [event for event in events if event.get("name") == "barge_in"]
    model_names = [
        str(event.get("attributes", {}).get("model", ""))
        for event in llm_events
        if event.get("attributes", {}).get("model")
    ]
    if case.get("model"):
        model_names.append(str(case["model"]))
    passed = bool(
        idle_before
        and semantic_groups_match(transcript, case["prompt_terms"])
        and len(transcripts) == 1
        and len(responses) == 1
        and len(response) >= case["minimum_characters"]
        and 2 <= count <= 4
        and semantic_groups_match(response, case["response_terms"])
        and model_names
        and playback_started
        and playback_drained
        and not sensor_events
        and not barge_events
        and new_mouth_transitions >= 4
        and new_blinks >= 1
        and new_drops == 0
        and new_starvation == 0
    )
    return {
        "prompt": case["prompt"],
        "transcripts": transcripts,
        "response": response,
        "response_characters": len(response),
        "response_sentences": count,
        "prompt_intent_verified": semantic_groups_match(
            transcript, case["prompt_terms"]
        ),
        "response_content_verified": semantic_groups_match(
            response, case["response_terms"]
        ),
        "models": model_names,
        "llm_ms": [round(event.get("duration_ms", 0), 3) for event in llm_events],
        "llm_first_token_ms": [
            event.get("attributes", {}).get("first_token_ms") for event in llm_events
        ],
        "tts_ms": [round(event.get("duration_ms", 0), 3) for event in tts_events],
        "tts_first_audio_ms": [
            event.get("attributes", {}).get("first_audio_ms") for event in tts_events
        ],
        "physical_playback_started": playback_started,
        "physical_playback_drained": playback_drained,
        "unexpected_sensor_events": sensor_events,
        "unexpected_barge_ins": len(barge_events),
        "speaking_mouth_transitions": new_mouth_transitions,
        "speaking_blinks": new_blinks,
        "newly_dropped_playback_frames": new_drops,
        "new_playback_starvation_events": new_starvation,
        "passed": passed,
    }


async def benchmark(base_url: str, timeout_s: float, language: str = "all") -> dict:
    health = fetch_json(f"{base_url}/health")
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    device_id = devices[0]
    device = fetch_json(f"{base_url}/v1/devices/{device_id}")
    cases: dict[str, dict] = {}
    selected = CASES.items() if language == "all" else ((language, CASES[language]),)
    for case_language, case in selected:
        models = health.get("models", {})
        case["model"] = str(
            models.get("conversation_runtime")
            or models.get("conversation_configured", "")
        )
        cases[case_language] = await run_case(
            case_language, case, base_url, device_id, timeout_s
        )
        await asyncio.sleep(1)
    return {
        "device": device,
        "server": health,
        "method": (
            "physical bilingual question, Eve semantic depth, and complete "
            "Stack-chan playback"
        ),
        "cases": cases,
        "passed": all(case["passed"] for case in cases.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--language", choices=("all", "en", "ja"), default="all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with audible_fixture_output():
        result = asyncio.run(benchmark(args.base_url, args.timeout, args.language))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
