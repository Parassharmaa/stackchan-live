from pathlib import Path

import httpx
import pytest

from stackchan_agent.app import create_app
from stackchan_agent.config import Settings


def settings(tmp_path: Path) -> Settings:
    return Settings(
        provider="mock",
        memory_path=tmp_path / "memory.sqlite3",
        schedule_path=tmp_path / "schedules.sqlite3",
    )


@pytest.mark.asyncio
async def test_loopback_schedule_api_creates_lists_pauses_and_deletes(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path))
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/v1/devices/stack-1/schedules",
            json={
                "label": "Morning hello",
                "prompt": "Give me a warm, concise morning greeting.",
                "language": "en",
                "routine": "wake_up",
                "music": False,
                "capture_photo": False,
                "recurrence": "once",
                "timezone": "UTC",
                "local_time": "2099-01-01T09:00",
                "quiet_start": "22:00",
                "quiet_end": "07:00",
            },
        )
        assert created.status_code == 200
        schedule_id = created.json()["schedule"]["id"]
        listed = await client.get("/v1/devices/stack-1/schedules")
        assert listed.json()["schedules"][0]["label"] == "Morning hello"
        paused = await client.patch(
            f"/v1/devices/stack-1/schedules/{schedule_id}", json={"enabled": False}
        )
        assert paused.json()["schedule"]["enabled"] is False
        deleted = await client.delete(f"/v1/devices/stack-1/schedules/{schedule_id}")
        assert deleted.json() == {"schedule_id": schedule_id, "deleted": True}
    app.state.schedules.close()
    app.state.memory.close()


@pytest.mark.asyncio
async def test_schedule_api_rejects_privacy_and_safety_shortcuts(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path))
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    base = {
        "label": "Unsafe shortcut",
        "prompt": "Do something.",
        "language": "en",
        "routine": "focus",
        "music": True,
        "capture_photo": True,
        "recurrence": "daily",
        "timezone": "Asia/Tokyo",
        "local_time": "09:00",
        "quiet_start": "22:00",
        "quiet_end": "07:00",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        music = await client.post("/v1/devices/stack-1/schedules", json=base)
        timezone = await client.post(
            "/v1/devices/stack-1/schedules",
            json={**base, "music": False, "timezone": "Local/Guess"},
        )
        missing_quiet = await client.post(
            "/v1/devices/stack-1/schedules",
            json={key: value for key, value in base.items() if key != "quiet_end"},
        )
    app.state.schedules.close()
    app.state.memory.close()

    assert music.status_code == 422
    assert music.json()["detail"] == "scheduled music is available only with the dance routine"
    assert timezone.status_code == 422
    assert "IANA timezone" in timezone.json()["detail"]
    assert missing_quiet.status_code == 422


@pytest.mark.asyncio
async def test_schedule_api_is_loopback_only(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path))
    transport = httpx.ASGITransport(app=app, client=("192.168.1.50", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/devices/stack-1/schedules")
    app.state.schedules.close()
    app.state.memory.close()

    assert response.status_code == 403
