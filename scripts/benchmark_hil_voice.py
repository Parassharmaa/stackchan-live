#!/usr/bin/env python3
"""Measure the physical Mac speaker -> Stack-chan -> Mac voice loop."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from stackchan_agent.metrics import speech_error_rate

ROOT = Path(__file__).resolve().parents[1]
TRACE_DIR = ROOT / "artifacts/benchmarks"
CASES = {
    "en": {
        "voice": "Samantha",
        "prompt_gain": 1.0,
        # Avoid a wake-name comma: macOS voices can pause there longer than the
        # device endpoint, turning one benchmark prompt into two speech turns.
        "prompt": "Count slowly from one to ten.",
        "interrupt": "Stop talking. I need a short joke instead.",
        "interrupt_parts": ("Stop talking.", "I need a short joke instead."),
        # The runner waits for the correlated firmware duck acknowledgement;
        # this small remainder covers the device's one-frame physical gain ramp.
        "interrupt_pause_s": 0.06,
        "prompt_intent_terms": (("count",), ("1", "one"), ("10", "ten")),
        "interrupt_intent_terms": (("joke",),),
    },
    "ja": {
        "voice": "Kyoko",
        "prompt_gain": 1.0,
        "prompt": "1から10までゆっくり数えてください。",
        "interrupt": "待ってください。ジョークを言って。",
        "interrupt_parts": ("待ってください。", "ジョークを言って。"),
        "interrupt_pause_s": 0.06,
        "prompt_intent_terms": (("1",), ("10",), ("数",)),
        "interrupt_intent_terms": (("ジョーク", "じょうく"), ("言",)),
    },
}


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)


def macos_output_muted() -> bool:
    """Read the physical Mac output mute state used by the acoustic fixture."""
    result = subprocess.run(
        ["osascript", "-e", "output muted of (get volume settings)"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip().casefold()
    if value not in {"true", "false"}:
        raise RuntimeError(f"unexpected macOS output mute state: {value!r}")
    return value == "true"


def set_macos_output_muted(muted: bool) -> None:
    subprocess.run(
        [
            "osascript",
            "-e",
            f"set volume output muted {'true' if muted else 'false'}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def macos_output_volume() -> int:
    result = subprocess.run(
        ["osascript", "-e", "output volume of (get volume settings)"],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def set_macos_output_volume(volume: int) -> None:
    subprocess.run(
        ["osascript", "-e", f"set volume output volume {volume}"],
        check=True,
        capture_output=True,
        text=True,
    )


@contextmanager
def audible_fixture_output():
    """Use a reproducible physical level and restore the exact Mac state."""
    was_muted = macos_output_muted()
    prior_volume = macos_output_volume()
    set_macos_output_volume(100)
    set_macos_output_muted(False)
    try:
        yield
    finally:
        set_macos_output_volume(prior_volume)
        set_macos_output_muted(was_muted)


def trace_offsets() -> dict[Path, int]:
    return {path: path.stat().st_size for path in TRACE_DIR.glob("*.jsonl")}


def new_trace_events(offsets: dict[Path, int]) -> list[dict]:
    events: list[dict] = []
    for path in TRACE_DIR.glob("*.jsonl"):
        offset = offsets.get(path, 0)
        with path.open() as handle:
            handle.seek(offset)
            for line in handle:
                if line.strip():
                    events.append(json.loads(line))
    return sorted(events, key=lambda event: event.get("start_ns", 0))


def latest_drop_count(
    results: list[dict], *, after_ns: int = 0, default: int = 0
) -> int:
    """Read the current cumulative counter, not the largest historical value.

    The server intentionally keeps result history across a device reconnect, while
    the firmware counter restarts at zero after flashing or rebooting.
    """
    samples = [
        result
        for result in results
        if result.get("component") == "audio"
        and result.get("received_monotonic_ns", 0) >= after_ns
    ]
    if not samples:
        return default
    latest = max(samples, key=lambda result: result.get("received_monotonic_ns", 0))
    return int(latest.get("playback_dropped_frames", default))


def latest_starvation_count(
    results: list[dict], *, after_ns: int = 0, default: int = 0
) -> int:
    samples = [
        result
        for result in results
        if result.get("component") == "audio"
        and result.get("received_monotonic_ns", 0) >= after_ns
    ]
    if not samples:
        return default
    latest = max(samples, key=lambda result: result.get("received_monotonic_ns", 0))
    return int(latest.get("playback_starvation_events", default))


def successful_physical_flushes(results: list[dict], *, after_ns: int) -> list[dict]:
    """Return only correlated firmware acknowledgements for a stopped speaker."""
    return [
        result
        for result in results
        if result.get("received_monotonic_ns", 0) >= after_ns
        and result.get("component") == "playback_flush"
        and result.get("success") is True
        and result.get("active") is False
        and result.get("request_id")
    ]


async def say(voice: str, text: str, *, gain: float = 1.0) -> None:
    # Render every language through the same normalization path. The fixture
    # temporarily uses 100% system output, while afplay gain is restricted to
    # unity or attenuation so test speech can never clip digitally.
    audio_path = await prepare_say(voice, text)
    playback = await asyncio.create_subprocess_exec(
        "afplay", "-v", str(gain), str(audio_path)
    )
    if await playback.wait() != 0:
        raise RuntimeError(f"macOS afplay failed for voice {voice}")


async def prepare_say(voice: str, text: str) -> Path:
    """Pre-render and loudness-normalize a reproducible near-end fixture.

    The installed Kyoko voice renders about 10 dB quieter than Samantha on this
    Mac. Without normalization, the bilingual HIL result measures voice-package
    gain rather than Stack-chan's Japanese interruption behavior.
    """
    digest = hashlib.sha256(f"loudnorm-v4-unity\0{voice}\0{text}".encode()).hexdigest()[
        :16
    ]
    audio_path = Path(tempfile.gettempdir()) / f"stackchan-hil-{digest}.aiff"
    if not audio_path.exists():
        raw_path = audio_path.with_name(f"{audio_path.stem}-raw.aiff")
        render = await asyncio.create_subprocess_exec(
            "say", "-v", voice, "-o", str(raw_path), text
        )
        if await render.wait() != 0:
            raise RuntimeError(f"macOS say render failed for voice {voice}")
        normalize = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(raw_path),
            "-af",
            "loudnorm=I=-16:TP=-2:LRA=7",
            "-ar",
            "22050",
            "-ac",
            "1",
            str(audio_path),
        )
        if await normalize.wait() != 0:
            raw_path.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg loudness normalization failed for {voice}")
        raw_path.unlink(missing_ok=True)
    return audio_path


def intent_recognized(text: str, required_groups: tuple[tuple[str, ...], ...]) -> bool:
    """Require one spoken cue from every semantic group.

    CER/WER remains in the report for literal quality. This independent signal
    prevents a harmless proper-name error from hiding successful task
    recognition while still rejecting a transcript that lost the request.
    """
    normalized = text.casefold()
    return bool(normalized) and all(
        any(term.casefold() in normalized for term in alternatives)
        for alternatives in required_groups
    )


async def wait_for_device_playback(
    base_url: str, device_id: str, after_ns: int, timeout_s: float = 12
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        payload = await asyncio.to_thread(
            fetch_json, f"{base_url}/v1/devices/{device_id}/results"
        )
        if any(
            result.get("received_monotonic_ns", 0) >= after_ns
            and (
                (
                    result.get("component") == "playback_state"
                    and result.get("active") is True
                )
                or (
                    result.get("component") == "audio"
                    and result.get("playback_active") is True
                )
            )
            for result in payload.get("results", [])
        ):
            return True
        await asyncio.sleep(0.1)
    return False


async def wait_for_device_listening_transition(
    base_url: str, device_id: str, after_ns: int, timeout_s: float = 4
) -> str | None:
    """Wait for a correlated physical duck or an already completed flush."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        payload = await asyncio.to_thread(
            fetch_json, f"{base_url}/v1/devices/{device_id}/results"
        )
        current = [
            result
            for result in payload.get("results", [])
            if result.get("received_monotonic_ns", 0) >= after_ns
        ]
        if successful_physical_flushes(current, after_ns=after_ns):
            return "flush"
        if any(
            result.get("component") == "playback_duck"
            and result.get("enabled") is True
            # The ESP transports this value as a 32-bit float, so the nominal
            # 0.05 gain can round to 0.0500000007. Match the server's correlated
            # acknowledgement tolerance instead of dropping the replacement
            # phrase because of wire-format precision.
            and float(result.get("gain", 1.0)) <= 0.051
            for result in current
        ):
            return "duck"
        await asyncio.sleep(0.05)
    return None


async def wait_for_device_idle(
    base_url: str,
    device_id: str,
    *,
    stable_s: float = 0.6,
    timeout_s: float = 30,
) -> bool:
    """Wait for a stable physical playback boundary between acoustic cases."""
    deadline = time.monotonic() + timeout_s
    idle_since: float | None = None
    while time.monotonic() < deadline:
        payload = await asyncio.to_thread(
            fetch_json, f"{base_url}/v1/devices/{device_id}/results"
        )
        states = [
            result
            for result in payload.get("results", [])
            if result.get("component") == "playback_state"
        ]
        active = bool(states and states[-1].get("active"))
        now = time.monotonic()
        if active:
            idle_since = None
        elif idle_since is None:
            idle_since = now
        elif now - idle_since >= stable_s:
            return True
        await asyncio.sleep(0.1)
    return False


async def run_case(
    language: str,
    response_headroom_s: float,
    base_url: str,
    device_id: str,
    interrupt_gain: float = 2.0,
    interrupt_delay_s: float = 0.35,
) -> dict:
    case = CASES[language]
    idle_before_prompt = await wait_for_device_idle(base_url, device_id)
    offsets = trace_offsets()
    started = time.time()
    playback_after_ns = time.perf_counter_ns()
    before_results = await asyncio.to_thread(
        fetch_json, f"{base_url}/v1/devices/{device_id}/results"
    )
    prior_dropped_frames = latest_drop_count(before_results.get("results", []))
    prior_starvation_events = latest_starvation_count(before_results.get("results", []))
    # Render before Stack-chan speaks. Doing this after playback begins creates
    # an artificial interval where only the robot's own voice reaches the mic.
    interrupt_parts = case.get("interrupt_parts", (case["interrupt"],))
    for part in interrupt_parts:
        await prepare_say(case["voice"], part)
    # The laptop is farther from the head microphone than a person speaking to
    # Stack-chan. Apply a modest, reproducible near-end gain to keep the fixture
    # above room noise without using the stronger double-talk interrupt gain.
    await say(case["voice"], case["prompt"], gain=case["prompt_gain"])
    playback_observed = await wait_for_device_playback(
        base_url,
        device_id,
        playback_after_ns,
        timeout_s=max(12, response_headroom_s),
    )
    duck_observed = False
    early_flush_observed = False
    control_cue_attempts = 0
    if playback_observed:
        # The server reserves a short AEC/bootstrap guard at the physical
        # playback edge. Starting on that exact edge can place the entire
        # Stop/ストップ word inside the guard and test fixture timing instead of
        # interruption. A nearby person naturally reacts after hearing speech.
        await asyncio.sleep(interrupt_delay_s)
        listening_transition: str | None = None
        control_part = interrupt_parts[0]
        # A person naturally repeats a control cue once when loud robot speech
        # masks it. Keep the retry explicit in the report so first-attempt and
        # recovered far-field behavior are distinguishable.
        for attempt in range(2):
            control_cue_attempts = attempt + 1
            part_started_ns = time.perf_counter_ns()
            await say(case["voice"], control_part, gain=interrupt_gain)
            listening_transition = await wait_for_device_listening_transition(
                base_url, device_id, part_started_ns
            )
            if listening_transition is not None:
                break
            await asyncio.sleep(0.15)
        duck_observed = listening_transition == "duck"
        early_flush_observed = listening_transition == "flush"
        if listening_transition is not None:
            await asyncio.sleep(float(case.get("interrupt_pause_s", 0.0)))
            for part in interrupt_parts[1:]:
                await say(case["voice"], part, gain=interrupt_gain)
    await asyncio.sleep(response_headroom_s)
    after_results = await asyncio.to_thread(
        fetch_json, f"{base_url}/v1/devices/{device_id}/results"
    )
    physical_playback = [
        result
        for result in after_results.get("results", [])
        if result.get("received_monotonic_ns", 0) >= playback_after_ns
        and result.get("component") == "playback_state"
    ]
    physical_flushes = successful_physical_flushes(
        after_results.get("results", []), after_ns=playback_after_ns
    )
    current_dropped_frames = latest_drop_count(
        after_results.get("results", []),
        after_ns=playback_after_ns,
        default=prior_dropped_frames,
    )
    # If the device rebooted during the case, count post-reboot drops instead of
    # producing an impossible negative delta. Other playback assertions will
    # still fail if the reconnect prevented a complete response.
    newly_dropped_frames = (
        current_dropped_frames - prior_dropped_frames
        if current_dropped_frames >= prior_dropped_frames
        else current_dropped_frames
    )
    current_starvation_events = latest_starvation_count(
        after_results.get("results", []),
        after_ns=playback_after_ns,
        default=prior_starvation_events,
    )
    new_starvation_events = (
        current_starvation_events - prior_starvation_events
        if current_starvation_events >= prior_starvation_events
        else current_starvation_events
    )
    events = new_trace_events(offsets)
    stt = [event for event in events if event.get("name") == "stt"]
    barge = [event for event in events if event.get("name") == "barge_in"]
    llm = [event for event in events if event.get("name") == "llm"]
    tts = [event for event in events if event.get("name") == "tts"]
    transcripts = [event.get("attributes", {}).get("transcript", "") for event in stt]
    prompt_error = (
        speech_error_rate(case["prompt"], transcripts[0], language)
        if transcripts
        else 1.0
    )
    interrupt_error = (
        speech_error_rate(case["interrupt"], transcripts[1], language)
        if len(transcripts) > 1
        else 1.0
    )
    prompt_intent_recognized = bool(transcripts) and intent_recognized(
        transcripts[0], case["prompt_intent_terms"]
    )
    interrupt_intent_recognized = len(transcripts) > 1 and intent_recognized(
        transcripts[1], case["interrupt_intent_terms"]
    )
    response_first_audio_after_stt_ms = None
    semantic_response_first_audio_after_stt_ms = None
    physical_response_start_after_stt_ms = None
    if len(stt) > 1 and tts:
        first_audio_ns = tts[-1]["start_ns"] + int(
            tts[-1].get("attributes", {}).get("first_audio_ms", 0) * 1_000_000
        )
        response_first_audio_after_stt_ms = round(
            (first_audio_ns - stt[1]["end_ns"]) / 1_000_000, 3
        )
        semantic_first_audio_ms = (
            tts[-1].get("attributes", {}).get("semantic_first_audio_ms")
        )
        if semantic_first_audio_ms is not None:
            semantic_first_audio_ns = tts[-1]["start_ns"] + int(
                semantic_first_audio_ms * 1_000_000
            )
            semantic_response_first_audio_after_stt_ms = round(
                (semantic_first_audio_ns - stt[1]["end_ns"]) / 1_000_000,
                3,
            )
        physical_response_starts = [
            result["received_monotonic_ns"]
            for result in physical_playback
            if result.get("active") is True
            and result.get("received_monotonic_ns", 0) >= stt[1]["end_ns"]
        ]
        if physical_response_starts:
            physical_response_start_after_stt_ms = round(
                (min(physical_response_starts) - stt[1]["end_ns"]) / 1_000_000,
                3,
            )
    return {
        "language": language,
        "started_unix": started,
        "idle_before_prompt": idle_before_prompt,
        "playback_observed_before_interrupt": playback_observed,
        "physical_duck_acknowledged": duck_observed,
        "control_cue_attempts": control_cue_attempts,
        "physical_early_flush_acknowledged": early_flush_observed,
        "physical_interruption_acknowledged": (duck_observed or early_flush_observed),
        "physical_playback_started": any(
            result.get("active") is True for result in physical_playback
        ),
        "physical_playback_drained": any(
            result.get("active") is False for result in physical_playback
        ),
        "physical_flush_acknowledged": bool(physical_flushes),
        "physical_flush_request_ids": [
            result["request_id"] for result in physical_flushes
        ],
        "newly_dropped_playback_frames": newly_dropped_frames,
        "new_playback_starvation_events": new_starvation_events,
        "transcripts": transcripts,
        "prompt_error_rate": round(prompt_error, 3),
        "interrupt_error_rate": round(interrupt_error, 3),
        "prompt_intent_recognized": prompt_intent_recognized,
        "interrupt_intent_recognized": interrupt_intent_recognized,
        "audio_artifacts": [
            event.get("attributes", {}).get("audio_artifact")
            for event in stt
            if event.get("attributes", {}).get("audio_artifact")
        ],
        "stt_ms": [round(event.get("duration_ms", 0), 3) for event in stt],
        "stt_routes": [
            {
                key: event.get("attributes", {}).get(key)
                for key in (
                    "stt_route",
                    "stt_fallback",
                    "stt_small_avg_logprob",
                    "stt_large_avg_logprob",
                )
                if key in event.get("attributes", {})
            }
            for event in stt
        ],
        "barge_in_flush_ms": [
            event.get("attributes", {}).get("flush_ms") for event in barge
        ],
        "llm_first_token_ms": [
            event.get("attributes", {}).get("first_token_ms") for event in llm
        ],
        "tts_first_audio_ms": [
            event.get("attributes", {}).get("first_audio_ms") for event in tts
        ],
        "response_first_audio_after_stt_ms": response_first_audio_after_stt_ms,
        "semantic_response_first_audio_after_stt_ms": (
            semantic_response_first_audio_after_stt_ms
        ),
        "physical_response_start_after_stt_ms": physical_response_start_after_stt_ms,
        "trace_ids": sorted({event.get("trace_id", "") for event in events}),
        "observed_two_turns": len(stt) >= 2,
        "unexpected_turns": max(0, len(stt) - 2),
        "observed_barge_in": bool(barge),
        "unexpected_barge_ins": max(0, len(barge) - 1),
        "response_completed": bool(llm and tts),
    }


async def benchmark(
    base_url: str,
    languages: list[str],
    wait_s: float,
    interrupt_gain: float,
    interrupt_delay_s: float,
) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no Stack-chan is connected to the local server")
    device = fetch_json(f"{base_url}/v1/devices/{devices[0]}")
    if device.get("audio_mode") != "full_duplex":
        raise RuntimeError("connected firmware did not report full_duplex audio")
    cases = []
    for index, language in enumerate(languages):
        if index:
            await asyncio.sleep(2)
        cases.append(
            await run_case(
                language,
                wait_s,
                base_url,
                devices[0],
                interrupt_gain,
                interrupt_delay_s,
            )
        )
    return {
        "device": device,
        "method": "physical acoustic loop via macOS say and automatic device VAD",
        "interrupt_fixture_gain": interrupt_gain,
        "interrupt_delay_after_playback_s": interrupt_delay_s,
        "cases": cases,
        "passed": all(
            case["idle_before_prompt"]
            and case["observed_two_turns"]
            and case["unexpected_turns"] == 0
            and case["playback_observed_before_interrupt"]
            and case["physical_interruption_acknowledged"]
            and case["physical_playback_started"]
            and case["physical_playback_drained"]
            and case["physical_flush_acknowledged"]
            and case["physical_response_start_after_stt_ms"] is not None
            and case["newly_dropped_playback_frames"] == 0
            and case["new_playback_starvation_events"] == 0
            and case["observed_barge_in"]
            and case["unexpected_barge_ins"] == 0
            and case["response_completed"]
            and max(case["barge_in_flush_ms"], default=999) < 50
            and case["prompt_intent_recognized"]
            and case["interrupt_intent_recognized"]
            for case in cases
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--language", choices=("en", "ja", "both"), default="both")
    parser.add_argument("--response-wait", type=float, default=20)
    parser.add_argument(
        "--interrupt-gain",
        type=float,
        default=1.0,
        help="afplay attenuation for the normalized near-end fixture (0, 1]",
    )
    parser.add_argument(
        "--interrupt-delay",
        type=float,
        default=0.35,
        help="seconds after physical playback starts before the near-end fixture",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.interrupt_delay < 0:
        raise SystemExit("--interrupt-delay must be non-negative")
    if not 0 < args.interrupt_gain <= 1:
        raise SystemExit("--interrupt-gain must be in (0, 1] to prevent clipping")
    languages = list(CASES) if args.language == "both" else [args.language]
    with audible_fixture_output():
        result = asyncio.run(
            benchmark(
                args.base_url,
                languages,
                args.response_wait,
                args.interrupt_gain,
                args.interrupt_delay,
            )
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
