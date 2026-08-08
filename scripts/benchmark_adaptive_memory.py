#!/usr/bin/env python3
"""Benchmark automatic profile and episodic recall through live Eve sessions."""

import argparse
import asyncio
import json
import tempfile
import time
from pathlib import Path

from stackchan_agent.config import Settings
from stackchan_agent.eve_provider import EveLLM
from stackchan_agent.local_providers import detect_language
from stackchan_agent.memory import MemoryStore
from stackchan_agent.pipeline import CascadePipeline
from stackchan_agent.providers import MockSTT, MockTTS, TurnContext
from stackchan_agent.telemetry import TraceRecorder


async def run_turn(
    transcript: str,
    memory: MemoryStore,
    trace_dir: Path,
    eve_url: str,
) -> dict:
    pipeline = CascadePipeline(
        MockSTT(transcript),
        EveLLM(eve_url),
        MockTTS(),
        memory,
        TraceRecorder(trace_dir),
    )
    started = time.perf_counter()
    first_delta_ms: float | None = None
    response = ""
    try:
        async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000):
            if not event.control:
                continue
            if event.control.type == "response.text.delta" and first_delta_ms is None:
                first_delta_ms = (time.perf_counter() - started) * 1_000
            if event.control.type == "response.text.done":
                response = str(event.control.payload.get("text", ""))
    finally:
        await pipeline.aclose()
    return {
        "transcript": transcript,
        "response": response,
        "first_delta_ms": round(first_delta_ms, 3) if first_delta_ms else None,
        "duration_ms": round((time.perf_counter() - started) * 1_000, 3),
    }


async def benchmark(eve_url: str) -> dict:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="stackchan-adaptive-memory-") as directory:
        root = Path(directory)
        memory = MemoryStore(
            root / "memory.sqlite3", episode_retention_days=30, episode_limit=50
        )
        turns: list[dict] = []

        turns.append(
            await run_turn(
                "My favorite benchmark fruit is dragonfruit.",
                memory,
                root / "traces",
                eve_url,
            )
        )
        profiles = memory.list_recent()
        profile_stored = bool(
            len(profiles) == 1
            and profiles[0].kind == "profile"
            and profiles[0].memory_key == "favorite:benchmark_fruit"
            and "dragonfruit" in profiles[0].content.casefold()
        )

        profile_query = "What is my favorite benchmark fruit?"
        profile_context = [item.content for item in memory.retrieve(profile_query)]
        turns.append(
            await run_turn(profile_query, memory, root / "traces", eve_url)
        )
        profile_recalled = bool(
            any("dragonfruit" in item.casefold() for item in profile_context)
            and "dragonfruit" in turns[-1]["response"].casefold()
            and "your favorite benchmark fruit" in turns[-1]["response"].casefold()
            and "my favorite benchmark fruit" not in turns[-1]["response"].casefold()
        )

        discussion = "Explain why rainy days can make a quiet room feel cozy."
        turns.append(await run_turn(discussion, memory, root / "traces", eve_url))
        episodes = memory.list_recent(include_episodes=True)
        episodes = [item for item in episodes if item.kind == "episode"]
        episode_stored = bool(
            episodes
            and "rainy days" in episodes[0].content.casefold()
            and episodes[0].expires_at is not None
        )

        episode_query = "What did we talk about last time?"
        episode_context = [item.content for item in memory.retrieve(episode_query)]
        turns.append(
            await run_turn(episode_query, memory, root / "traces", eve_url)
        )
        episode_recalled = bool(
            episode_context
            and "rainy days" in episode_context[0].casefold()
            and any(
                cue in turns[-1]["response"].casefold()
                for cue in ("rain", "cozy", "quiet room")
            )
        )

        turns.append(
            await run_turn(
                "私の好きなベンチマーク飲み物はほうじ茶です。",
                memory,
                root / "traces",
                eve_url,
            )
        )
        japanese_profile_query = "私の好きなベンチマーク飲み物は何ですか？"
        japanese_profile_context = [
            item.content for item in memory.retrieve(japanese_profile_query)
        ]
        turns.append(
            await run_turn(
                japanese_profile_query, memory, root / "traces", eve_url
            )
        )
        japanese_profile_recalled = bool(
            any("ほうじ茶" in item for item in japanese_profile_context)
            and "ほうじ茶" in turns[-1]["response"]
            and "私の好きな" not in turns[-1]["response"]
        )

        japanese_discussion = "雨の日に静かな部屋が心地よく感じる理由を説明して。"
        turns.append(
            await run_turn(
                japanese_discussion, memory, root / "traces", eve_url
            )
        )
        japanese_episode_query = "前回は何を話しましたか？"
        japanese_episode_context = [
            item.content for item in memory.retrieve(japanese_episode_query)
        ]
        turns.append(
            await run_turn(
                japanese_episode_query, memory, root / "traces", eve_url
            )
        )
        japanese_episode_recalled = bool(
            japanese_episode_context
            and "雨の日" in japanese_episode_context[0]
            and "雨" in turns[-1]["response"]
        )

        before_sensitive = len(memory.list_recent(include_episodes=True))
        sensitive_capture = memory.capture_automatic_memories(
            "I take insulin every day.",
            "That medical detail deserves careful handling.",
            "en",
        )
        sensitive_rejected = bool(
            not sensitive_capture
            and len(memory.list_recent(include_episodes=True)) == before_sensitive
        )
        no_unconfirmed_memory_claim = "remember" not in turns[0]["response"].casefold()
        no_unconfirmed_japanese_memory_claim = "覚え" not in turns[4]["response"]
        memory.close()

    switch_provider = EveLLM(eve_url)
    switch_responses: list[str] = []
    try:
        for context in (
            ("Say one short sentence about tea.", "en"),
            ("お茶について短い一文を言ってください。", "ja"),
        ):
            text = "".join(
                [
                    piece
                    async for piece in switch_provider.generate(
                        TurnContext(context[0], context[1], [])
                    )
                ]
            )
            switch_responses.append(text)
    finally:
        await switch_provider.aclose()
    same_session_language_switch = bool(
        len(switch_responses) == 2
        and detect_language(switch_responses[0]) == "en"
        and detect_language(switch_responses[1]) == "ja"
    )

    checks = {
        "profile_stored": profile_stored,
        "profile_recalled_in_fresh_eve_session": profile_recalled,
        "episode_stored_with_expiry": episode_stored,
        "episode_recalled_in_fresh_eve_session": episode_recalled,
        "japanese_profile_recalled_in_fresh_eve_session": japanese_profile_recalled,
        "japanese_episode_recalled_in_fresh_eve_session": japanese_episode_recalled,
        "sensitive_automatic_memory_rejected": sensitive_rejected,
        "no_unconfirmed_memory_claim": no_unconfirmed_memory_claim,
        "no_unconfirmed_japanese_memory_claim": no_unconfirmed_japanese_memory_claim,
        "same_eve_session_switches_english_to_japanese": same_session_language_switch,
        "all_turns_returned_text": all(turn["response"].strip() for turn in turns),
    }
    return {
        "method": (
            "temporary SQLite store, production CascadePipeline, and a fresh live "
            "Eve session for every turn"
        ),
        "eve_url": eve_url,
        "turns": turns,
        "profile_context": profile_context,
        "episode_context": episode_context,
        "japanese_profile_context": japanese_profile_context,
        "japanese_episode_context": japanese_episode_context,
        "same_session_language_switch_responses": switch_responses,
        "checks": checks,
        "duration_ms": round((time.perf_counter() - started) * 1_000, 3),
        "temporary_data_cleaned": True,
        "passed": all(checks.values()),
    }


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--eve-url", default=settings.eve_url)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/adaptive-memory-latest.json"),
    )
    args = parser.parse_args()
    result = asyncio.run(benchmark(args.eve_url))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
