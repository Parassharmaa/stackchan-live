#!/usr/bin/env python3
"""Exercise durable bilingual memory after a long Eve conversation."""

import argparse
import asyncio
import json
import statistics
import time
import uuid
from pathlib import Path

import httpx
from stackchan_agent.config import Settings
from stackchan_agent.eve_provider import EveLLM
from stackchan_agent.providers import TurnContext


async def generate(
    provider: EveLLM, context: TurnContext
) -> tuple[str, float]:
    started = time.perf_counter()
    response = "".join([piece async for piece in provider.generate(context)]).strip()
    return response, (time.perf_counter() - started) * 1000


async def benchmark(base_url: str, eve_url: str, turns: int) -> dict:
    marker = uuid.uuid4().hex[:8]
    en_fact = f"The long memory codename is amber comet {marker}."
    ja_fact = f"長期メモリの合言葉は青い月{marker}です。"
    created_ids: list[int] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=15) as client:
        health = (await client.get("/health")).raise_for_status().json()
        models = health.get("models", {})
        model = str(
            models.get("conversation_runtime")
            or models.get("conversation_configured", "")
        )
        model_verified = bool(models.get("conversation_runtime"))
        provider_name = str(models.get("conversation_provider", ""))
        if provider_name != "eve":
            raise RuntimeError("live server is not configured for Eve intelligence")
        provider = EveLLM(eve_url, core_url=base_url)
        await provider.warmup()
        try:
            stored = []
            for content, language in ((en_fact, "en"), (ja_fact, "ja")):
                response = await client.post(
                    "/v1/memories",
                    json={
                        "content": content,
                        "language": language,
                        "kind": "fact",
                        "importance": 0.8,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                memory_id = int(payload["memory"]["id"])
                created_ids.append(memory_id)
                stored.append(payload)

            duplicate = await client.post(
                "/v1/memories",
                json={
                    "content": en_fact,
                    "language": "en",
                    "kind": "fact",
                    "importance": 0.8,
                },
            )
            duplicate.raise_for_status()

            recent_turns: list[tuple[str, str]] = []
            distractor_latencies = []
            for index in range(turns):
                language = "ja" if index % 2 else "en"
                transcript = (
                    f"短く答えて。数字の{index + 1}と言って。"
                    if language == "ja"
                    else f"Answer briefly. Say the number {index + 1}."
                )
                reply, latency_ms = await generate(
                    provider,
                    TurnContext(
                        transcript,
                        language,
                        (),
                        recent_turns=tuple(recent_turns),
                    ),
                )
                recent_turns.append((transcript, reply))
                distractor_latencies.append(latency_ms)

            en_recall = await client.get(
                "/v1/memories", params={"query": "long memory codename", "limit": 6}
            )
            en_recall.raise_for_status()
            ja_recall = await client.get(
                "/v1/memories", params={"query": "長期メモリの合言葉", "limit": 6}
            )
            ja_recall.raise_for_status()
            en_memories = [
                str(item["content"]) for item in en_recall.json().get("memories", [])
            ]
            ja_memories = [
                str(item["content"]) for item in ja_recall.json().get("memories", [])
            ]
            en_reply, en_latency = await generate(
                provider,
                TurnContext(
                    "What is the long memory codename?",
                    "en",
                    en_memories,
                    recent_turns=tuple(recent_turns),
                ),
            )
            ja_reply, ja_latency = await generate(
                provider,
                TurnContext(
                    "長期メモリの合言葉は何ですか。",
                    "ja",
                    ja_memories,
                    recent_turns=tuple(recent_turns),
                ),
            )

            sensitive = await client.post(
                "/v1/memories",
                json={
                    "content": "My password is long-memory-test-placeholder",
                    "language": "en",
                },
            )
            before_delete_passed = bool(
                all(payload.get("created") is True for payload in stored)
                and duplicate.json().get("created") is False
                and any(marker in memory for memory in en_memories)
                and any(marker in memory for memory in ja_memories)
                and "amber comet" in en_reply.casefold()
                and "青い月" in ja_reply
                and sensitive.status_code == 422
            )
        finally:
            await provider.aclose()
            for memory_id in created_ids:
                await client.delete(f"/v1/memories/{memory_id}")

        after = await client.get(
            "/v1/memories", params={"query": marker, "limit": 50}
        )
        after.raise_for_status()
        cleanup_passed = not after.json().get("memories")
    return {
        "method": "live Eve conversation with loopback durable-memory API",
        "model": model,
        "model_label_verified": model_verified,
        "distractor_turns": turns,
        "stored_created": [payload.get("created") for payload in stored],
        "duplicate_created": duplicate.json().get("created"),
        "english_retrieved_count": len(en_memories),
        "japanese_retrieved_count": len(ja_memories),
        "english_reply": en_reply,
        "japanese_reply": ja_reply,
        "distractor_latency_p50_ms": round(statistics.median(distractor_latencies), 3),
        "english_recall_latency_ms": round(en_latency, 3),
        "japanese_recall_latency_ms": round(ja_latency, 3),
        "sensitive_memory_status": sensitive.status_code,
        "temporary_memories_cleaned_up": cleanup_passed,
        "passed": before_delete_passed and cleanup_passed,
    }


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--eve-url", default=settings.eve_url)
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/long-memory-latest.json"),
    )
    args = parser.parse_args()
    if args.turns < 1:
        raise SystemExit("--turns must be positive")
    result = asyncio.run(benchmark(args.base_url, args.eve_url, args.turns))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
