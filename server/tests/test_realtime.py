import asyncio
import base64
import json
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from stackchan_agent.app import create_app, send_locked_text
from stackchan_agent.config import Settings
from stackchan_agent.memory import MemoryStore
from stackchan_agent.protocol import AudioFlags
from stackchan_agent.realtime import OpenAIRealtimePipeline, resample_pcm16
from stackchan_agent.telemetry import TraceRecorder


class FakeRealtimeSocket:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = deque(json.dumps(event) for event in events)
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def recv(self) -> str:
        if not self.events:
            await asyncio.Event().wait()
        return self.events.popleft()

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self) -> None:
        self.closed = True


class OverlapDetectingDeviceSocket:
    def __init__(self) -> None:
        self.active_writes = 0
        self.maximum_active_writes = 0
        self.sent: list[str] = []

    async def send_text(self, message: str) -> None:
        self.active_writes += 1
        self.maximum_active_writes = max(self.maximum_active_writes, self.active_writes)
        await asyncio.sleep(0)
        self.sent.append(message)
        self.active_writes -= 1


def make_pipeline(
    tmp_path: Path, socket: FakeRealtimeSocket
) -> OpenAIRealtimePipeline:
    async def factory(*args: Any, **kwargs: Any) -> FakeRealtimeSocket:
        assert args[0].endswith("?model=gpt-realtime-2.1")
        assert kwargs["additional_headers"]["Authorization"] == "Bearer test-key"
        return socket

    return OpenAIRealtimePipeline(
        api_key="test-key",
        memory=MemoryStore(tmp_path / "memory.sqlite3"),
        trace=TraceRecorder(tmp_path / "traces", trace_id="realtime-test"),
        connection_factory=factory,
        timeout_seconds=0.5,
    )


def test_speech_to_speech_fails_closed_without_credentials(tmp_path: Path) -> None:
    settings = Settings().model_copy(
        update={"provider": "speech_to_speech", "memory_path": tmp_path / "memory.sqlite3"}
    )
    with pytest.raises(ValueError, match="requires OPENAI_API_KEY"):
        create_app(settings)


def test_pcm16_resampler_preserves_duration_and_bounds() -> None:
    source = (np.sin(np.arange(16_000) * 2 * np.pi * 440 / 16_000) * 20_000).astype(
        "<i2"
    )
    resampled = np.frombuffer(resample_pcm16(source.tobytes(), 16_000), dtype="<i2")

    assert len(resampled) == 24_000
    assert abs(int(resampled.max())) <= 20_001


@pytest.mark.asyncio
async def test_device_text_writes_are_serialized_with_one_connection_lock() -> None:
    socket = OverlapDetectingDeviceSocket()
    lock = asyncio.Lock()

    await asyncio.gather(
        send_locked_text(socket, lock, "first"),  # type: ignore[arg-type]
        send_locked_text(socket, lock, "second"),  # type: ignore[arg-type]
    )

    assert socket.maximum_active_writes == 1
    assert socket.sent == ["first", "second"]


@pytest.mark.asyncio
async def test_realtime_streams_ga_audio_and_bilingual_transcript(tmp_path: Path) -> None:
    pcm = b"\x10\x00" * 960
    socket = FakeRealtimeSocket(
        [
            {"type": "session.created"},
            {"type": "session.updated"},
            {"type": "response.created", "response": {"id": "resp-1"}},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "こんにちは",
            },
            {
                "type": "response.output_item.added",
                "item": {"id": "assistant-1", "type": "message"},
            },
            {
                "type": "response.output_audio_transcript.delta",
                "delta": "やあ！",
            },
            {
                "type": "response.output_audio.delta",
                "delta": base64.b64encode(pcm).decode(),
            },
            {
                "type": "response.done",
                "response": {"status": "completed", "output": []},
            },
        ]
    )
    pipeline = make_pipeline(tmp_path, socket)

    events = [event async for event in pipeline.run_turn(b"\x01\x00" * 1_600, 16_000)]
    controls = [event.control for event in events if event.control]
    audio = [event.audio for event in events if event.audio]

    transcript = next(item for item in controls if item.type == "transcript.final")
    assert transcript.payload == {"text": "こんにちは", "language": "ja"}
    assert [frame.flags for frame in audio] == [AudioFlags.START, AudioFlags.END]
    assert len(audio[0].pcm) == 960
    assert controls[-1].type == "session.state"
    session = socket.sent[0]["session"]
    assert session["type"] == "realtime"
    assert session["audio"]["input"]["turn_detection"] is None
    assert session["audio"]["output"]["format"]["rate"] == 24_000
    assert {tool["name"] for tool in session["tools"]} >= {
        "move_head",
        "perform_gesture",
        "set_lights",
        "play_routine",
        "remember_fact",
        "recall_memory",
    }
    await pipeline.aclose()


@pytest.mark.asyncio
async def test_realtime_executes_device_tool_and_continues_response(tmp_path: Path) -> None:
    socket = FakeRealtimeSocket(
        [
            {"type": "session.created"},
            {"type": "session.updated"},
            {"type": "response.created", "response": {"id": "tool-response"}},
            {
                "type": "response.done",
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "name": "set_lights",
                            "call_id": "call-1",
                            "arguments": json.dumps(
                                {
                                    "red": 20,
                                    "green": 40,
                                    "blue": 255,
                                    "brightness": 0.25,
                                    "animation": "pulse",
                                }
                            ),
                        }
                    ],
                },
            },
            {"type": "response.created", "response": {"id": "final-response"}},
            {"type": "response.output_audio_transcript.delta", "delta": "Blue lights!"},
            {
                "type": "response.done",
                "response": {"status": "completed", "output": []},
            },
        ]
    )
    pipeline = make_pipeline(tmp_path, socket)

    events = []
    async for event in pipeline.run_turn(b"\x00\x00" * 1_600, 16_000):
        events.append(event)
        if event.control and event.control.type == "lights.set":
            assert event.control.request_id == "call-1"
            pipeline.complete_tool_result(
                "call-1",
                {
                    "tool": "set_lights",
                    "stage": "completed",
                    "success": True,
                    "detail": "LED frame written over I2C",
                },
            )

    assert any(event.control and event.control.type == "lights.set" for event in events)
    outputs = [
        event
        for event in socket.sent
        if event["type"] == "conversation.item.create"
    ]
    grounded = json.loads(outputs[0]["item"]["output"])
    assert grounded["success"] is True
    assert grounded["stage"] == "completed"
    assert sum(event["type"] == "response.create" for event in socket.sent) == 2
    await pipeline.aclose()


@pytest.mark.asyncio
async def test_realtime_explicit_memory_tool_is_deduplicated(tmp_path: Path) -> None:
    socket = FakeRealtimeSocket([])
    pipeline = make_pipeline(tmp_path, socket)
    item = {
        "name": "remember_fact",
        "call_id": "memory-call",
        "arguments": json.dumps({"content": "I like coffee", "language": "en"}),
    }

    first_output, first_control = await pipeline._invoke_function(item)
    second_output, second_control = await pipeline._invoke_function(item)

    assert first_control is not None and first_control.type == "memory.stored"
    assert second_control is not None and second_control.type == "memory.stored"
    assert json.loads(first_output["item"]["output"])["status"] == "stored"
    assert json.loads(second_output["item"]["output"])["status"] == "already_exists"
    assert [memory.content for memory in pipeline.memory.retrieve("coffee")] == [
        "I like coffee"
    ]
    await pipeline.aclose()


@pytest.mark.asyncio
async def test_realtime_profiles_completed_turn_but_not_an_unrelated_episode(
    tmp_path: Path,
) -> None:
    socket = FakeRealtimeSocket(
        [
            {"type": "session.created"},
            {"type": "session.updated"},
            {"type": "response.created", "response": {"id": "profile-response"}},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "I like jazz.",
            },
            {
                "type": "response.output_audio_transcript.delta",
                "delta": "Jazz rewards close listening.",
            },
            {
                "type": "response.done",
                "response": {"status": "completed", "output": []},
            },
        ]
    )
    pipeline = make_pipeline(tmp_path, socket)

    _ = [event async for event in pipeline.run_turn(b"\x00\x00" * 1_600, 16_000)]

    memories = pipeline.memory.list_recent(include_episodes=True)
    assert [(item.kind, item.content) for item in memories] == [
        ("profile", "The user likes jazz.")
    ]
    await pipeline.aclose()


@pytest.mark.asyncio
async def test_realtime_cancellation_truncates_to_played_audio(tmp_path: Path) -> None:
    socket = FakeRealtimeSocket(
        [{"type": "session.created"}, {"type": "session.updated"}]
    )
    pipeline = make_pipeline(tmp_path, socket)
    await pipeline._ensure_connected()
    pipeline._active_response_id = "resp-active"
    pipeline._last_assistant_item_id = "assistant-active"

    pipeline.cancel(321)
    assert pipeline._cancel_task is not None
    await pipeline._cancel_task

    assert socket.sent[-2] == {
        "type": "response.cancel",
        "response_id": "resp-active",
    }
    assert socket.sent[-1] == {
        "type": "conversation.item.truncate",
        "item_id": "assistant-active",
        "content_index": 0,
        "audio_end_ms": 321,
    }
    await pipeline.aclose()
