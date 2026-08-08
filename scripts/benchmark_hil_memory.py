#!/usr/bin/env python3
"""Verify bilingual durable-memory storage and physical spoken recall."""

import argparse
import asyncio
import json
import sqlite3
import time
from pathlib import Path

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
from stackchan_agent.config import Settings

CASES = {
    "en": {
        "voice": "Samantha",
        "remember": "Stack Chan, remember that the memory test color is lavender.",
        "question": "What is the memory test color?",
        "stored_contains": "memory test color",
        "answer_contains": "lavender",
    },
    "ja": {
        "voice": "Kyoko",
        "remember": "スタックちゃん、メモリーテストの色は紫だと覚えてください。",
        "question": "メモリーテストの色は何ですか。",
        "stored_contains": "メモリーテストの色",
        "answer_contains": "紫",
    },
}


async def wait_for_turn(offsets: dict[Path, int], timeout_s: float) -> list[dict]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        events = new_trace_events(offsets)
        if any(event.get("name") == "llm" for event in events):
            return events
        await asyncio.sleep(0.2)
    return new_trace_events(offsets)


def matching_memories(path: Path, needle: str) -> list[dict]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, content, language, kind, importance FROM memories "
            "WHERE content LIKE ? ORDER BY id",
            (f"%{needle}%",),
        ).fetchall()
    return [dict(row) for row in rows]


def cleanup_test_memories(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "DELETE FROM memories WHERE content LIKE '%memory test color%' "
            "OR content LIKE '%メモリーテストの色%'"
        )
        connection.commit()


async def run_case(
    case: dict,
    memory_path: Path,
    timeout_s: float,
    base_url: str,
    device_id: str,
) -> dict:
    before_results = fetch_json(f"{base_url}/v1/devices/{device_id}/results")
    before_drops = latest_drop_count(before_results.get("results", []))
    before_starvations = latest_starvation_count(before_results.get("results", []))
    remember_offsets = trace_offsets()
    remember_started_ns = time.perf_counter_ns()
    await say(
        case["voice"], case["remember"], gain=float(case.get("remember_gain", 1.0))
    )
    remember_events = await wait_for_turn(remember_offsets, timeout_s)
    remember_playback = await wait_for_device_playback(
        base_url, device_id, remember_started_ns, timeout_s
    )
    remember_drained = await wait_for_device_idle(base_url, device_id, timeout_s=timeout_s)
    stored = matching_memories(memory_path, case["stored_contains"])
    await asyncio.sleep(float(case.get("between_turns_delay_s", 0.0)))

    question_offsets = trace_offsets()
    question_started_ns = time.perf_counter_ns()
    await say(
        case["voice"], case["question"], gain=float(case.get("question_gain", 1.0))
    )
    question_events = await wait_for_turn(question_offsets, timeout_s)
    question_playback = await wait_for_device_playback(
        base_url, device_id, question_started_ns, timeout_s
    )
    question_drained = await wait_for_device_idle(base_url, device_id, timeout_s=timeout_s)
    after_results = fetch_json(f"{base_url}/v1/devices/{device_id}/results")
    after_drops = latest_drop_count(after_results.get("results", []), default=before_drops)
    new_drops = after_drops - before_drops if after_drops >= before_drops else after_drops
    after_starvations = latest_starvation_count(
        after_results.get("results", []), default=before_starvations
    )
    new_starvations = (
        after_starvations - before_starvations
        if after_starvations >= before_starvations
        else after_starvations
    )
    transcripts = [
        str(event.get("attributes", {}).get("transcript", ""))
        for event in question_events
        if event.get("name") == "stt"
    ]
    responses = [
        str(event.get("attributes", {}).get("response", ""))
        for event in question_events
        if event.get("name") == "llm"
    ]
    memory_counts = [
        int(event.get("attributes", {}).get("memory_count", 0))
        for event in question_events
        if event.get("name") == "llm"
    ]
    retrieved_memories = [
        list(event.get("attributes", {}).get("memories", []))
        for event in question_events
        if event.get("name") == "llm"
    ]
    return {
        "remember_transcripts": [
            str(event.get("attributes", {}).get("transcript", ""))
            for event in remember_events
            if event.get("name") == "stt"
        ],
        "stored_memories": stored,
        "question_transcripts": transcripts,
        "responses": responses,
        "retrieved_memory_counts": memory_counts,
        "retrieved_memories": retrieved_memories,
        "remember_physical_playback_started": remember_playback,
        "remember_physical_playback_drained": remember_drained,
        "question_physical_playback_started": question_playback,
        "question_physical_playback_drained": question_drained,
        "newly_dropped_playback_frames": new_drops,
        "new_playback_starvation_events": new_starvations,
        "passed": bool(
            stored
            and transcripts
            and responses
            and max(memory_counts, default=0) > 0
            and any(
                case["answer_contains"].casefold() in memory.casefold()
                for memories in retrieved_memories
                for memory in memories
            )
            and case["answer_contains"].casefold() in responses[-1].casefold()
            and remember_playback
            and remember_drained
            and question_playback
            and question_drained
            and new_drops == 0
            and new_starvations == 0
        ),
    }


async def benchmark(base_url: str, timeout_s: float) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    device = fetch_json(f"{base_url}/v1/devices/{devices[0]}")
    memory_path = Settings().memory_path
    cleanup_test_memories(memory_path)
    cases: dict[str, dict] = {}
    try:
        for language, case in CASES.items():
            cases[language] = await run_case(
                case, memory_path, timeout_s, base_url, devices[0]
            )
            await asyncio.sleep(1)
    finally:
        cleanup_test_memories(memory_path)
    return {
        "device": device,
        "method": "physical acoustic remember and recall with temporary test facts",
        "test_memories_cleaned_up": True,
        "cases": cases,
        "passed": bool(cases and all(case["passed"] for case in cases.values())),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(benchmark(args.base_url, args.timeout))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
