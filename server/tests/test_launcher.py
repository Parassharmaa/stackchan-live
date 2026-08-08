from pathlib import Path

import pytest

from stackchan_agent import launcher


def test_server_runtime_ready_does_not_require_connected_device(monkeypatch) -> None:
    monkeypatch.setattr(
        launcher,
        "_get_json",
        lambda _url: {
            "status": "degraded",
            "dependencies": {
                "device": False,
                "eve": True,
                "supertonic": True,
                "whisper": True,
            },
        },
    )

    assert launcher._server_runtime_ready("http://127.0.0.1:8765/health")


def test_server_runtime_ready_requires_every_voice_dependency(monkeypatch) -> None:
    monkeypatch.setattr(
        launcher,
        "_get_json",
        lambda _url: {
            "dependencies": {
                "device": True,
                "eve": True,
                "supertonic": False,
                "whisper": True,
            }
        },
    )

    assert not launcher._server_runtime_ready("http://127.0.0.1:8765/health")


def test_device_connected_is_reported_separately(monkeypatch) -> None:
    monkeypatch.setattr(
        launcher,
        "_get_json",
        lambda _url: {"dependencies": {"device": True}},
    )

    assert launcher._device_connected("http://127.0.0.1:8765/health")


def test_remote_eve_url_is_not_started_locally() -> None:
    with pytest.raises(RuntimeError, match="start that remote service"):
        launcher._eve_command("https://eve.example.test")


def test_tail_returns_only_requested_lines(tmp_path: Path) -> None:
    log = tmp_path / "service.log"
    log.write_text("one\ntwo\nthree\n", encoding="utf-8")

    assert launcher._tail(log, lines=2) == "two\nthree"
