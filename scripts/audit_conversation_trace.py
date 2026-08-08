#!/usr/bin/env python3
"""Audit recent Stack-chan conversation traces for grounded behavior regressions."""

import argparse
import json
import re
import statistics
from pathlib import Path


def _wake_name_only(text: str) -> bool:
    normalized = re.sub(r"[\s,，.。!！?？、-]+", "", text.casefold())
    return normalized in {"stackchan", "スタックちゃん", "すたっくちゃん", "스태크chan"}


def _affirmative(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(?:yes|yeah|yep|sure|please|do it|take it|go ahead|"
            r"はい|うん|ええ|お願いします|お願い|撮って|そうして)[。.!！?？\s]*",
            text.casefold(),
        )
    )


def _photo_reference(text: str) -> bool:
    folded = text.casefold()
    return bool(
        re.search(r"(?:take|capture|use).{0,36}(?:photo|picture|still|camera)", folded)
        or re.search(r"(?:photo|picture|still|camera).{0,36}(?:take|capture|use)", folded)
        or re.search(r"(?:写真|一枚).*(?:撮|撮影)", text)
    )


def _photo_offer(text: str) -> bool:
    folded = text.casefold()
    return _photo_reference(text) and bool(
        re.search(
            r"(?:would you like me to|do you want me to|shall i|may i|should i)",
            folded,
        )
        or re.search(
            r"(?:撮りましょうか|撮りますか|撮ってもいい|撮影しましょうか)", text
        )
    )


def _physical_now_claim(text: str) -> bool:
    folded = text.casefold()
    return bool(
        re.search(
            r"\b(?:light|lights|head|face)\b.{0,48}\b(?:is|are|has|have|turned|moved)\b"
            r".{0,48}\b(?:now|currently|blue|red|pink|left|right|up|down)\b",
            folded,
        )
        or re.search(r"(?:今|現在).*(?:ライト|頭|顔).*(?:青|赤|ピンク|向|なって)", text)
        or re.search(r"(?:ライト|頭|顔).*(?:青|赤|ピンク|向).*(?:今|なっています)", text)
    )


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 3)


def load_events(path: Path) -> list[dict]:
    if path.is_dir():
        candidates = sorted(path.glob("*.jsonl"), key=lambda item: item.stat().st_mtime)
        if not candidates:
            raise RuntimeError(f"no JSONL traces found under {path}")
        path = candidates[-1]
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def conversation_turns(events: list[dict]) -> list[dict]:
    turns: list[dict] = []
    pending_transcript: dict | None = None
    previous_response = ""
    for event in sorted(events, key=lambda item: item.get("start_ns", 0)):
        attributes = event.get("attributes", {})
        if event.get("name") == "stt":
            transcript = str(attributes.get("transcript", "")).strip()
            if transcript:
                pending_transcript = {
                    "transcript": transcript,
                    "stt": attributes,
                    "previous_response": previous_response,
                }
        elif event.get("name") == "llm" and pending_transcript is not None:
            response = str(attributes.get("response", "")).strip()
            turns.append(
                {
                    **pending_transcript,
                    "response": response,
                    "llm": attributes,
                    "duration_ms": float(event.get("duration_ms", 0.0)),
                }
            )
            previous_response = response
            pending_transcript = None
    return turns


def analyze(events: list[dict], *, tail_turns: int = 50) -> dict:
    turns = conversation_turns(events)[-tail_turns:]
    regressions: list[dict] = []
    first_tokens: list[float] = []
    totals: list[float] = []
    instrumented = 0
    for index, turn in enumerate(turns, start=1):
        transcript = turn["transcript"]
        response = turn["response"]
        llm = turn["llm"]
        memory_count = int(llm.get("memory_count", 0))
        planned_tools = llm.get("planned_tools")
        physical_results = llm.get("physical_action_results")
        has_instrumentation = isinstance(planned_tools, list) and isinstance(
            physical_results, list
        )
        if has_instrumentation:
            instrumented += 1
        if _wake_name_only(transcript) and memory_count:
            regressions.append(
                {
                    "type": "wake_name_memory_leak",
                    "turn": index,
                    "transcript": transcript,
                    "memories": llm.get("memories", []),
                }
            )
        if has_instrumentation and not planned_tools and not physical_results:
            if _physical_now_claim(response):
                regressions.append(
                    {
                        "type": "ungrounded_physical_state_claim",
                        "turn": index,
                        "transcript": transcript,
                        "response": response,
                    }
                )
            if (
                _affirmative(transcript)
                and _photo_offer(turn["previous_response"])
                and "capture_photo" not in planned_tools
            ):
                regressions.append(
                    {
                        "type": "photo_promised_without_capture",
                        "turn": index,
                        "transcript": transcript,
                        "previous_response": turn["previous_response"],
                        "response": response,
                    }
                )
        first_token = llm.get("first_token_ms")
        if isinstance(first_token, (int, float)):
            first_tokens.append(float(first_token))
        totals.append(turn["duration_ms"])

    metrics = {
        "turns": len(turns),
        "instrumented_turns": instrumented,
        "instrumentation_coverage": round(instrumented / len(turns), 3) if turns else 0.0,
        "first_token_p50_ms": round(statistics.median(first_tokens), 3)
        if first_tokens
        else None,
        "first_token_p95_ms": _percentile(first_tokens, 0.95),
        "total_p50_ms": round(statistics.median(totals), 3) if totals else None,
        "total_p95_ms": _percentile(totals, 0.95),
    }
    performance_warnings = []
    if metrics["first_token_p95_ms"] and metrics["first_token_p95_ms"] > 3_000:
        performance_warnings.append("first_token_p95_over_3000ms")
    if metrics["total_p95_ms"] and metrics["total_p95_ms"] > 10_000:
        performance_warnings.append("total_p95_over_10000ms")
    return {
        "metrics": metrics,
        "performance_warnings": performance_warnings,
        "regressions": regressions,
        "passed": not regressions and instrumented == len(turns),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("artifacts/benchmarks"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tail-turns", type=int, default=50)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    result = analyze(load_events(args.input), tail_turns=max(1, args.tail_turns))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"] and not args.report_only:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
