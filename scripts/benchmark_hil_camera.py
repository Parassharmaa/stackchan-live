#!/usr/bin/env python3
"""Verify the acoustic camera turn and shared-bus recovery on physical Stack-chan."""

import argparse
import asyncio
import io
import json
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from benchmark_hil_voice import (
    audible_fixture_output,
    fetch_json,
    latest_drop_count,
    latest_starvation_count,
    new_trace_events,
    say,
    trace_offsets,
    wait_for_device_idle,
    wait_for_device_playback,
)
from PIL import Image


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.load(response)


def fetch_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=3) as response:
        return response.read()


async def benchmark(base_url: str, timeout_s: float) -> dict:
    devices = fetch_json(f"{base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    device_id = str(devices[0])
    encoded_device = urllib.parse.quote(device_id, safe="")
    result_url = f"{base_url}/v1/devices/{encoded_device}/results"
    before = fetch_json(result_url).get("results", [])
    before_drops = latest_drop_count(before)
    before_starvations = latest_starvation_count(before)
    before_failures = max(
        (
            int(item.get("read_failures", 0))
            for item in before
            if item.get("component") == "head_sensor"
        ),
        default=0,
    )
    offsets = trace_offsets()
    conversation_started_ns = time.perf_counter_ns()
    await say(
        "Samantha",
        "I made a small 3D-printed object. Would you like to see it?",
    )
    first_playback_started = await wait_for_device_playback(
        base_url, device_id, conversation_started_ns, timeout_s
    )
    first_playback_drained = await wait_for_device_idle(
        base_url, device_id, stable_s=0.7, timeout_s=timeout_s
    )
    first_results = fetch_json(result_url).get("results", [])
    premature_capture = any(
        item.get("tool") == "capture_photo"
        and item.get("received_monotonic_ns", 0) >= conversation_started_ns
        for item in first_results
    )

    started_ns = time.perf_counter_ns()
    await say("Samantha", "Just look at it. Here it is.")

    deadline = time.monotonic() + timeout_s
    events: list[dict] = []
    current: list[dict] = []
    while time.monotonic() < deadline:
        events = new_trace_events(offsets)
        after = fetch_json(result_url).get("results", [])
        current = [
            item for item in after if item.get("received_monotonic_ns", 0) >= started_ns
        ]
        if any(item.get("tool") == "capture_photo" for item in current) and any(
            event.get("name") == "tts" for event in events
        ):
            break
        await asyncio.sleep(0.15)

    playback_started = await wait_for_device_playback(
        base_url, device_id, started_ns, timeout_s
    )
    playback_drained = await wait_for_device_idle(
        base_url, device_id, stable_s=0.7, timeout_s=timeout_s
    )
    events = new_trace_events(offsets)
    after = fetch_json(result_url).get("results", [])
    current = [item for item in after if item.get("received_monotonic_ns", 0) >= started_ns]
    transcripts = [
        str(event.get("attributes", {}).get("transcript", ""))
        for event in events
        if event.get("name") == "stt"
    ]
    responses = [
        str(event.get("attributes", {}).get("response", ""))
        for event in events
        if event.get("name") == "llm"
    ]
    motion_completed = any(
        item.get("tool") == "move_head"
        and item.get("stage") == "completed"
        and item.get("success") is True
        for item in current
    )
    captures = [item for item in current if item.get("component") == "camera_capture"]
    capture_results = [item for item in current if item.get("tool") == "capture_photo"]
    capture_completed = any(
        item.get("stage") == "completed"
        and item.get("success") is True
        and item.get("control_bus_restored") is True
        and isinstance(item.get("vision"), dict)
        and item["vision"].get("summary")
        for item in capture_results
    )
    vision_terms: set[str] = set()
    vision_reported_uncertainty = False
    for item in capture_results:
        vision = item.get("vision")
        if not isinstance(vision, dict):
            continue
        vision_reported_uncertainty = vision_reported_uncertainty or (
            "could not identify" in str(vision.get("summary", "")).casefold()
        )
        if int(vision.get("faceCount", 0)) > 0:
            vision_terms.add("face")
        for label in vision.get("labels", []):
            if isinstance(label, dict) and label.get("name"):
                vision_terms.update(
                    str(label["name"]).casefold().replace("_", " ").split()
                )
    response_text = (responses[-1] if responses else "").casefold()
    response_uses_vision = bool(
        (
            (vision_terms and any(term in response_text for term in vision_terms))
            or (
                vision_reported_uncertainty
                and any(
                    phrase in response_text
                    for phrase in (
                        "could not identify",
                        "couldn't identify",
                        "couldn’t identify",
                        "can't identify",
                        "can’t identify",
                        "cannot identify",
                        "isn't clear enough",
                        "is not clear enough",
                        "not clear enough",
                    )
                )
            )
        )
        and "can't see you right now" not in response_text
        and "cannot see you right now" not in response_text
    )

    jpeg = await asyncio.to_thread(
        fetch_bytes,
        f"{base_url}/v1/devices/{encoded_device}/captures/latest",
    )
    with Image.open(io.BytesIO(jpeg)) as image:
        image_format = image.format
        image_size = image.size
        image.verify()

    light_request_id = uuid.uuid4().hex
    await asyncio.to_thread(
        post_json,
        f"{base_url}/v1/devices/{encoded_device}/control",
        {
            "type": "lights.set",
            "request_id": light_request_id,
            "payload": {
                "red": 40,
                "green": 180,
                "blue": 255,
                "brightness": 0.2,
                "animation": "pulse",
            },
        },
    )
    light_recovered = False
    for _ in range(20):
        results = fetch_json(result_url).get("results", [])
        if any(
            item.get("request_id") == light_request_id
            and item.get("tool") == "set_lights"
            and item.get("success") is True
            for item in results
        ):
            light_recovered = True
            break
        await asyncio.sleep(0.1)

    head_samples = [item for item in current if item.get("component") == "head_sensor"]
    after_failures = max(
        (int(item.get("read_failures", 0)) for item in head_samples),
        default=before_failures,
    )
    after_drops = latest_drop_count(after, default=before_drops)
    after_starvations = latest_starvation_count(after, default=before_starvations)
    passed = bool(
        len(transcripts) == 2
        and "3d" in transcripts[0].casefold().replace("-", "")
        and (
            (
                "look" in transcripts[1].casefold()
                and "it" in transcripts[1].casefold()
            )
            or "here it is" in transcripts[1].casefold()
        )
        and len(responses) == 2
        and first_playback_started
        and first_playback_drained
        and not premature_capture
        and response_uses_vision
        and motion_completed
        and capture_completed
        and len(captures) == 1
        and image_format == "JPEG"
        and image_size == (320, 240)
        and light_recovered
        and any(item.get("read_ok") is True for item in head_samples)
        and after_failures == before_failures
        and playback_started
        and playback_drained
        and after_drops == before_drops
        and after_starvations == before_starvations
    )
    return {
        "device": fetch_json(f"{base_url}/v1/devices/{encoded_device}"),
        "transcripts": transcripts,
        "responses": responses,
        "premature_capture_before_visual_handoff": premature_capture,
        "response_uses_vision": response_uses_vision,
        "motion_completed": motion_completed,
        "camera_captures": captures,
        "capture_results": capture_results,
        "jpeg_bytes": len(jpeg),
        "jpeg_size": image_size,
        "light_bus_recovered": light_recovered,
        "head_sensor_read_failures_before": before_failures,
        "head_sensor_read_failures_after": after_failures,
        "physical_playback_started": playback_started,
        "physical_playback_drained": playback_drained,
        "playback_dropped_frames_before": before_drops,
        "playback_dropped_frames_after": after_drops,
        "playback_starvation_before": before_starvations,
        "playback_starvation_after": after_starvations,
        "passed": passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=35.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/hil-camera-latest.json"),
    )
    args = parser.parse_args()
    with audible_fixture_output():
        result = asyncio.run(benchmark(args.base_url, args.timeout))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output, flush=True)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
