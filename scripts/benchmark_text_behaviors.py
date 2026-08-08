#!/usr/bin/env python3
"""Evaluate Stack-chan behavior with text turns and deterministic trajectory evidence."""

import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path
from typing import Literal

from audit_conversation_trace import _photo_offer, _physical_now_claim
from stackchan_agent.eve_provider import EveLLM
from stackchan_agent.memory import MemoryStore
from stackchan_agent.providers import TurnContext
from stackchan_agent.tools import plan_tools

Verdict = Literal["true", "false", "na"]


def fold_verdicts(verdicts: list[Verdict]) -> Verdict:
    """Fold independently judged occurrences without turning all-NA into a pass."""
    if "false" in verdicts:
        return "false"
    if "true" in verdicts:
        return "true"
    return "na"


def behavior_result(name: str, occurrences: list[dict]) -> dict:
    verdict = fold_verdicts([item["verdict"] for item in occurrences])
    return {"name": name, "verdict": verdict, "occurrences": occurrences}


async def collect_reply(
    eve_url: str, transcript: str, language: str, memories: list[str]
) -> dict:
    provider = EveLLM(eve_url)
    started = time.perf_counter()
    first_token_at: float | None = None
    pieces: list[str] = []
    try:
        async with asyncio.timeout(30):
            async for piece in provider.generate(
                TurnContext(transcript, language, memories)
            ):
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                pieces.append(piece)
    finally:
        await provider.aclose()
    completed = time.perf_counter()
    return {
        "transcript": transcript,
        "language": language,
        "memories": memories,
        "response": "".join(pieces).strip(),
        "first_token_ms": round((first_token_at - started) * 1_000, 3)
        if first_token_at is not None
        else None,
        "total_ms": round((completed - started) * 1_000, 3),
    }


def judge_memory_behavior(wake: dict, preferred_name: dict) -> dict:
    wake_ok = not wake["memories"] and "コーヒー" not in wake["response"]
    name_ok = bool(
        preferred_name["memories"]
        and "パラス" in preferred_name["memories"][0]
        and "パラス" in preferred_name["response"]
    )
    return behavior_result(
        "relevant-personal-memory",
        [
            {
                "id": "wake-name-does-not-load-unrelated-memory",
                "verdict": "true" if wake_ok else "false",
                "evidence": wake,
            },
            {
                "id": "preferred-name-cross-language-exact-recall",
                "verdict": "true" if name_ok else "false",
                "evidence": preferred_name,
            },
        ],
    )


def judge_embodied_behavior(
    incomplete_state: dict,
    camera_offer: dict,
    before_consent: list[str],
    after_consent: list[str],
    outside_scope: list[str],
) -> dict:
    state_ok = not _physical_now_claim(incomplete_state["response"])
    consent_ok = bool(
        _photo_offer(camera_offer["response"])
        and before_consent == []
        and after_consent == ["move_head", "capture_photo"]
    )
    return behavior_result(
        "grounded-embodied-actions",
        [
            {
                "id": "incomplete-state-turn-does-not-become-a-state-claim",
                "verdict": "true" if state_ok else "false",
                "evidence": incomplete_state,
            },
            {
                "id": "photo-offer-waits-then-authorizes-one-still-plan",
                "verdict": "true" if consent_ok else "false",
                "evidence": {
                    "offer": camera_offer,
                    "planned_before_consent": before_consent,
                    "planned_after_confirmation": after_consent,
                },
            },
            {
                "id": "camera-capability-statement-is-outside-consent-scope",
                "verdict": "na" if outside_scope == [] else "false",
                "reason": "trigger_not_met" if outside_scope == [] else None,
                "evidence": {
                    "previous_response": "I cannot take a photo right now.",
                    "confirmation": "Yes.",
                    "planned_tools": outside_scope,
                },
            },
            {
                "id": "physical-completion-result",
                "verdict": "na",
                "reason": "text_harness_has_no_physical_result_channel",
                "evidence": {},
            },
        ],
    )


async def benchmark(eve_url: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="stackchan-text-behaviors-") as directory:
        memory = MemoryStore(Path(directory) / "memory.sqlite3")
        memory.remember(
            "こんにちはスタックちゃん左を向いて私がコーヒーが好きだ",
            language="ja",
        )
        memory.capture_automatic_memories("パラスと呼んで。", "わかりました。", "ja")

        wake_query = "スタックちゃん。"
        wake_memories = [item.content for item in memory.retrieve(wake_query)]
        name_query = "Do you know my name?"
        name_memories = [item.content for item in memory.retrieve(name_query)]
        wake = await collect_reply(eve_url, wake_query, "ja", wake_memories)
        preferred_name = await collect_reply(
            eve_url, name_query, "en", name_memories
        )
        memory.close()

    incomplete_state = await collect_reply(
        eve_url,
        "スタックちゃんは考えるとき、そのライトは…",
        "ja",
        [],
    )
    offer_prompt = "Offer to take one camera still, but wait for my answer."
    camera_offer = await collect_reply(eve_url, offer_prompt, "en", [])
    before_consent = [item.name for item in plan_tools(offer_prompt, "en")]
    camera_context = [(offer_prompt, camera_offer["response"])]
    after_consent = [
        item.name for item in plan_tools("はい。", "ja", recent_turns=camera_context)
    ]
    outside_scope = [
        item.name
        for item in plan_tools(
            "Yes.",
            "en",
            recent_turns=[("Camera.", "I cannot take a photo right now.")],
        )
    ]

    behaviors = [
        judge_memory_behavior(wake, preferred_name),
        judge_embodied_behavior(
            incomplete_state,
            camera_offer,
            before_consent,
            after_consent,
            outside_scope,
        ),
    ]
    turns = [wake, preferred_name, incomplete_state, camera_offer]
    first_tokens = [
        item["first_token_ms"]
        for item in turns
        if item["first_token_ms"] is not None
    ]
    return {
        "harness": "text-only",
        "behavior_results": behaviors,
        "metrics": {
            "turns": len(turns),
            "first_token_max_ms": max(first_tokens) if first_tokens else None,
            "total_max_ms": max(item["total_ms"] for item in turns),
        },
        "passed": all(item["verdict"] == "true" for item in behaviors),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eve-url", default="http://127.0.0.1:2000")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(benchmark(args.eve_url.rstrip("/")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
