#!/usr/bin/env python3
"""Run reproducible physical English and Japanese proactive-schedule checks."""

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def request_json(url: str, *, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"schedule API returned {error.code}: {detail}") from error


def run_case(
    base_url: str,
    device_id: str,
    *,
    local_time: datetime,
    language: str,
    capture_photo: bool,
) -> dict:
    existing_results = request_json(
        f"{base_url}/v1/devices/{urllib.parse.quote(device_id, safe='')}/results?limit=1"
    ).get("results", [])
    baseline_ns = max(
        (int(item.get("received_monotonic_ns", 0)) for item in existing_results),
        default=0,
    )
    label = f"HIL proactive {language}{' camera' if capture_photo else ''}"
    prompt = (
        "これは明示的に許可された一回だけの周囲確認です。ローカルVisionの結果だけを"
        "根拠に、日本語でかわいく自然な一文を話してください。推測しないでください。"
        if language == "ja"
        else "In one warm natural English sentence, ask whether I want to choose one "
        "focused goal for the next hour. Do not mention testing."
    )
    schedule = request_json(
        f"{base_url}/v1/devices/{urllib.parse.quote(device_id, safe='')}/schedules",
        method="POST",
        body={
            "label": label,
            "prompt": prompt,
            "language": language,
            "routine": "curious" if capture_photo else "focus",
            "music": False,
            "capture_photo": capture_photo,
            "recurrence": "once",
            "timezone": "Asia/Tokyo",
            "local_time": local_time.strftime("%Y-%m-%dT%H:%M"),
            "quiet_start": "23:00",
            "quiet_end": "07:00",
        },
    )["schedule"]
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        listed = request_json(
            f"{base_url}/v1/devices/{urllib.parse.quote(device_id, safe='')}/schedules"
        )["schedules"]
        current = next(item for item in listed if item["id"] == schedule["id"])
        if current["last_status"] == "completed":
            break
        if str(current["last_status"] or "").endswith("retry"):
            raise RuntimeError(
                f"schedule {schedule['id']} entered {current['last_status']}"
            )
        time.sleep(0.5)
    else:
        raise RuntimeError(f"schedule {schedule['id']} did not complete")

    results = request_json(
        f"{base_url}/v1/devices/{urllib.parse.quote(device_id, safe='')}/results?limit=300"
    )["results"]
    reaction = next(
        item
        for item in results
        if item.get("component") == "sensor_reaction"
        and item.get("schedule_id") == schedule["id"]
    )
    completion = next(
        item
        for item in results
        if item.get("component") == "schedule_completed"
        and item.get("schedule_id") == schedule["id"]
    )
    later = [
        item
        for item in results
        if item.get("received_monotonic_ns", 0) >= reaction["received_monotonic_ns"]
    ]
    playback_active = next(
        item
        for item in later
        if item.get("component") == "playback_state" and item.get("active") is True
    )
    playback_idle = next(
        item
        for item in later
        if item.get("component") == "playback_state"
        and item.get("active") is False
        and item["received_monotonic_ns"] > playback_active["received_monotonic_ns"]
    )
    ordering_passed = (
        reaction["received_monotonic_ns"]
        < playback_active["received_monotonic_ns"]
        < playback_idle["received_monotonic_ns"]
        < completion["received_monotonic_ns"]
    )
    audio = [item for item in later if item.get("component") == "audio"]
    audio_passed = bool(audio) and all(
        item.get("playback_dropped_frames", 0) == 0
        and item.get("playback_starvation_events", 0) == 0
        and item.get("microphone_clipped_samples", 0) == 0
        for item in audio
    )
    camera_passed = not capture_photo or any(
        item.get("component") == "camera_capture"
        and item["received_monotonic_ns"] > baseline_ns
        and item["received_monotonic_ns"] < reaction["received_monotonic_ns"]
        for item in results
    )
    return {
        "schedule_id": schedule["id"],
        "language": language,
        "capture_photo": capture_photo,
        "text": reaction["text"],
        "playback_idle_to_completion_ms": round(
            (completion["received_monotonic_ns"] - playback_idle["received_monotonic_ns"])
            / 1_000_000,
            3,
        ),
        "ordering_passed": ordering_passed,
        "audio_passed": audio_passed,
        "camera_passed": camera_passed,
        "passed": ordering_passed and audio_passed and camera_passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/hil-schedules-latest.json"),
    )
    args = parser.parse_args()
    devices = request_json(f"{args.base_url}/v1/devices").get("devices", [])
    if not devices:
        raise RuntimeError("no physical Stack-chan is connected")
    zone = ZoneInfo("Asia/Tokyo")
    first_minute = datetime.now(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
    cases = [
        run_case(
            args.base_url,
            str(devices[0]),
            local_time=first_minute,
            language="en",
            capture_photo=False,
        ),
        run_case(
            args.base_url,
            str(devices[0]),
            local_time=first_minute + timedelta(minutes=2),
            language="ja",
            capture_photo=True,
        ),
    ]
    result = {
        "device_id": devices[0],
        "method": "durable scheduler plus physical camera/routine/TTS telemetry",
        "cases": cases,
        "passed": all(case["passed"] for case in cases),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(args.output, flush=True)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
