import asyncio
import re
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from .memory import MemoryStore, SensitiveMemoryError, extract_explicit_memory
from .protocol import AudioFlags, AudioFrame, AudioStream, ControlMessage, control
from .providers import LLMProvider, STTProvider, TTSProvider, TurnContext
from .telemetry import TraceRecorder
from .tools import invoke_tool, plan_tools, unsupported_action_feedback

SERVER_PCM_BUDGET_FRAMES = 96
OUTPUT_QUEUE_CAPACITY_ITEMS = 64
# One frame is retained so the final packet can carry END, and one producer may
# be suspended while putting into a full output queue. In the worst case all 64
# generic output slots contain PCM, leaving 30 frames for TTS prefetch.
REMAINDER_PCM_QUEUE_CAPACITY_FRAMES = (
    SERVER_PCM_BUDGET_FRAMES - OUTPUT_QUEUE_CAPACITY_ITEMS - 2
)


@dataclass(slots=True)
class PipelineEvent:
    control: ControlMessage | None = None
    audio: AudioFrame | None = None


def take_speakable_phrase(
    text: str,
    language: str,
    *,
    force: bool = False,
    first_phrase: bool = False,
) -> tuple[str, str]:
    """Return the earliest natural phrase and the unconsumed remainder."""
    # Responses are already bounded to one short sentence. Give that sentence
    # enough room to finish so Supertonic normally performs one render instead
    # of producing audible gaps between many tiny clips.
    limit = 32 if language == "ja" else 72
    # Do not synthesize a tiny first fragment such as ``One.`` while the model
    # is still streaming ``One... two...``.  A four-character clip drains
    # before the remainder has been rendered and creates an audible dropout.
    # At end-of-stream ``force`` still emits genuinely short replies at once.
    minimum_first_phrase = 8 if language == "ja" else 12
    for index, character in enumerate(text):
        if character in ".?!。！？\n" and text[: index + 1].strip():
            if character == "." and (
                (index > 0 and text[index - 1] == ".")
                or (index + 1 < len(text) and text[index + 1] == ".")
            ):
                continue
            if (
                first_phrase
                and not force
                and len(text[: index + 1].strip()) < minimum_first_phrase
            ):
                continue
            return text[: index + 1].strip(), text[index + 1 :]
    # Supertonic returns a whole phrase before yielding PCM. Start it on a
    # short, speakable prefix while the LLM continues generating the remainder.
    # These thresholds keep the first synthesis request below roughly one
    # second of speech without firing on tiny, unstable token fragments.
    if len(text) >= limit:
        split = max(text.rfind(mark, limit // 2, limit + 1) for mark in " ,;，、：:")
        split = split + 1 if split >= limit // 2 else limit
        return text[:split].strip(), text[split:]
    if force and text.strip():
        return text.strip(), ""
    return "", text


def meaningful_transcript(transcript: str) -> bool:
    text = transcript.strip()
    if len(text) < 2:
        return False
    if re.fullmatch(r"(?:\[.*]|\(.*\)|<.*>|\*.*\*)", text):
        return False
    normalized = text.casefold().replace("_", " ")
    return normalized not in {"blank audio", "silence", "music", "noise"}


class CascadePipeline:
    def __init__(
        self,
        stt: STTProvider,
        llm: LLMProvider,
        tts: TTSProvider,
        memory: MemoryStore,
        trace: TraceRecorder,
    ) -> None:
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.memory = memory
        self.trace = trace
        self._cancelled = False
        self.memory_enabled = True
        self._recent_turns: deque[tuple[str, str]] = deque(maxlen=4)
        self._tool_results: dict[str, dict[str, Any]] = {}

    def complete_tool_result(self, request_id: str, result: dict[str, Any]) -> None:
        """Ground the pending turn with a correlated terminal firmware result."""
        self._tool_results[request_id] = result

    def cancel(self, played_audio_ms: int | None = None) -> None:
        del played_audio_ms
        self._cancelled = True
        self.llm.cancel()

    async def aclose(self) -> None:
        await self.llm.aclose()

    async def run_turn(self, pcm16: bytes, sample_rate: int) -> AsyncIterator[PipelineEvent]:
        self._cancelled = False
        yield PipelineEvent(control=control("session.state", state="thinking"))

        audio_artifact = self.trace.capture_pcm16(pcm16, sample_rate)
        with self.trace.span("stt", audio_bytes=len(pcm16), sample_rate=sample_rate) as attrs:
            if audio_artifact:
                attrs["audio_artifact"] = audio_artifact.name
            transcript, language = await self.stt.transcribe(pcm16, sample_rate)
            attrs.update(transcript=transcript, language=language)
            route = getattr(self.stt, "last_route", None)
            if isinstance(route, dict):
                attrs.update({f"stt_{key}": value for key, value in route.items()})
        if not meaningful_transcript(transcript):
            yield PipelineEvent(
                control=control("turn.ignored", reason="no_speech", transcript=transcript)
            )
            yield PipelineEvent(control=control("session.state", state="idle"))
            return
        yield PipelineEvent(control=control("transcript.final", text=transcript, language=language))

        approval_turn = self.llm.blocks_normal_turn()
        action_results: list[str] = []
        explicit_memory = (
            extract_explicit_memory(transcript, language)
            if self.memory_enabled and not approval_turn
            else None
        )
        if explicit_memory:
            try:
                remembered, created = self.memory.remember_once(
                    explicit_memory, language=language, kind="explicit", importance=0.85
                )
            except SensitiveMemoryError as error:
                action_results.append(
                    f"sensitive {error.category} information was not stored"
                )
                yield PipelineEvent(
                    control=control("memory.rejected", category=error.category)
                )
            else:
                action_results.append(
                    (
                        "stored durable memory: "
                        if created
                        else "durable memory already exists: "
                    )
                    + remembered.content
                )
                yield PipelineEvent(
                    control=control(
                        "memory.stored",
                        content=remembered.content,
                        created=created,
                        memory_id=remembered.id,
                    )
                )
        automatic_profiles = (
            self.memory.capture_profile_memories(transcript, language)
            if self.memory_enabled and not approval_turn
            else []
        )
        planned_tools = (
            []
            if approval_turn
            else plan_tools(transcript, language, recent_turns=list(self._recent_turns))
        )
        unsupported_actions = (
            [] if approval_turn else unsupported_action_feedback(transcript, language)
        )
        action_results.extend(unsupported_actions)
        for planned in planned_tools:
            request_id = uuid.uuid4().hex
            command = await invoke_tool(planned.name, planned.arguments)
            command.request_id = request_id
            yield PipelineEvent(control=command)
            result = self._tool_results.pop(request_id, None)
            if result is None:
                action_results.append(
                    f"{planned.name} was not physically confirmed before the timeout"
                )
            elif bool(result.get("success", False)):
                detail = str(result.get("detail") or result.get("stage") or "completed")
                action_results.append(f"{planned.name} physically completed: {detail}")
            else:
                detail = str(result.get("detail") or result.get("stage") or "failed")
                action_results.append(f"{planned.name} failed on the device: {detail}")

        memories = (
            [item.content for item in self.memory.retrieve(transcript)]
            # Device commands are already grounded by correlated firmware
            # results. Injecting old chat episodes here made Luna narrate about
            # coffee instead of acknowledging the physical action.
            if self.memory_enabled
            and not approval_turn
            and not planned_tools
            and not unsupported_actions
            else []
        )
        context = TurnContext(
            transcript=transcript,
            language=language,
            memories=memories,
            action_results=action_results,
            recent_turns=list(self._recent_turns),
        )
        output: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(
            maxsize=OUTPUT_QUEUE_CAPACITY_ITEMS
        )
        phrases: asyncio.Queue[tuple[str, bool] | None] = asyncio.Queue(maxsize=8)
        remainder_audio: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=REMAINDER_PCM_QUEUE_CAPACITY_FRAMES
        )
        pieces: list[str] = []
        announced_approval_request_id: str | None = None
        llm_start = time.perf_counter_ns()
        tts_start: int | None = None
        first_token_ns: int | None = None
        first_audio_ns: int | None = None
        semantic_first_audio_ns: int | None = None
        first_phrase_chars: int | None = None
        tts_requests = 0
        tts_request_details: list[dict[str, Any]] = []
        server_pcm_queue_high_water = 0
        queued_output_audio_frames = 0
        pending_pcm: bytes | None = None

        def update_pcm_high_water() -> None:
            nonlocal server_pcm_queue_high_water
            buffered = (
                queued_output_audio_frames
                + remainder_audio.qsize()
                + (1 if pending_pcm is not None else 0)
            )
            server_pcm_queue_high_water = max(server_pcm_queue_high_water, buffered)

        async def put_audio_output(pcm: bytes) -> None:
            """Transfer one PCM frame while preserving the end-to-end budget."""
            nonlocal queued_output_audio_frames
            queued_output_audio_frames += 1
            update_pcm_high_water()
            try:
                await output.put(("audio", pcm))
            except BaseException:
                queued_output_audio_frames -= 1
                raise

        async def produce_text() -> None:
            nonlocal announced_approval_request_id, first_phrase_chars, first_token_ns
            speech_buffer = ""
            phrase_emitted = False
            cancelled = False
            try:
                async for piece in self.llm.generate(context):
                    if self._cancelled:
                        break
                    if first_token_ns is None:
                        first_token_ns = time.perf_counter_ns()
                    pieces.append(piece)
                    pending_approval = self.llm.pending_tool_approval()
                    if (
                        pending_approval is not None
                        and pending_approval.request_id != announced_approval_request_id
                    ):
                        announced_approval_request_id = pending_approval.request_id
                        await output.put(
                            (
                                "control",
                                control(
                                    "approval.requested",
                                    request_id=pending_approval.request_id,
                                    tool_name=pending_approval.tool_name,
                                    action_summary=pending_approval.action_summary,
                                    challenge=pending_approval.challenge,
                                    timeout_seconds=round(
                                        pending_approval.seconds_remaining, 1
                                    ),
                                ),
                            )
                        )
                    speech_buffer += piece
                    await output.put(("text", piece))
                    while True:
                        phrase, speech_buffer = take_speakable_phrase(
                            speech_buffer,
                            language,
                            first_phrase=not phrase_emitted,
                        )
                        if not phrase:
                            break
                        if first_phrase_chars is None:
                            first_phrase_chars = len(phrase)
                        phrase_emitted = True
                        await phrases.put((phrase, False))
                phrase, _ = take_speakable_phrase(speech_buffer, language, force=True)
                if phrase and not self._cancelled:
                    if first_phrase_chars is None:
                        first_phrase_chars = len(phrase)
                    await phrases.put((phrase, False))
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception as error:
                await output.put(("error", error))
            finally:
                # A cancelled producer may be blocked behind a full phrase
                # queue after its consumer has also been cancelled. There is
                # no consumer to notify during teardown, so do not enqueue
                # sentinels in that path.
                if not cancelled:
                    await phrases.put(None)
                    await output.put(("llm_done", None))

        async def produce_audio() -> None:
            nonlocal first_audio_ns, semantic_first_audio_ns, tts_requests, tts_start
            nonlocal server_pcm_queue_high_water
            cancelled = False
            remainder_task: asyncio.Task[None] | None = None
            # This inner queue and the generic output queue share one 96-frame
            # server PCM budget. The ESP independently owns another 96-frame
            # physical playout queue.

            def begin_tts_request(text: str) -> tuple[dict[str, Any], int]:
                nonlocal tts_requests
                started_ns = time.perf_counter_ns()
                detail: dict[str, Any] = {
                    "index": tts_requests,
                    "text": text,
                    "characters": len(text),
                    "frames": 0,
                }
                tts_requests += 1
                tts_request_details.append(detail)
                return detail, started_ns

            def finish_tts_request(
                detail: dict[str, Any], started_ns: int, frames: int
            ) -> None:
                detail["frames"] = frames
                detail["producer_duration_ms"] = round(
                    (time.perf_counter_ns() - started_ns) / 1_000_000, 3
                )

            async def render_remainder() -> None:
                nonlocal server_pcm_queue_high_water
                renderer_cancelled = False
                try:
                    while (item := await phrases.get()) is not None:
                        phrase, _ = item
                        if self._cancelled:
                            return
                        # Start synthesizing the next complete phrase as soon
                        # as it exists. Previously all remaining text waited
                        # for LLM end-of-stream, so the physical queue could
                        # drain between the first and second TTS requests.
                        detail, request_started_ns = begin_tts_request(phrase)
                        request_frames = 0
                        try:
                            async for pcm in self.tts.synthesize(phrase, language):
                                if self._cancelled:
                                    return
                                if request_frames == 0:
                                    detail["first_frame_ms"] = round(
                                        (
                                            time.perf_counter_ns()
                                            - request_started_ns
                                        )
                                        / 1_000_000,
                                        3,
                                    )
                                await remainder_audio.put(pcm)
                                request_frames += 1
                                update_pcm_high_water()
                        finally:
                            finish_tts_request(
                                detail, request_started_ns, request_frames
                            )
                except asyncio.CancelledError:
                    renderer_cancelled = True
                    raise
                finally:
                    if not renderer_cancelled:
                        await remainder_audio.put(None)

            try:
                first_item = await phrases.get()
                if first_item is None or self._cancelled:
                    return
                first_phrase, _ = first_item
                # Render the first complete phrase immediately. A separate
                # producer renders later phrases into a bounded PCM queue while
                # the first phrase is still being sent and physically buffered.
                remainder_task = asyncio.create_task(render_remainder())
                tts_start = time.perf_counter_ns()
                first_detail, first_request_started_ns = begin_tts_request(first_phrase)
                first_request_frames = 0
                await output.put(("control", control("session.state", state="speaking")))
                try:
                    async for pcm in self.tts.synthesize(first_phrase, language):
                        if self._cancelled:
                            break
                        first_request_frames += 1
                        first_audio_ns = first_audio_ns or time.perf_counter_ns()
                        if semantic_first_audio_ns is None:
                            semantic_first_audio_ns = time.perf_counter_ns()
                        if first_request_frames == 1:
                            first_detail["first_frame_ms"] = round(
                                (
                                    time.perf_counter_ns()
                                    - first_request_started_ns
                                )
                                / 1_000_000,
                                3,
                            )
                        await put_audio_output(pcm)
                finally:
                    finish_tts_request(
                        first_detail,
                        first_request_started_ns,
                        first_request_frames,
                    )
                if not self._cancelled:
                    while (pcm := await remainder_audio.get()) is not None:
                        if self._cancelled:
                            break
                        await put_audio_output(pcm)
                    await remainder_task
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception as error:
                await output.put(("error", error))
            finally:
                if remainder_task is not None and not remainder_task.done():
                    remainder_task.cancel()
                    await asyncio.gather(remainder_task, return_exceptions=True)
                if not cancelled:
                    await output.put(("tts_done", None))

        workers = [asyncio.create_task(produce_text()), asyncio.create_task(produce_audio())]
        llm_done = False
        tts_done = False
        sequence = 0
        try:
            while not (llm_done and tts_done):
                kind, value = await output.get()
                if self._cancelled:
                    yield PipelineEvent(control=control("playback.flush", reason="barge_in"))
                    return
                if kind == "text":
                    yield PipelineEvent(control=control("response.text.delta", text=value))
                elif kind == "control":
                    yield PipelineEvent(control=value)
                elif kind == "audio":
                    queued_output_audio_frames -= 1
                    if pending_pcm is not None:
                        flags = AudioFlags.START if sequence == 0 else AudioFlags.NONE
                        yield PipelineEvent(
                            audio=AudioFrame(
                                stream=AudioStream.SPEAKER,
                                flags=flags,
                                sequence=sequence,
                                timestamp_ms=(time.perf_counter_ns() // 1_000_000) & 0xFFFFFFFF,
                                pcm=pending_pcm,
                            )
                        )
                        sequence += 1
                    pending_pcm = value
                elif kind == "llm_done":
                    llm_done = True
                elif kind == "tts_done":
                    tts_done = True
                    if pending_pcm is not None:
                        flags = AudioFlags.END
                        if sequence == 0:
                            flags |= AudioFlags.START
                        yield PipelineEvent(
                            audio=AudioFrame(
                                stream=AudioStream.SPEAKER,
                                flags=flags,
                                sequence=sequence,
                                timestamp_ms=(time.perf_counter_ns() // 1_000_000) & 0xFFFFFFFF,
                                pcm=pending_pcm,
                            )
                        )
                        sequence += 1
                        pending_pcm = None
                elif kind == "error":
                    raise value
        finally:
            for worker in workers:
                if not worker.done():
                    worker.cancel()
            cleanup = asyncio.gather(*workers, return_exceptions=True)
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

        llm_end = time.perf_counter_ns()
        response = "".join(pieces).strip()
        automatic_memories = list(automatic_profiles)
        if (
            response
            and self.memory_enabled
            and not automatic_profiles
            and not approval_turn
        ):
            automatic_memories.extend(
                self.memory.capture_episode_memory(transcript, response, language)
            )
        self.trace.record(
            "llm",
            llm_start,
            first_token_ms=(first_token_ns - llm_start) / 1_000_000 if first_token_ns else None,
            characters=sum(map(len, pieces)),
            response=response,
            memory_count=len(memories),
            memories=memories,
            automatic_memory_count=len(automatic_memories),
            automatic_memory_kinds=[item.kind for item in automatic_memories],
        )
        if tts_start is not None:
            self.trace.record(
                "tts",
                tts_start,
                first_audio_ms=(first_audio_ns - tts_start) / 1_000_000 if first_audio_ns else None,
                semantic_first_audio_ms=(semantic_first_audio_ns - tts_start) / 1_000_000
                if semantic_first_audio_ns
                else None,
                frames=sequence,
                first_phrase_chars=first_phrase_chars,
                tts_requests=tts_requests,
                tts_request_details=sorted(
                    tts_request_details, key=lambda item: int(item["index"])
                ),
                server_pcm_queue_high_water_frames=server_pcm_queue_high_water,
                server_pcm_queue_capacity_frames=SERVER_PCM_BUDGET_FRAMES,
                overlapped_llm_ms=max(0.0, (llm_end - tts_start) / 1_000_000),
            )
        if response:
            self._recent_turns.append((transcript, response))
        yield PipelineEvent(control=control("response.text.done", text=response))
        final_state = (
            "awaiting_approval"
            if self.llm.pending_tool_approval() is not None
            else "idle"
        )
        yield PipelineEvent(control=control("session.state", state=final_state))
