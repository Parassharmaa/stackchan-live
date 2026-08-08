#!/usr/bin/env python3
"""Verify one live Eve turn executes multiple physical tools before replying."""

import argparse
import asyncio
import json
import time
import urllib.parse
from pathlib import Path

import httpx
from stackchan_agent.eve_provider import EveLLM
from stackchan_agent.providers import TurnContext


async def benchmark(base_url: str, eve_url: str, timeout_s: float) -> dict:
    async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
        response = await client.get("/v1/devices")
        response.raise_for_status()
        devices = response.json().get("devices", [])
        if not devices:
            raise RuntimeError("no physical Stack-chan is connected")
        device_id = str(devices[0])
        encoded_device = urllib.parse.quote(device_id, safe="")
        started_ns = time.perf_counter_ns()

        provider = EveLLM(eve_url, core_url=base_url, device_id=device_id)
        pieces: list[str] = []
        try:
            async with asyncio.timeout(timeout_s):
                async for piece in provider.generate(
                    TurnContext(
                        "Use move_head with yaw -10 degrees, pitch 45 degrees, and "
                        "duration 500 milliseconds, then set_lights with red 30, green "
                        "80, blue 200, brightness 0.2, and pulse animation. Complete "
                        "both tools in this turn, then tell me what actually completed.",
                        "en",
                        [],
                    )
                ):
                    pieces.append(piece)
        finally:
            await provider.aclose()

        deadline = asyncio.get_running_loop().time() + 5.0
        current: list[dict] = []
        while asyncio.get_running_loop().time() < deadline:
            results = await client.get(f"/v1/devices/{encoded_device}/results")
            results.raise_for_status()
            current = [
                item
                for item in results.json().get("results", [])
                if item.get("received_monotonic_ns", 0) >= started_ns
            ]
            completed_names = {
                str(item.get("tool"))
                for item in current
                if item.get("stage") == "completed" and item.get("success") is True
            }
            if {"move_head", "set_lights"}.issubset(completed_names):
                break
            await asyncio.sleep(0.1)

    reply = "".join(pieces).strip()
    completed = {
        str(item.get("tool"))
        for item in current
        if item.get("stage") == "completed" and item.get("success") is True
    }
    grounded_reply = bool(
        reply
        and any(term in reply.casefold() for term in ("head", "moved", "movement"))
        and any(term in reply.casefold() for term in ("light", "blue", "pulse"))
        and "dispatch" not in reply.casefold()
    )
    return {
        "device_id": device_id,
        "reply": reply,
        "completed_tools": sorted(completed),
        "tool_results": [
            item
            for item in current
            if item.get("tool") in {"move_head", "set_lights"}
        ],
        "grounded_reply": grounded_reply,
        "passed": {"move_head", "set_lights"}.issubset(completed) and grounded_reply,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--eve-url", default="http://127.0.0.1:2000")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        benchmark(args.base_url.rstrip("/"), args.eve_url.rstrip("/"), args.timeout)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
