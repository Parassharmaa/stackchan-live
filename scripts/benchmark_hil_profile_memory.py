#!/usr/bin/env python3
"""Verify automatic bilingual profile memory through physical Stack-chan audio."""

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

from benchmark_hil_memory import run_case
from benchmark_hil_voice import fetch_json
from stackchan_agent.config import Settings

CASES = {
    "en": {
        "voice": "Samantha",
        "remember": "Stack Chan, my favorite fruit is dragon fruit.",
        "question": "What is my favorite fruit?",
        "stored_contains": "favorite fruit",
        "answer_contains": "dragon fruit",
        "question_gain": 1.5,
        "between_turns_delay_s": 1.2,
    },
    "ja": {
        "voice": "Kyoko",
        "remember": "スタックちゃん、私の好きな飲み物はほうじ茶です。",
        "question": "私の好きな飲み物は何ですか。",
        "stored_contains": "好きな飲み物",
        "answer_contains": "ほうじ茶",
        "question_gain": 1.5,
        "between_turns_delay_s": 1.2,
    },
}


def cleanup(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """DELETE FROM memories
               WHERE content LIKE '%temporary profile fruit%'
                  OR content LIKE '%一時プロフィール飲み物%'
                  OR content LIKE '%favorite fruit%'
                  OR content LIKE '%好きな飲み物%'
                  OR content LIKE '%dragon fruit%'
                  OR content LIKE '%matcha%'
                  OR content LIKE '%マッチュ%'
                  OR content LIKE '%ほうじ茶%'
                  OR memory_key IN (
                      'favorite:temporary_profile_fruit',
                      'favorite:一時プロフィール飲み物',
                      'favorite:fruit',
                      'favorite:飲み物'
                  )"""
        )
        connection.commit()


async def benchmark(base_url: str, timeout_s: float) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    device = fetch_json(f"{base_url}/v1/devices/{devices[0]}")
    memory_path = Settings().memory_path
    with sqlite3.connect(memory_path) as connection:
        existing_rows = connection.execute(
            "SELECT count(*) FROM memories"
        ).fetchone()[0]
    if existing_rows:
        raise RuntimeError(
            "hil-profile-memory requires an isolated empty STACKCHAN_MEMORY_PATH"
        )
    cases: dict[str, dict] = {}
    try:
        for language, definition in CASES.items():
            case = await run_case(
                definition, memory_path, timeout_s, base_url, devices[0]
            )
            response = case.get("responses", [""])[-1].casefold()
            stored = case.get("stored_memories", [])
            correct_perspective = (
                (
                    ("your favorite" in response or "you like" in response)
                    and "i don't have a favorite" not in response
                    and "my favorite" not in response
                )
                if language == "en"
                else "私の好きな飲み物" not in response
            )
            question_transcript = " ".join(case.get("question_transcripts", []))
            question_intent = (
                "favorite" in question_transcript.casefold()
                and "fruit" in question_transcript.casefold()
                if language == "en"
                else "飲み物" in question_transcript
            )
            case["automatic_profile_kind"] = bool(
                stored and all(item.get("kind") == "profile" for item in stored)
            )
            case["correct_user_perspective"] = correct_perspective
            case["question_intent_recognized"] = question_intent
            case["passed"] = bool(
                case["passed"]
                and case["automatic_profile_kind"]
                and correct_perspective
                and question_intent
            )
            cases[language] = case
            await asyncio.sleep(1)
    finally:
        cleanup(memory_path)
    return {
        "device": device,
        "method": (
            "physical bilingual acoustic declaration and fresh-turn recall without "
            "an explicit remember command"
        ),
        "test_memories_cleaned_up": True,
        "cases": cases,
        "passed": bool(cases and all(case["passed"] for case in cases.values())),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/hil-profile-memory-latest.json"),
    )
    args = parser.parse_args()
    result = asyncio.run(benchmark(args.base_url, args.timeout))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
