#!/usr/bin/env python3
"""Compare Supertonic diffusion steps with bilingual round-trip ASR quality."""

import argparse
import asyncio
import json
import math
import platform
import statistics
import time
from pathlib import Path

from stackchan_agent.config import Settings
from stackchan_agent.local_providers import SupertonicTTS, WhisperServerSTT

CASES = {
    "en": "Here is a tiny joke: robots always have a good byte.",
    "ja": "短いジョークを言うね。ロボットは充電中でも元気だよ。",
}


def edit_distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, 1):
        current = [left_index]
        for right_index, right_value in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_value != right_value),
                )
            )
        previous = current
    return previous[-1]


def normalized_error(reference: str, hypothesis: str, language: str) -> float:
    punctuation = "，。！？、,.!?：:'\""
    if language == "ja":
        expected = list("".join(reference.translate(str.maketrans("", "", punctuation)).split()))
        actual = list("".join(hypothesis.translate(str.maketrans("", "", punctuation)).split()))
    else:
        expected = reference.casefold().translate(str.maketrans("", "", punctuation)).split()
        actual = hypothesis.casefold().translate(str.maketrans("", "", punctuation)).split()
    return edit_distance(expected, actual) / max(1, len(expected))


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


async def run(output: Path, steps: list[int], repetitions: int) -> dict:
    settings = Settings()
    recognizers = {
        "en": WhisperServerSTT(
            f"http://{settings.whisper_server_host}:{settings.whisper_server_port}",
            prompt=settings.whisper_prompt,
        ),
        "ja": WhisperServerSTT(
            f"http://{settings.whisper_server_host}:{settings.whisper_ja_server_port}",
            prompt=settings.whisper_ja_prompt,
        ),
    }
    result: dict = {
        "machine": platform.platform(),
        "voice": settings.supertonic_voice,
        "speed": settings.supertonic_speed,
        "repetitions": repetitions,
        "steps": {},
    }
    for step_count in steps:
        tts = SupertonicTTS(
            f"http://{settings.supertonic_host}:{settings.supertonic_port}",
            voice=settings.supertonic_voice,
            steps=step_count,
            speed=settings.supertonic_speed,
        )
        step_result = {}
        for language, reference in CASES.items():
            runs = []
            for _ in range(repetitions):
                started = time.perf_counter()
                first_pcm = None
                chunks = []
                async for chunk in tts.synthesize(reference, language):
                    first_pcm = first_pcm or time.perf_counter()
                    chunks.append(chunk)
                completed = time.perf_counter()
                pcm = b"".join(chunks)
                transcript, detected = await recognizers[language].transcribe(
                    pcm, tts.sample_rate
                )
                runs.append(
                    {
                        "first_pcm_ms": (first_pcm - started) * 1000,
                        "total_ms": (completed - started) * 1000,
                        "audio_seconds": len(pcm) / 2 / tts.sample_rate,
                        "transcript": transcript,
                        "detected_language": detected,
                        "round_trip_error_rate": normalized_error(
                            reference, transcript, language
                        ),
                    }
                )
            first_pcm_values = [run["first_pcm_ms"] for run in runs]
            step_result[language] = {
                "reference": reference,
                "runs": runs,
                "first_pcm_p50_ms": statistics.median(first_pcm_values),
                "first_pcm_p95_ms": percentile(first_pcm_values, 0.95),
                "mean_round_trip_error_rate": statistics.fmean(
                    run["round_trip_error_rate"] for run in runs
                ),
            }
        result["steps"][str(step_count)] = step_result
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/tts-steps-latest.json"),
    )
    parser.add_argument("--steps", type=int, nargs="+", default=[1, 2, 3, 5])
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.repetitions < 1 or any(step < 1 for step in args.steps):
        raise SystemExit("steps and repetitions must be positive")
    result = asyncio.run(run(args.output, args.steps, args.repetitions))
    print(args.output)
    for step, languages in result["steps"].items():
        summary = ", ".join(
            f"{language}: {values['first_pcm_p50_ms']:.0f} ms, "
            f"error {values['mean_round_trip_error_rate']:.3f}"
            for language, values in languages.items()
        )
        print(f"steps={step}: {summary}")


if __name__ == "__main__":
    main()
