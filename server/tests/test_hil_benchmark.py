import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_hil_voice.py"
SPEC = importlib.util.spec_from_file_location("benchmark_hil_voice", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_latest_drop_count_uses_newest_sample_after_reboot() -> None:
    results = [
        {
            "component": "audio",
            "received_monotonic_ns": 100,
            "playback_dropped_frames": 55,
        },
        {
            "component": "audio",
            "received_monotonic_ns": 200,
            "playback_dropped_frames": 0,
        },
    ]

    assert MODULE.latest_drop_count(results) == 0


def test_latest_drop_count_honors_case_start() -> None:
    results = [
        {
            "component": "audio",
            "received_monotonic_ns": 100,
            "playback_dropped_frames": 9,
        },
        {
            "component": "audio",
            "received_monotonic_ns": 300,
            "playback_dropped_frames": 2,
        },
    ]

    assert MODULE.latest_drop_count(results, after_ns=250) == 2
    assert MODULE.latest_drop_count(results, after_ns=400, default=7) == 7


def test_intent_recognition_requires_every_semantic_group() -> None:
    groups = (("ジョーク", "じょうく"), ("言",))

    assert MODULE.intent_recognized("おかえじょうくを言ってください", groups)
    assert not MODULE.intent_recognized("スタックちゃん、スタックちゃん", groups)


def test_physical_flush_requires_correlated_successful_stopped_ack() -> None:
    results = [
        {"component": "playback_state", "active": False, "received_monotonic_ns": 200},
        {
            "component": "playback_flush",
            "success": False,
            "active": False,
            "request_id": "failed",
            "received_monotonic_ns": 300,
        },
        {
            "component": "playback_flush",
            "success": True,
            "active": False,
            "request_id": "confirmed",
            "received_monotonic_ns": 400,
        },
    ]

    assert MODULE.successful_physical_flushes(results, after_ns=250) == [results[-1]]


def test_listening_transition_accepts_correlated_early_flush(monkeypatch) -> None:
    monkeypatch.setattr(
        MODULE,
        "fetch_json",
        lambda _url: {
            "results": [
                {
                    "component": "playback_flush",
                    "success": True,
                    "active": False,
                    "request_id": "physical-stop",
                    "received_monotonic_ns": 300,
                }
            ]
        },
    )

    assert (
        asyncio.run(
            MODULE.wait_for_device_listening_transition(
                "http://127.0.0.1:8765", "device", 250, timeout_s=0.1
            )
        )
        == "flush"
    )


def test_audible_fixture_restores_previously_muted_output(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "output muted of" in command[-1]:
            return SimpleNamespace(stdout="true\n")
        if "output volume of" in command[-1]:
            return SimpleNamespace(stdout="75\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)

    with MODULE.audible_fixture_output():
        assert calls[-1][-1] == "set volume output muted false"

    assert calls[-2][-1] == "set volume output volume 75"
    assert calls[-1][-1] == "set volume output muted true"


def test_audible_fixture_preserves_unmuted_output(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "output muted of" in command[-1]:
            return SimpleNamespace(stdout="false\n")
        if "output volume of" in command[-1]:
            return SimpleNamespace(stdout="75\n")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)

    with MODULE.audible_fixture_output():
        pass

    assert calls[-2][-1] == "set volume output volume 75"
    assert calls[-1][-1] == "set volume output muted false"
