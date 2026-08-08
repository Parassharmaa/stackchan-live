#!/usr/bin/env python3
"""Benchmark the live Eve intelligence sidecar through the Python adapter."""

import argparse
import asyncio
import json
import re
import statistics
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from stackchan_agent.eve_provider import EveLLM, visual_only_glyph
from stackchan_agent.providers import TurnContext

BENCHMARK_FAILURES = (RuntimeError, TimeoutError, httpx.HTTPError, ValueError, KeyError)


@dataclass(frozen=True)
class Scenario:
    name: str
    context: TurnContext
    minimum_chars: int
    required_any: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()


SCENARIOS = (
    Scenario(
        "common_sense_en",
        TurnContext("Why do cats often sit inside boxes?", "en", []),
        100,
        ("safe", "secure", "cozy", "comfort", "warm", "enclosed", "hide"),
        ("quantum", "rarely sit", "don't sit"),
    ),
    Scenario(
        "explanation_depth_en",
        TurnContext(
            "Explain one benefit and one tradeoff of running an AI model locally.",
            "en",
            [],
        ),
        140,
        ("privacy", "private", "latency", "offline", "control"),
    ),
    Scenario(
        "explanation_depth_ja",
        TurnContext(
            "AIモデルをローカルで動かす利点と欠点を一つずつ説明して。",
            "ja",
            [],
        ),
        45,
        ("利点", "メリット", "プライバシー", "遅延", "オフライン"),
    ),
    Scenario(
        "retrieved_memory_en",
        TurnContext(
            "What color did I ask you to remember?",
            "en",
            ["My remembered color is lavender"],
        ),
        25,
        ("lavender",),
    ),
)


def has_visual_only_glyph(text: str) -> bool:
    return any(visual_only_glyph(character) for character in text)


def evaluate(scenario: Scenario, reply: str) -> tuple[bool, list[str]]:
    folded = reply.casefold()
    reasons = []
    if len(reply.strip()) < scenario.minimum_chars:
        reasons.append("too_short")
    if scenario.required_any and not any(term.casefold() in folded for term in scenario.required_any):
        reasons.append("missing_required_concept")
    if any(term.casefold() in folded for term in scenario.forbidden):
        reasons.append("forbidden_claim")
    japanese = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", reply))
    if scenario.context.language == "ja" and not japanese:
        reasons.append("language_drift")
    if scenario.context.language == "en" and japanese:
        reasons.append("language_drift")
    if has_visual_only_glyph(reply) or any(marker in reply for marker in ("**", "#", "```")):
        reasons.append("not_spoken_text")
    if folded.lstrip().startswith(("okay", "sure", "ah,", "that's a fun question")):
        reasons.append("generic_acknowledgement")
    return not reasons, reasons


async def collect_reply(
    provider: EveLLM, context: TurnContext, *, timeout_seconds: float = 30.0
) -> tuple[str, float | None, float]:
    started = time.perf_counter()
    first_token = None
    pieces = []
    async with asyncio.timeout(timeout_seconds):
        async for piece in provider.generate(context):
            first_token = first_token or time.perf_counter()
            pieces.append(piece)
    completed = time.perf_counter()
    return (
        "".join(pieces).strip(),
        (first_token - started) * 1_000 if first_token else None,
        (completed - started) * 1_000,
    )


async def check_multi_turn(base_url: str) -> dict:
    provider = EveLLM(base_url)
    try:
        _ = await collect_reply(
            provider,
            TurnContext(
                "For this conversation only, the temporary nickname is Firefly. "
                "Acknowledge it briefly.",
                "en",
                [],
            ),
        )
        reply, first_token_ms, total_ms = await collect_reply(
            provider,
            TurnContext(
                "What temporary nickname did I just give you?",
                "en",
                [],
            ),
        )
        return {
            "passed": "firefly" in reply.casefold(),
            "first_token_ms": first_token_ms,
            "total_ms": total_ms,
            "reply": reply,
        }
    finally:
        await provider.aclose()


async def check_cancel_recovery(base_url: str) -> dict:
    provider = EveLLM(base_url)

    async def consume_cancelled_turn() -> str:
        return "".join(
            [
                piece
                async for piece in provider.generate(
                    TurnContext(
                        "Explain ten distinct aspects of local AI in detail.", "en", []
                    )
                )
            ]
        )

    task = asyncio.create_task(consume_cancelled_turn())
    try:
        # Reproduce the app's hardest race: cancel locally after submission
        # begins, but before Eve has emitted a turn ID.
        await asyncio.sleep(0)
        provider.cancel()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        reply, first_token_ms, total_ms = await collect_reply(
            provider,
            TurnContext("Reply with the single word recovered.", "en", []),
        )
        return {
            "passed": "recovered" in reply.casefold()
            and not provider._cancel_pending,
            "early_cancel_requested": True,
            "pending_cancel_cleared": not provider._cancel_pending,
            "first_token_ms": first_token_ms,
            "total_ms": total_ms,
            "reply": reply,
        }
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await provider.aclose()


async def check_eve_memory_tools(base_url: str, core_url: str) -> dict:
    marker = f"EveToolBenchmark{uuid.uuid4().hex[:10]}"
    memory_id = None
    remember_reply = ""
    forget_reply = ""
    provider = EveLLM(base_url)
    async with httpx.AsyncClient(base_url=core_url.rstrip("/"), timeout=5.0) as client:
        try:
            remember_reply, _, _ = await collect_reply(
                provider,
                TurnContext(
                    "Please use the remember tool to store exactly this harmless fact: "
                    f"my Eve tool benchmark marker is {marker}.",
                    "en",
                    [],
                ),
            )
            recalled = await client.get("/v1/memories", params={"query": marker})
            recalled.raise_for_status()
            matching = [
                item
                for item in recalled.json()["memories"]
                if marker in str(item["content"])
            ]
            stored_through_eve = len(matching) == 1
            if stored_through_eve:
                memory_id = int(matching[0]["id"])
                forget_reply, _, _ = await collect_reply(
                    provider,
                    TurnContext(
                        "Please use forget_memory now to delete the benchmark memory "
                        f"with ID {memory_id}.",
                        "en",
                        [],
                    ),
                )
            after = await client.get("/v1/memories", params={"query": marker})
            after.raise_for_status()
            still_present = any(
                marker in str(item["content"])
                for item in after.json()["memories"]
            )
            deleted_through_eve = stored_through_eve and not still_present
            if deleted_through_eve:
                memory_id = None
            return {
                "passed": stored_through_eve and deleted_through_eve,
                "stored_through_eve": stored_through_eve,
                "deleted_through_eve": deleted_through_eve,
                "remember_reply": remember_reply,
                "forget_reply": forget_reply,
            }
        finally:
            await provider.aclose()
            if memory_id is not None:
                await client.delete(f"/v1/memories/{memory_id}")


async def check_eve_device_tools(base_url: str, core_url: str) -> dict:
    async with httpx.AsyncClient(base_url=core_url.rstrip("/"), timeout=5.0) as client:
        devices_response = await client.get("/v1/devices")
        devices_response.raise_for_status()
        devices = devices_response.json().get("devices", [])
        if not devices:
            return {"passed": False, "error": "no physical Stack-chan connected"}
        device_id = str(devices[0])
        provider = EveLLM(base_url, core_url=core_url, device_id=device_id)
        started_ns = time.perf_counter_ns()
        try:
            status_reply, _, _ = await collect_reply(
                provider,
                TurnContext(
                    "Are your physical head sensors ready? Answer briefly from the "
                    "device status result.",
                    "en",
                    [],
                ),
            )
            motion_reply, _, _ = await collect_reply(
                provider,
                TurnContext(
                    "Use move_head now to request yaw 12 degrees, pitch 45 degrees, "
                    "over 500 milliseconds. Do not claim physical completion from a "
                    "dispatched result.",
                    "en",
                    [],
                ),
            )
        finally:
            await provider.aclose()
        motion_events = []
        deadline = asyncio.get_running_loop().time() + 4.0
        while asyncio.get_running_loop().time() < deadline:
            response = await client.get(f"/v1/devices/{device_id}/results")
            response.raise_for_status()
            motion_events = [
                item
                for item in response.json().get("results", [])
                if item.get("received_monotonic_ns", 0) >= started_ns
                and item.get("tool") == "move_head"
            ]
            if any(item.get("stage") == "completed" for item in motion_events):
                break
            await asyncio.sleep(0.1)
        completed = next(
            (
                item
                for item in motion_events
                if item.get("stage") == "completed"
            ),
            None,
        )
        folded_status = status_reply.casefold()
        folded_motion = motion_reply.casefold()
        status_grounded = "ready" in folded_status and not any(
            phrase in folded_status
            for phrase in (
                "not ready",
                "don't have",
                "do not have",
                "can't confirm",
                "cannot confirm",
                "no device status",
            )
        )
        reply_grounded = not any(
            phrase in folded_motion
            for phrase in (
                "movement completed",
                "has completed",
                "successfully completed",
                "successfully moved",
                "has moved",
                "i moved",
            )
        )
        return {
            "passed": bool(
                status_grounded
                and reply_grounded
                and completed
                and completed.get("success") is True
            ),
            "status_reply": status_reply,
            "motion_reply": motion_reply,
            "motion_events": motion_events,
        }


async def check_memory_boundary(core_url: str) -> dict:
    marker = f"Eve benchmark marker {uuid.uuid4().hex}"
    memory_id = None
    async with httpx.AsyncClient(base_url=core_url.rstrip("/"), timeout=5.0) as client:
        try:
            stored = await client.post(
                "/v1/memories",
                json={
                    "content": marker,
                    "language": "en",
                    "kind": "explicit",
                    "importance": 0.5,
                },
            )
            stored.raise_for_status()
            memory_id = int(stored.json()["memory"]["id"])
            recalled = await client.get("/v1/memories", params={"query": marker})
            recalled.raise_for_status()
            recall_ids = [item["id"] for item in recalled.json()["memories"]]
            benign_recalled = memory_id in recall_ids
            denied = await client.post(
                "/v1/memories",
                json={
                    "content": "My password is benchmark-only-placeholder",
                    "language": "en",
                },
            )
            deleted = await client.delete(f"/v1/memories/{memory_id}")
            deleted.raise_for_status()
            deletion_confirmed = bool(deleted.json()["deleted"])
            memory_id = None
            return {
                "passed": benign_recalled
                and denied.status_code == 422
                and deletion_confirmed,
                "benign_recalled": benign_recalled,
                "sensitive_status": denied.status_code,
                "deletion_confirmed": deletion_confirmed,
            }
        finally:
            if memory_id is not None:
                await client.delete(f"/v1/memories/{memory_id}")


async def capture_contract_check(awaitable) -> dict:
    try:
        return await awaitable
    except BENCHMARK_FAILURES as error:
        return {
            "passed": False,
            "error_type": type(error).__name__,
            "error": str(error),
        }


async def run(base_url: str, core_url: str, output: Path) -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        info_response = await client.get(f"{base_url.rstrip('/')}/eve/v1/info")
        info_response.raise_for_status()
        info = info_response.json()
    model = str(info.get("agent", {}).get("model", {}).get("id", "unknown"))
    available_tools = {
        str(tool.get("name")) for tool in info.get("tools", {}).get("available", [])
    }
    expected_tools = {
        "device_status",
        "load_skill",
        "move_head",
        "play_routine",
        "remember",
        "recall_memory",
        "list_memories",
        "forget_memory",
        "set_face",
        "set_lights",
    }
    dangerous_tools = {
        "bash",
        "read_file",
        "write_file",
        "glob",
        "grep",
        "web_fetch",
        "web_search",
        "agent",
        "ask_question",
        "todo",
    }
    tool_surface = {
        "passed": available_tools == expected_tools
        and not (available_tools & dangerous_tools),
        "available": sorted(available_tools),
        "unexpected": sorted(available_tools - expected_tools),
        "missing": sorted(expected_tools - available_tools),
    }
    warmup_provider = EveLLM(base_url)
    warmup_reply = ""
    warmup_first_token_ms = None
    warmup_total_ms = None
    try:
        warmup_reply, warmup_first_token_ms, warmup_total_ms = await collect_reply(
            warmup_provider,
            TurnContext("Reply with the single word ready.", "en", []),
        )
    finally:
        await warmup_provider.aclose()
    results = []
    for scenario in SCENARIOS:
        provider = EveLLM(base_url)
        try:
            reply, first_token_ms, total_ms = await collect_reply(
                provider, scenario.context
            )
            passed, reasons = evaluate(scenario, reply)
            result = {
                "name": scenario.name,
                "passed": passed,
                "failure_reasons": reasons,
                "first_token_ms": first_token_ms,
                "total_ms": total_ms,
                "characters": len(reply),
                "reply": reply,
            }
        except BENCHMARK_FAILURES as error:
            result = {
                "name": scenario.name,
                "passed": False,
                "failure_reasons": ["provider_error"],
                "first_token_ms": None,
                "total_ms": None,
                "characters": 0,
                "reply": "",
                "error_type": type(error).__name__,
                "error": str(error),
            }
        finally:
            await provider.aclose()
        results.append(result)
    contract_checks = {
        "tool_surface": tool_surface,
        "multi_turn": await capture_contract_check(check_multi_turn(base_url)),
        "cancel_recovery": await capture_contract_check(check_cancel_recovery(base_url)),
        "memory_boundary": await capture_contract_check(check_memory_boundary(core_url)),
        "eve_memory_tools": await capture_contract_check(
            check_eve_memory_tools(base_url, core_url)
        ),
        "eve_device_tools": await capture_contract_check(
            check_eve_device_tools(base_url, core_url)
        ),
    }
    first_tokens = [item["first_token_ms"] for item in results if item["first_token_ms"]]
    contract_passed = all(item["passed"] for item in contract_checks.values())
    report = {
        "model": model,
        "scenario_count": len(results),
        "quality_pass_rate": sum(item["passed"] for item in results) / len(results),
        "first_token_p50_ms": statistics.median(first_tokens) if first_tokens else None,
        "first_token_max_ms": max(first_tokens) if first_tokens else None,
        "realtime_gate_passed": all(item["passed"] for item in results)
        and bool(first_tokens)
        and max(first_tokens) <= 1_500
        and contract_passed,
        "contract_passed": contract_passed,
        "contract_checks": contract_checks,
        "startup_warmup": {
            "reply": warmup_reply,
            "first_token_ms": warmup_first_token_ms,
            "total_ms": warmup_total_ms,
        },
        "scenarios": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:2000")
    parser.add_argument("--core-url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/benchmarks/eve-intelligence-latest.json")
    )
    args = parser.parse_args()
    report = asyncio.run(run(args.base_url, args.core_url, args.output))
    print(args.output)
    print(
        f"{report['model']}: quality={report['quality_pass_rate']:.0%}, "
        f"first-token p50={report['first_token_p50_ms'] or 0:.0f} ms, "
        f"contract={report['contract_passed']}, "
        f"realtime_gate={report['realtime_gate_passed']}"
    )
    if not report["realtime_gate_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
