from pathlib import Path

import httpx
import pytest

from stackchan_agent.app import create_app
from stackchan_agent.config import Settings


@pytest.mark.asyncio
async def test_loopback_memory_api_remembers_recalls_and_forgets(tmp_path: Path) -> None:
    settings = Settings(provider="mock", memory_path=tmp_path / "memory.sqlite3")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        stored = await client.post(
            "/v1/memories",
            json={
                "content": "temporary API fact",
                "language": "en",
                "kind": "explicit",
                "importance": 0.8,
            },
        )
        assert stored.status_code == 200
        memory_id = stored.json()["memory"]["id"]
        recalled = await client.get("/v1/memories", params={"query": "temporary"})
        assert recalled.json()["memories"][0]["id"] == memory_id
        deleted = await client.delete(f"/v1/memories/{memory_id}")
        assert deleted.json() == {"memory_id": memory_id, "deleted": True}
    app.state.memory.close()


@pytest.mark.asyncio
async def test_memory_api_rejects_non_loopback_clients(tmp_path: Path) -> None:
    settings = Settings(provider="mock", memory_path=tmp_path / "memory.sqlite3")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("192.168.1.50", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/memories")
    app.state.memory.close()

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_device_telemetry_api_rejects_non_loopback_clients(tmp_path: Path) -> None:
    settings = Settings(provider="mock", memory_path=tmp_path / "memory.sqlite3")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("192.168.1.50", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        responses = [
            await client.get("/v1/devices"),
            await client.get("/v1/devices/example"),
            await client.get("/v1/devices/example/results"),
            await client.post(
                "/v1/eve-sessions/example",
                json={"device_id": "example"},
            ),
        ]
    app.state.memory.close()

    assert [response.status_code for response in responses] == [403, 403, 403, 403]


@pytest.mark.asyncio
async def test_memory_api_rejects_sensitive_content(tmp_path: Path) -> None:
    settings = Settings(provider="mock", memory_path=tmp_path / "memory.sqlite3")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/memories",
            json={"content": "My password is a test-only phrase", "language": "en"},
        )
        memories = await client.get("/v1/memories")
    app.state.memory.close()

    assert response.status_code == 422
    assert response.json()["detail"] == "sensitive credential information cannot be stored"
    assert memories.json() == {"memories": []}
