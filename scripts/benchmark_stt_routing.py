#!/usr/bin/env python3
"""Benchmark resident Whisper routing on real Stack-chan acoustic captures."""

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from stackchan_agent.local_providers import detect_language
from stackchan_agent.metrics import speech_error_rate

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_ROOT = ROOT / "artifacts/benchmarks"
TRACE_ID = "2a5f0547-afed-4f19-8b40-46c77308949f"


@dataclass(frozen=True, slots=True)
class Capture:
    turn: int
    language: str
    reference: str
    intent_groups: tuple[tuple[str, ...], ...]
    artifact: str | None = None

    @property
    def path(self) -> Path:
        name = self.artifact or f"{TRACE_ID}-turn-{self.turn:03d}-clean.wav"
        return CAPTURE_ROOT / name


@dataclass(frozen=True, slots=True)
class Configuration:
    name: str
    url: str
    language: str
    prompt: str
    evaluate_languages: frozenset[str] = frozenset(("en", "ja"))


EN_PROMPT = "Stack Chan, count slowly from one to ten."
EN_INTERRUPT = "Stop, tell me a short joke instead."
JA_PROMPT = "スタックチャン、1から10までゆっくり数えてください。"
JA_INTERRUPT = "ストップ。短いジョークを言ってください。"
JA_PRIOR = "ストップ。短いジョークを言ってください。"
MIXED_PRIOR = (
    "Stack-chan. Stop. Head, lights, dance, music, short joke. "
    "スタックちゃん。ストップ。頭、ライト、ダンス、音楽、短いジョーク。"
)


def captures() -> tuple[Capture, ...]:
    result: list[Capture] = []
    for prompt_turn, interrupt_turn in ((6, 7), (10, 11), (14, 15), (18, 19)):
        result.extend(
            (
                Capture(
                    prompt_turn,
                    "en",
                    EN_PROMPT,
                    (("count",), ("1", "one"), ("10", "ten")),
                ),
                Capture(interrupt_turn, "en", EN_INTERRUPT, (("joke",),)),
            )
        )
    for prompt_turn, interrupt_turn in ((8, 9), (12, 13), (16, 17), (20, 21)):
        result.extend(
            (
                Capture(prompt_turn, "ja", JA_PROMPT, (("1",), ("10",), ("数",))),
                Capture(
                    interrupt_turn,
                    "ja",
                    JA_INTERRUPT,
                    (("ジョーク", "じょうく"), ("言",)),
                ),
            )
        )
    return tuple(result)


def configurations() -> tuple[Configuration, ...]:
    return (
        Configuration("base-auto", "http://127.0.0.1:8178", "auto", MIXED_PRIOR),
        Configuration("small-auto", "http://127.0.0.1:8180", "auto", ""),
        Configuration(
            "small-forced-ja-prior",
            "http://127.0.0.1:8180",
            "ja",
            JA_PRIOR,
            frozenset(("ja",)),
        ),
        Configuration("large-auto", "http://127.0.0.1:8181", "auto", ""),
        Configuration("large-auto-ja-prior", "http://127.0.0.1:8181", "auto", JA_PRIOR),
        Configuration(
            "large-forced-ja-prior",
            "http://127.0.0.1:8181",
            "ja",
            JA_PRIOR,
            frozenset(("ja",)),
        ),
    )


def walk_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_objects(child)


def broad_japanese_captures() -> tuple[Capture, ...]:
    """Discover unique standard HIL prompt/interrupt captures across reports."""
    discovered: dict[str, Capture] = {}
    for report_path in sorted(CAPTURE_ROOT.glob("*.json")):
        try:
            report = json.loads(report_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for item in walk_objects(report):
            audio_artifacts = item.get("audio_artifacts")
            if (
                item.get("language") != "ja"
                or not isinstance(audio_artifacts, list)
                or len(audio_artifacts) < 2
                or "observed_two_turns" not in item
            ):
                continue
            pairs = (
                (audio_artifacts[0], JA_PROMPT, (("1",), ("10",), ("数",))),
                (
                    audio_artifacts[1],
                    JA_INTERRUPT,
                    (("ジョーク", "じょうく"), ("言",)),
                ),
            )
            for artifact, reference, intent_groups in pairs:
                if not isinstance(artifact, str) or not artifact.endswith(".wav"):
                    continue
                discovered[artifact] = Capture(
                    -1, "ja", reference, intent_groups, artifact
                )
    return tuple(discovered[name] for name in sorted(discovered))


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def recognizes(text: str, groups: tuple[tuple[str, ...], ...]) -> bool:
    folded = text.casefold()
    return bool(folded) and all(
        any(term.casefold() in folded for term in alternatives)
        for alternatives in groups
    )


async def transcribe(
    client: httpx.AsyncClient, configuration: Configuration, capture: Capture
) -> dict:
    started = time.perf_counter_ns()
    response = await client.post(
        f"{configuration.url}/inference",
        files={"file": (capture.path.name, capture.path.read_bytes(), "audio/wav")},
        data={
            "response_format": "verbose_json",
            "language": configuration.language,
            **({"prompt": configuration.prompt} if configuration.prompt else {}),
        },
    )
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    response.raise_for_status()
    payload = response.json()
    text = str(payload.get("text", "")).strip()
    segments = payload.get("segments", [])
    average_log_probability = (
        statistics.mean(float(segment.get("avg_logprob", -99)) for segment in segments)
        if segments
        else -99.0
    )
    word_probabilities = [
        float(word.get("probability", 0))
        for segment in segments
        for word in segment.get("words", [])
    ]
    detected = detect_language(text)
    return {
        "turn": capture.turn if capture.turn >= 0 else None,
        "artifact": capture.path.name,
        "language": capture.language,
        "latency_ms": round(elapsed_ms, 3),
        "transcript": text,
        "detected_language": detected,
        "language_correct": detected == capture.language,
        "intent_recognized": recognizes(text, capture.intent_groups),
        "error_rate": round(
            speech_error_rate(capture.reference, text, capture.language), 3
        ),
        "average_log_probability": round(average_log_probability, 6),
        "minimum_word_probability": (
            round(min(word_probabilities), 6) if word_probabilities else None
        ),
    }


async def benchmark(suite_name: str) -> dict:
    suite = captures() if suite_name == "latest" else broad_japanese_captures()
    missing = [str(capture.path) for capture in suite if not capture.path.exists()]
    if missing:
        raise RuntimeError(f"missing physical captures: {', '.join(missing)}")

    result: dict = {
        "method": "resident Whisper HTTP on physical cleaned captures",
        "suite": suite_name,
        "capture_count": len(suite),
        "models": {},
    }
    selected_configurations = configurations()
    if suite_name == "broad-ja":
        selected_configurations = tuple(
            configuration
            for configuration in selected_configurations
            if configuration.name in {"small-forced-ja-prior", "large-forced-ja-prior"}
        )
    async with httpx.AsyncClient(timeout=30) as client:
        for configuration in selected_configurations:
            selected = [
                capture
                for capture in suite
                if capture.language in configuration.evaluate_languages
            ]
            # Warm the exact route before recording timings.
            await transcribe(client, configuration, selected[0])
            cases = [
                await transcribe(client, configuration, capture) for capture in selected
            ]
            latencies = [case["latency_ms"] for case in cases]
            per_language = {}
            for language in sorted(configuration.evaluate_languages):
                language_cases = [
                    case for case in cases if case["language"] == language
                ]
                language_latencies = [case["latency_ms"] for case in language_cases]
                per_language[language] = {
                    "count": len(language_cases),
                    "p50_ms": round(statistics.median(language_latencies), 3),
                    "p95_ms": round(percentile(language_latencies, 0.95), 3),
                    "intent_rate": round(
                        sum(case["intent_recognized"] for case in language_cases)
                        / len(language_cases),
                        3,
                    ),
                    "language_rate": round(
                        sum(case["language_correct"] for case in language_cases)
                        / len(language_cases),
                        3,
                    ),
                    "mean_error_rate": round(
                        statistics.mean(case["error_rate"] for case in language_cases),
                        3,
                    ),
                }
            result["models"][configuration.name] = {
                "endpoint": configuration.url,
                "request_language": configuration.language,
                "prompt": configuration.prompt,
                "p50_ms": round(statistics.median(latencies), 3),
                "p95_ms": round(percentile(latencies, 0.95), 3),
                "per_language": per_language,
                "cases": cases,
            }

    if suite_name == "latest":
        base_ja = result["models"]["base-auto"]["per_language"]["ja"]["p50_ms"]
        forced_ja = result["models"]["large-forced-ja-prior"]["per_language"]["ja"]
        sticky = result["models"]["large-auto-ja-prior"]
        sticky_ja = sticky["per_language"]["ja"]
        sticky_en = sticky["per_language"]["en"]
        result["routing"] = {
            "current_adaptive_ja_estimated_p50_ms": round(
                base_ja + forced_ja["p50_ms"], 3
            ),
            "small_adaptive_ja_estimated_p50_ms": round(
                base_ja
                + result["models"]["small-forced-ja-prior"]["per_language"]["ja"][
                    "p50_ms"
                ],
                3,
            ),
            "sticky_auto_ja_p50_ms": sticky_ja["p50_ms"],
            "detector_pass_ms": round(base_ja, 3),
            "sticky_auto_qualified": bool(
                sticky_ja["intent_rate"] == 1
                and sticky_ja["language_rate"] == 1
                and sticky_en["intent_rate"] == 1
                and sticky_en["language_rate"] == 1
            ),
        }
    else:
        small_cases = {
            case["artifact"]: case
            for case in result["models"]["small-forced-ja-prior"]["cases"]
        }
        large_cases = {
            case["artifact"]: case
            for case in result["models"]["large-forced-ja-prior"]["cases"]
        }
        regressions = [
            artifact
            for artifact, large_case in large_cases.items()
            if large_case["intent_recognized"]
            and not small_cases[artifact]["intent_recognized"]
        ]
        recoveries = [
            artifact
            for artifact, small_case in small_cases.items()
            if small_case["intent_recognized"]
            and not large_cases[artifact]["intent_recognized"]
        ]
        result["comparison"] = {
            "small_regressions_vs_large": regressions,
            "small_recoveries_vs_large": recoveries,
            "small_is_drop_in_qualified": not regressions,
        }
        sweeps = []
        for threshold in (-0.1, -0.15, -0.18, -0.2, -0.22, -0.25, -0.3, -0.4):
            chosen_cases = []
            fallback_artifacts = []
            total_latencies = []
            for artifact, small_case in small_cases.items():
                large_case = large_cases[artifact]
                chosen = small_case
                latency_ms = small_case["latency_ms"]
                if small_case["average_log_probability"] < threshold:
                    fallback_artifacts.append(artifact)
                    latency_ms += large_case["latency_ms"]
                    if (
                        large_case["average_log_probability"]
                        > small_case["average_log_probability"]
                    ):
                        chosen = large_case
                chosen_cases.append((artifact, chosen))
                total_latencies.append(latency_ms)
            selection_regressions = [
                artifact
                for artifact, chosen in chosen_cases
                if large_cases[artifact]["intent_recognized"]
                and not chosen["intent_recognized"]
            ]
            selection_recoveries = [
                artifact
                for artifact, chosen in chosen_cases
                if chosen["intent_recognized"]
                and not large_cases[artifact]["intent_recognized"]
            ]
            sweeps.append(
                {
                    "threshold": threshold,
                    "fallback_count": len(fallback_artifacts),
                    "fallback_rate": round(
                        len(fallback_artifacts) / len(small_cases), 3
                    ),
                    "model_only_p50_ms": round(statistics.median(total_latencies), 3),
                    "model_only_p95_ms": round(percentile(total_latencies, 0.95), 3),
                    "intent_rate": round(
                        sum(chosen["intent_recognized"] for _, chosen in chosen_cases)
                        / len(chosen_cases),
                        3,
                    ),
                    "mean_error_rate": round(
                        statistics.mean(
                            chosen["error_rate"] for _, chosen in chosen_cases
                        ),
                        3,
                    ),
                    "regressions_vs_large": selection_regressions,
                    "recoveries_vs_large": selection_recoveries,
                }
            )
        qualified = [item for item in sweeps if not item["regressions_vs_large"]]
        recommendation = (
            min(
                qualified,
                key=lambda item: (
                    -item["intent_rate"],
                    item["fallback_count"],
                    item["mean_error_rate"],
                ),
            )
            if qualified
            else None
        )
        result["confidence_routing"] = {
            "selection": "small first; on low confidence run large and keep higher average log probability",
            "sweep": sweeps,
            "recommended": recommendation,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/stt-routing-latest.json"),
    )
    parser.add_argument("--suite", choices=("latest", "broad-ja"), default="latest")
    args = parser.parse_args()
    result = asyncio.run(benchmark(args.suite))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    print(json.dumps(result.get("routing", result.get("comparison")), indent=2))


if __name__ == "__main__":
    main()
