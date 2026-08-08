import asyncio
import base64
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

import numpy as np
from websockets.asyncio.client import connect

from .memory import MemoryStore
from .pipeline import PipelineEvent
from .protocol import AudioFlags, AudioFrame, AudioStream, control
from .telemetry import TraceRecorder
from .tools import TOOLS, invoke_tool

ConnectionFactory = Callable[..., Awaitable[Any]]

STACKCHAN_REALTIME_INSTRUCTIONS = """
You are Stack-chan, a small, warm, playful desk robot with an expressive face,
RGB body lights, safe head motion, and coordinated music routines. Speak in the
language the user most recently used: English or Japanese. Keep ordinary replies
specific and thoughtful in two concise sentences by default, using up to three
when detail is requested. Speak promptly and naturally, and allow interruptions.
Use a device tool when expression, light, motion, or a routine adds meaning. Use
remember_fact only when the user explicitly asks you to remember something; use
recall_memory when a past fact is needed. A dispatched head movement is not proof
that the servos moved, so never claim physical completion. A memory saying “The
user” or 「ユーザー」 describes the human: speak it back as “your” or 「あなたの」,
never as Stack-chan's own preference. Do not promise storage unless a memory tool
result confirms it. Recall exact stored names and values without generalizing,
translating, or substituting them.
""".strip()


def resample_pcm16(pcm16: bytes, source_rate: int, target_rate: int = 24_000) -> bytes:
    """Resample mono little-endian PCM16 without adding a heavyweight DSP dependency."""
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if len(pcm16) % 2:
        raise ValueError("PCM16 payload must contain complete samples")
    if source_rate == target_rate or not pcm16:
        return pcm16
    source = np.frombuffer(pcm16, dtype="<i2").astype(np.float32)
    target_length = max(1, round(len(source) * target_rate / source_rate))
    positions = np.linspace(0, len(source) - 1, target_length, dtype=np.float32)
    resampled = np.interp(positions, np.arange(len(source)), source)
    return np.clip(np.rint(resampled), -32768, 32767).astype("<i2").tobytes()


def _language_for(text: str) -> str:
    return "ja" if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", text) else "en"


def realtime_tool_specs() -> list[dict[str, Any]]:
    specs = [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.schema.model_json_schema(),
        }
        for tool in TOOLS.values()
    ]
    specs.extend(
        [
            {
                "type": "function",
                "name": "remember_fact",
                "description": "Store a fact only after the user explicitly asks to remember it.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "minLength": 1, "maxLength": 400},
                        "language": {"type": "string", "enum": ["en", "ja", "und"]},
                    },
                    "required": ["content", "language"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "function",
                "name": "recall_memory",
                "description": "Search durable memories when a prior user fact is needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 300},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        ]
    )
    return specs


class OpenAIRealtimePipeline:
    """Persistent GA Realtime session adapted to Stack-chan's device event stream."""

    def __init__(
        self,
        *,
        api_key: str,
        memory: MemoryStore,
        trace: TraceRecorder,
        url: str = "wss://api.openai.com/v1/realtime",
        model: str = "gpt-realtime-2.1",
        voice: str = "marin",
        reasoning_effort: str = "low",
        transcription_model: str = "gpt-4o-mini-transcribe",
        max_output_tokens: int = 160,
        timeout_seconds: float = 20.0,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("speech_to_speech requires OPENAI_API_KEY")
        self._api_key = api_key
        self.memory = memory
        self.trace = trace
        self.url = url
        self.model = model
        self.voice = voice
        self.reasoning_effort = reasoning_effort
        self.transcription_model = transcription_model
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.connection_factory = connection_factory or connect
        self.memory_enabled = True
        self._ws: Any | None = None
        self._send_lock = asyncio.Lock()
        self._cancelled = False
        self._cancel_task: asyncio.Task[None] | None = None
        self._active_response_id: str | None = None
        self._last_assistant_item_id: str | None = None
        self._played_audio_ms = 0
        self._tool_results: dict[str, dict[str, Any]] = {}

    def complete_tool_result(self, request_id: str, result: dict[str, Any]) -> None:
        """Provide the correlated terminal firmware result to the model call."""
        self._tool_results[request_id] = result

    @property
    def endpoint(self) -> str:
        separator = "&" if "?" in self.url else "?"
        return f"{self.url}{separator}{urlencode({'model': self.model})}"

    def session_update(self) -> dict[str, Any]:
        return {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "model": self.model,
                "instructions": STACKCHAN_REALTIME_INSTRUCTIONS,
                "output_modalities": ["audio"],
                "max_output_tokens": self.max_output_tokens,
                "reasoning": {"effort": self.reasoning_effort},
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24_000},
                        "transcription": {"model": self.transcription_model},
                        "turn_detection": None,
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": 24_000},
                        "voice": self.voice,
                        "speed": 1.08,
                    },
                },
                "tools": realtime_tool_specs(),
                "tool_choice": "auto",
                "parallel_tool_calls": False,
            },
        }

    async def _send(self, event: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeError("Realtime connection is not open")
        async with self._send_lock:
            await self._ws.send(json.dumps(event, ensure_ascii=False))

    async def _receive(self) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("Realtime connection is not open")
        raw = await asyncio.wait_for(self._ws.recv(), timeout=self.timeout_seconds)
        event = json.loads(raw)
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise RuntimeError("Realtime server returned an invalid event")
        if event["type"] == "error":
            error = event.get("error", {})
            detail = error.get("message", "unknown Realtime API error")
            raise RuntimeError(str(detail))
        return event

    async def _ensure_connected(self) -> None:
        if self._ws is not None:
            return
        self._ws = await self.connection_factory(
            self.endpoint,
            additional_headers={"Authorization": f"Bearer {self._api_key}"},
            open_timeout=self.timeout_seconds,
            ping_interval=20,
            ping_timeout=20,
            max_size=4 * 1024 * 1024,
        )
        try:
            while (await self._receive())["type"] != "session.created":
                pass
            await self._send(self.session_update())
            while (await self._receive())["type"] != "session.updated":
                pass
        except Exception:
            await self.aclose()
            raise

    def cancel(self, played_audio_ms: int | None = None) -> None:
        self._cancelled = True
        if played_audio_ms is not None:
            self._played_audio_ms = max(0, played_audio_ms)
        if self._ws is not None and (self._cancel_task is None or self._cancel_task.done()):
            self._cancel_task = asyncio.create_task(self._cancel_upstream())

    async def _cancel_upstream(self) -> None:
        try:
            if self._active_response_id:
                await self._send(
                    {"type": "response.cancel", "response_id": self._active_response_id}
                )
            if self._last_assistant_item_id:
                await self._send(
                    {
                        "type": "conversation.item.truncate",
                        "item_id": self._last_assistant_item_id,
                        "content_index": 0,
                        "audio_end_ms": self._played_audio_ms,
                    }
                )
        except Exception:
            # The consumer will surface a transport failure or reconnect next turn.
            pass

    async def aclose(self) -> None:
        if self._cancel_task is not None and not self._cancel_task.done():
            await asyncio.gather(self._cancel_task, return_exceptions=True)
        ws, self._ws = self._ws, None
        self._active_response_id = None
        if ws is not None:
            await ws.close()

    async def _invoke_function(
        self, item: dict[str, Any]
    ) -> tuple[dict[str, Any], Any | None]:
        name = str(item.get("name", ""))
        call_id = str(item.get("call_id", ""))
        try:
            arguments = json.loads(item.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            device_control = None
            if name in TOOLS:
                device_control = await invoke_tool(name, arguments)
                device_control.request_id = call_id
                result: dict[str, Any] = {
                    "status": "awaiting_device",
                    "control": device_control.type,
                }
            elif name == "remember_fact":
                if not self.memory_enabled:
                    result = {"status": "disabled_for_test_session"}
                else:
                    content = str(arguments.get("content", "")).strip()
                    language = str(arguments.get("language", "und"))
                    if not content:
                        raise ValueError("memory content cannot be empty")
                    remembered, created = self.memory.remember_once(
                        content,
                        language=language if language in {"en", "ja", "und"} else "und",
                        kind="explicit",
                        importance=0.85,
                    )
                    result = {
                        "status": "stored" if created else "already_exists",
                        "memory_id": remembered.id,
                        "content": remembered.content,
                    }
                    device_control = control(
                        "memory.stored",
                        content=remembered.content,
                        created=created,
                        memory_id=remembered.id,
                    )
            elif name == "recall_memory":
                query = str(arguments.get("query", "")).strip()
                memories = self.memory.retrieve(query) if self.memory_enabled and query else []
                result = {"memories": [memory.content for memory in memories]}
            else:
                raise KeyError(f"unknown tool: {name}")
        except Exception as error:
            result = {"status": "error", "detail": str(error)}
            device_control = None
        output = {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result, ensure_ascii=False),
            },
        }
        return output, device_control

    def _audio_frame(self, pcm: bytes, sequence: int, flags: AudioFlags) -> AudioFrame:
        return AudioFrame(
            stream=AudioStream.SPEAKER,
            flags=flags,
            sequence=sequence,
            timestamp_ms=(time.perf_counter_ns() // 1_000_000) & 0xFFFFFFFF,
            pcm=pcm,
        )

    async def run_turn(self, pcm16: bytes, sample_rate: int) -> AsyncIterator[PipelineEvent]:
        self._cancelled = False
        self._played_audio_ms = 0
        await self._ensure_connected()
        yield PipelineEvent(control=control("session.state", state="thinking"))
        started_ns = time.perf_counter_ns()
        first_audio_ns: int | None = None
        transcript = ""
        automatic_profiles = []
        response_text: list[str] = []
        audio_buffer = bytearray()
        pending_pcm: bytes | None = None
        frame_bytes = 24_000 // 50 * 2
        sequence = 0
        audio_artifact = self.trace.capture_pcm16(pcm16, sample_rate)
        input_24k = resample_pcm16(pcm16, sample_rate)
        try:
            for offset in range(0, len(input_24k), 15_360):
                await self._send(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(input_24k[offset : offset + 15_360]).decode(),
                    }
                )
            await self._send({"type": "input_audio_buffer.commit"})
            await self._send({"type": "response.create"})

            response_complete = False
            while not response_complete:
                event = await self._receive()
                kind = event["type"]
                if kind == "response.created":
                    self._active_response_id = str(event.get("response", {}).get("id", ""))
                elif kind == "response.output_item.added":
                    item = event.get("item", {})
                    if item.get("type") == "message":
                        self._last_assistant_item_id = str(item.get("id", ""))
                elif kind == "conversation.item.input_audio_transcription.completed":
                    transcript = str(event.get("transcript", "")).strip()
                    if transcript:
                        language = _language_for(transcript)
                        if self.memory_enabled:
                            automatic_profiles = self.memory.capture_profile_memories(
                                transcript, language
                            )
                        yield PipelineEvent(
                            control=control(
                                "transcript.final",
                                text=transcript,
                                language=language,
                            )
                        )
                elif kind in {
                    "response.output_audio_transcript.delta",
                    "response.output_text.delta",
                }:
                    text = str(event.get("delta", ""))
                    if text:
                        response_text.append(text)
                        yield PipelineEvent(
                            control=control("response.text.delta", text=text)
                        )
                elif kind == "response.output_audio.delta":
                    if first_audio_ns is None:
                        first_audio_ns = time.perf_counter_ns()
                        yield PipelineEvent(
                            control=control("session.state", state="speaking")
                        )
                    audio_buffer.extend(base64.b64decode(event.get("delta", "")))
                    while len(audio_buffer) >= frame_bytes:
                        chunk = bytes(audio_buffer[:frame_bytes])
                        del audio_buffer[:frame_bytes]
                        if pending_pcm is not None:
                            flags = AudioFlags.START if sequence == 0 else AudioFlags.NONE
                            yield PipelineEvent(
                                audio=self._audio_frame(pending_pcm, sequence, flags)
                            )
                            sequence += 1
                        pending_pcm = chunk
                elif kind == "response.done":
                    self._active_response_id = None
                    response = event.get("response", {})
                    status = response.get("status")
                    calls = [
                        item
                        for item in response.get("output", [])
                        if item.get("type") == "function_call"
                    ]
                    if calls and status == "completed" and not self._cancelled:
                        for call in calls:
                            output, device_control = await self._invoke_function(call)
                            if device_control is not None:
                                yield PipelineEvent(control=device_control)
                                request_id = device_control.request_id
                                terminal = (
                                    self._tool_results.pop(request_id, None)
                                    if request_id
                                    else None
                                )
                                result = terminal or {
                                    "success": False,
                                    "stage": "timeout",
                                    "detail": "no correlated terminal firmware result",
                                }
                                output["item"]["output"] = json.dumps(
                                    result, ensure_ascii=False
                                )
                            await self._send(output)
                        await self._send({"type": "response.create"})
                    else:
                        response_complete = True

                if self._cancelled and kind in {"response.done", "response.cancelled"}:
                    response_complete = True

            if audio_buffer:
                if pending_pcm is not None:
                    flags = AudioFlags.START if sequence == 0 else AudioFlags.NONE
                    yield PipelineEvent(audio=self._audio_frame(pending_pcm, sequence, flags))
                    sequence += 1
                pending_pcm = bytes(audio_buffer)
            if pending_pcm is not None:
                flags = AudioFlags.END
                if sequence == 0:
                    flags |= AudioFlags.START
                yield PipelineEvent(audio=self._audio_frame(pending_pcm, sequence, flags))
                sequence += 1

            if self._cancelled:
                yield PipelineEvent(control=control("playback.flush", reason="barge_in"))
                return
            final_text = "".join(response_text).strip()
            if (
                transcript
                and final_text
                and self.memory_enabled
                and not automatic_profiles
            ):
                self.memory.capture_episode_memory(
                    transcript, final_text, _language_for(transcript)
                )
            yield PipelineEvent(control=control("response.text.done", text=final_text))
            yield PipelineEvent(control=control("session.state", state="idle"))
        except Exception:
            await self.aclose()
            raise
        finally:
            self.trace.record(
                "speech_to_speech",
                started_ns,
                model=self.model,
                input_bytes=len(pcm16),
                input_sample_rate=sample_rate,
                first_audio_ms=(first_audio_ns - started_ns) / 1_000_000
                if first_audio_ns
                else None,
                transcript=transcript,
                frames=sequence,
                audio_artifact=audio_artifact.name if audio_artifact else None,
                cancelled=self._cancelled,
            )
