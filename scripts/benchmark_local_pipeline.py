#!/usr/bin/env python3
"""Run repeatable local provider benchmarks on bilingual fixture audio."""

import argparse
import asyncio
import json
import math
import platform
import statistics
import time
import wave
from pathlib import Path

from stackchan_agent.config import Settings
from stackchan_agent.eve_provider import EveLLM
from stackchan_agent.local_providers import (
    MacOSTTS,
    SupertonicTTS,
    WhisperServerSTT,
)
from stackchan_agent.providers import TurnContext

FIXTURES = {
    "en": (
        "Hello Stack Chan. Please turn your head to the left and remember that I like coffee.",
        Path("../artifacts/benchmarks/fixtures/en.wav"),
    ),
    "ja": (
        "こんにちは、スタックチャン。左を向いて、私がコーヒーが好きだと覚えてください。",
        Path("../artifacts/benchmarks/fixtures/ja.wav"),
    ),
}

REPETITIONS = 3


def distribution(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50_ms": statistics.median(ordered),
        "p95_ms": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
        "mean_ms": statistics.fmean(ordered),
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
    if language == "ja":
        ref = list("".join(reference.split()))
        hyp = list("".join(hypothesis.split()))
    else:
        ref = reference.lower().split()
        hyp = hypothesis.lower().split()
    return edit_distance(ref, hyp) / max(1, len(ref))


def load_pcm(path: Path) -> tuple[bytes, int, float]:
    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
        return frames, handle.getframerate(), handle.getnframes() / handle.getframerate()


async def benchmark(settings: Settings) -> dict:
    results: dict = {
        "machine": platform.platform(),
        "providers": {},
    }

    stt_results = {}
    if settings.whisper_model.exists():
        stt = WhisperServerSTT(
            f"http://{settings.whisper_server_host}:{settings.whisper_server_port}"
        )
        for language, (reference, relative_path) in FIXTURES.items():
            path = (Path(__file__).parent / relative_path).resolve()
            pcm, sample_rate, audio_seconds = load_pcm(path)
            runs = []
            transcript = ""
            detected = ""
            for _ in range(REPETITIONS):
                started = time.perf_counter()
                transcript, detected = await stt.transcribe(pcm, sample_rate)
                runs.append((time.perf_counter() - started) * 1000)
            stt_results[language] = {
                "cold_ms": runs[0],
                "warm": distribution(runs[1:]),
                "audio_seconds": audio_seconds,
                "warm_real_time_factor": statistics.fmean(runs[1:]) / 1000 / audio_seconds,
                "detected_language": detected,
                "error_rate": normalized_error(reference, transcript, language),
            }
    else:
        stt_results["status"] = f"skipped: missing {settings.whisper_model}"
    results["providers"]["whisper_server"] = stt_results

    llm = EveLLM(settings.eve_url, timeout_seconds=settings.eve_timeout_seconds)
    llm_results = {}
    try:
        for language, (prompt, _) in FIXTURES.items():
            runs = []
            for _ in range(REPETITIONS):
                started = time.perf_counter()
                first_token = None
                character_count = 0
                async for piece in llm.generate(TurnContext(prompt, language, [])):
                    first_token = first_token or time.perf_counter()
                    character_count += len(piece)
                completed = time.perf_counter()
                runs.append(
                    {
                        "first_token_ms": (first_token - started) * 1000 if first_token else None,
                        "total_ms": (completed - started) * 1000,
                        "characters": character_count,
                    }
                )
            llm_results[language] = {
                "cold": runs[0],
                "warm_first_token": distribution(
                    [run["first_token_ms"] for run in runs[1:]]
                ),
                "warm_total": distribution([run["total_ms"] for run in runs[1:]]),
            }
    finally:
        await llm.aclose()
    results["providers"]["eve"] = {
        "configured_model": settings.eve_model,
        "note": "configured label; the independently launched Eve sidecar owns runtime selection",
        "languages": llm_results,
    }

    async def benchmark_tts(tts) -> dict:
        tts_results = {}
        for language, (text, _) in FIXTURES.items():
            runs = []
            for _ in range(REPETITIONS):
                started = time.perf_counter()
                first_pcm = None
                byte_count = 0
                async for chunk in tts.synthesize(text, language):
                    first_pcm = first_pcm or time.perf_counter()
                    byte_count += len(chunk)
                completed = time.perf_counter()
                audio_seconds = byte_count / 2 / tts.sample_rate
                runs.append(
                    {
                        "first_pcm_ms": (first_pcm - started) * 1000 if first_pcm else None,
                        "total_ms": (completed - started) * 1000,
                        "audio_seconds": audio_seconds,
                        "real_time_factor": (completed - started) / audio_seconds,
                    }
                )
            tts_results[language] = {
                "cold": runs[0],
                "warm_first_pcm": distribution(
                    [run["first_pcm_ms"] for run in runs[1:]]
                ),
                "warm_total": distribution([run["total_ms"] for run in runs[1:]]),
                "warm_rtf": statistics.fmean(
                    run["real_time_factor"] for run in runs[1:]
                ),
            }
        return tts_results

    results["providers"]["supertonic_tts"] = await benchmark_tts(
        SupertonicTTS(
            f"http://{settings.supertonic_host}:{settings.supertonic_port}",
            voice=settings.supertonic_voice,
            steps=settings.supertonic_steps,
            speed=settings.supertonic_speed,
        )
    )
    results["providers"]["macos_tts"] = await benchmark_tts(
        MacOSTTS(settings.tts_voice_en, settings.tts_voice_ja)
    )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(benchmark(Settings()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
