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
    provider: EveLLM,
    transcript: str,
    language: str,
    memories: list[str],
    action_results: list[str] | None = None,
) -> dict:
    started = time.perf_counter()
    first_token_at: float | None = None
    pieces: list[str] = []
    async with asyncio.timeout(30):
        async for piece in provider.generate(
            TurnContext(
                transcript,
                language,
                memories,
                action_results=action_results or [],
            )
        ):
            if first_token_at is None:
                first_token_at = time.perf_counter()
            pieces.append(piece)
    completed = time.perf_counter()
    return {
        "transcript": transcript,
        "language": language,
        "memories": memories,
        "action_results": action_results or [],
        "response": "".join(pieces).strip(),
        "first_token_ms": round((first_token_at - started) * 1_000, 3)
        if first_token_at is not None
        else None,
        "total_ms": round((completed - started) * 1_000, 3),
    }


def judge_memory_behavior(wake: dict, preferred_name: dict) -> dict:
    wake_ok = bool(wake["response"].strip()) and not wake["memories"] and "コーヒー" not in wake["response"]
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
    failed_action: dict,
    camera_offer: dict,
    before_consent: list[str],
    after_consent: list[str],
    outside_scope: list[str],
) -> dict:
    state_ok = bool(incomplete_state["response"].strip()) and not _physical_now_claim(
        incomplete_state["response"]
    )
    failure_reply = failed_action["response"].casefold()
    failure_ok = bool(failure_reply.strip()) and not any(
        phrase in failure_reply
        for phrase in (
            "cannot confirm",
            "can't confirm",
            "unconfirmed",
            "tool failed",
            "terminal result",
            "on the device",
        )
    )
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
                "id": "failed-action-uses-natural-recovery-language",
                "verdict": "true" if failure_ok else "false",
                "evidence": failed_action,
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
    provider = EveLLM(eve_url)
    warmup_started = time.perf_counter()
    try:
        await provider.warm_session()
        warmup_ms = round((time.perf_counter() - warmup_started) * 1_000, 3)
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
            wake = await collect_reply(provider, wake_query, "ja", wake_memories)
            preferred_name = await collect_reply(
                provider, name_query, "en", name_memories
            )
            memory.close()

        incomplete_state = await collect_reply(
            provider,
            "スタックちゃんは考えるとき、そのライトは…",
            "ja",
            [],
        )
        failed_action = await collect_reply(
            provider,
            "Please nod.",
            "en",
            [],
            ["perform_gesture failed on the device: another motion is already active"],
        )
        offer_prompt = "Offer to take one camera still, but wait for my answer."
        camera_offer = await collect_reply(provider, offer_prompt, "en", [])
    finally:
        await provider.aclose()
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
            failed_action,
            camera_offer,
            before_consent,
            after_consent,
            outside_scope,
        ),
    ]
    turns = [wake, preferred_name, incomplete_state, failed_action, camera_offer]
    first_tokens = [
        item["first_token_ms"]
        for item in turns
        if item["first_token_ms"] is not None
    ]
    return {
        "harness": "text-only",
        "session_mode": "warmed-persistent",
        "behavior_results": behaviors,
        "metrics": {
            "turns": len(turns),
            "warmup_ms": warmup_ms,
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
