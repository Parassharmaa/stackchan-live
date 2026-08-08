#!/usr/bin/env python3
"""Benchmark native speech-to-speech on the same bilingual acoustic fixtures."""

import argparse
import asyncio
import json
import platform
import statistics
import tempfile
import time
import wave
from pathlib import Path

from stackchan_agent.config import Settings
from stackchan_agent.memory import MemoryStore
from stackchan_agent.realtime import OpenAIRealtimePipeline
from stackchan_agent.telemetry import TraceRecorder

FIXTURES = {
    "en": Path("../artifacts/benchmarks/fixtures/en.wav"),
    "ja": Path("../artifacts/benchmarks/fixtures/ja.wav"),
}


def load_pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValueError(f"fixture must be mono PCM16: {path}")
        return handle.readframes(handle.getnframes()), handle.getframerate()


async def benchmark(output: Path, repetitions: int) -> dict:
    settings = Settings()
    api_key = settings.openai_api_key
    if api_key is None or not api_key.get_secret_value().strip():
        raise RuntimeError("set OPENAI_API_KEY before running the Realtime benchmark")
    result: dict = {
        "machine": platform.platform(),
        "provider": "openai_realtime",
        "model": settings.openai_realtime_model,
        "voice": settings.openai_realtime_voice,
        "languages": {},
    }
    with tempfile.TemporaryDirectory(prefix="stackchan-realtime-bench-") as directory:
        root = Path(directory)
        pipeline = OpenAIRealtimePipeline(
            api_key=api_key.get_secret_value(),
            memory=MemoryStore(root / "memory.sqlite3"),
            trace=TraceRecorder(root / "traces"),
            url=settings.openai_realtime_url,
            model=settings.openai_realtime_model,
            voice=settings.openai_realtime_voice,
            reasoning_effort=settings.openai_realtime_reasoning_effort,
            transcription_model=settings.openai_realtime_transcription_model,
            max_output_tokens=settings.openai_realtime_max_output_tokens,
            timeout_seconds=settings.openai_realtime_timeout_seconds,
        )
        try:
            for language, relative_path in FIXTURES.items():
                pcm, sample_rate = load_pcm((Path(__file__).parent / relative_path).resolve())
                runs = []
                for _ in range(repetitions):
                    started = time.perf_counter()
                    first_audio = None
                    transcript = ""
                    response = ""
                    audio_bytes = 0
                    async for event in pipeline.run_turn(pcm, sample_rate):
                        if event.audio:
                            first_audio = first_audio or time.perf_counter()
                            audio_bytes += len(event.audio.pcm)
                        if event.control and event.control.type == "transcript.final":
                            transcript = str(event.control.payload.get("text", ""))
                        if event.control and event.control.type == "response.text.done":
                            response = str(event.control.payload.get("text", ""))
                    completed = time.perf_counter()
                    runs.append(
                        {
                            "first_audio_ms": (first_audio - started) * 1000
                            if first_audio
                            else None,
                            "total_ms": (completed - started) * 1000,
                            "transcript": transcript,
                            "response": response,
                            "audio_seconds": audio_bytes / 2 / 24_000,
                        }
                    )
                valid_first_audio = [
                    run["first_audio_ms"]
                    for run in runs
                    if run["first_audio_ms"] is not None
                ]
                result["languages"][language] = {
                    "runs": runs,
                    "first_audio_p50_ms": statistics.median(valid_first_audio),
                    "total_p50_ms": statistics.median(run["total_ms"] for run in runs),
                }
        finally:
            await pipeline.aclose()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/realtime-latest.json"),
    )
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.repetitions < 1:
        raise SystemExit("--repetitions must be positive")
    try:
        result = asyncio.run(benchmark(args.output, args.repetitions))
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
