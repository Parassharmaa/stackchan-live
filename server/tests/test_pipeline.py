import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from stackchan_agent.memory import MemoryStore
from stackchan_agent.pipeline import (
    CascadePipeline,
    meaningful_transcript,
    take_speakable_phrase,
)
from stackchan_agent.providers import (
    LLMProvider,
    MockLLM,
    MockSTT,
    MockTTS,
    PendingToolApproval,
    TTSProvider,
    TurnContext,
)
from stackchan_agent.telemetry import TraceRecorder
from stackchan_agent.tools import invoke_tool, plan_tools, unsupported_action_feedback


class FastSentenceLLM(LLMProvider):
    async def generate(self, context: TurnContext) -> AsyncIterator[str]:
        for index in range(100):
            yield f"sentence {index}."


class BlockingTTS(TTSProvider):
    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        await asyncio.Event().wait()
        yield b""


class CapturingLLM(LLMProvider):
    def __init__(self) -> None:
        self.contexts: list[TurnContext] = []

    async def generate(self, context: TurnContext) -> AsyncIterator[str]:
        self.contexts.append(context)
        yield "A short reply."


class TwoSentenceLLM(LLMProvider):
    async def generate(self, context: TurnContext) -> AsyncIterator[str]:
        yield "First sentence."
        yield " Second sentence."


class GatedThreeSentenceLLM(LLMProvider):
    def __init__(self) -> None:
        self.finish = asyncio.Event()

    async def generate(self, context: TurnContext) -> AsyncIterator[str]:
        yield "First sentence."
        yield " Second sentence."
        await self.finish.wait()
        yield " Third sentence."


class CapturingTTS(TTSProvider):
    def __init__(self) -> None:
        self.requests: list[str] = []

    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        self.requests.append(text)
        yield b"\x00\x00" * 480


class SignallingTTS(CapturingTTS):
    def __init__(self) -> None:
        super().__init__()
        self.second_request_started = asyncio.Event()

    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        self.requests.append(text)
        if len(self.requests) == 2:
            self.second_request_started.set()
        yield b"\x00\x00" * 480


class ManyFrameTTS(CapturingTTS):
    async def synthesize(self, text: str, language: str) -> AsyncIterator[bytes]:
        self.requests.append(text)
        for _ in range(120):
            yield b"\x00\x00" * 480


class ApprovalPromptLLM(LLMProvider):
    def __init__(self) -> None:
        self.pending = True

    async def generate(self, context: TurnContext) -> AsyncIterator[str]:
        self.pending = True
        yield "Allow the calendar action? Say approve or deny."

    def pending_tool_approval(self) -> PendingToolApproval | None:
        if not self.pending:
            return None
        return PendingToolApproval(
            request_id="approval_1",
            tool_name="calendar__create_event",
            action_summary="title Project sync",
            challenge="47",
            seconds_remaining=30.0,
        )

    def blocks_normal_turn(self) -> bool:
        return self.pending


@pytest.mark.asyncio
async def test_mock_pipeline_emits_transcript_audio_and_idle(tmp_path: Path) -> None:
    pipeline = CascadePipeline(
        MockSTT("こんにちは"),
        MockLLM(),
        MockTTS(),
        MemoryStore(tmp_path / "memory.sqlite3"),
        TraceRecorder(tmp_path / "traces", trace_id="test"),
    )
    events = [event async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000)]
    controls = [event.control.type for event in events if event.control]
    audio = [event.audio for event in events if event.audio]

    assert "transcript.final" in controls
    assert "response.text.done" in controls
    assert controls[-1] == "session.state"
    assert audio
    assert audio[0].sequence == 0


@pytest.mark.asyncio
async def test_pipeline_surfaces_approval_without_tool_input_and_waits(
    tmp_path: Path,
) -> None:
    pipeline = CascadePipeline(
        MockSTT("create the event"),
        ApprovalPromptLLM(),
        MockTTS(),
        MemoryStore(tmp_path / "memory.sqlite3"),
        TraceRecorder(tmp_path / "traces", trace_id="approval-test"),
    )

    events = [event async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000)]
    controls = [event.control for event in events if event.control]
    requested = next(item for item in controls if item.type == "approval.requested")

    assert requested.request_id == "approval_1"
    assert requested.payload == {
        "tool_name": "calendar__create_event",
        "action_summary": "title Project sync",
        "challenge": "47",
        "timeout_seconds": 30.0,
    }
    assert controls[-1].payload["state"] == "awaiting_approval"


@pytest.mark.asyncio
async def test_pending_approval_bypasses_local_memory_and_motion_routes(
    tmp_path: Path,
) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    pipeline = CascadePipeline(
        MockSTT("Move your head left and remember my color is blue."),
        ApprovalPromptLLM(),
        MockTTS(),
        memory,
        TraceRecorder(tmp_path / "traces", trace_id="approval-isolation-test"),
    )

    events = [event async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000)]
    controls = [event.control for event in events if event.control]

    assert not any(item.type == "motion.set" for item in controls)
    assert not any(item.type == "memory.stored" for item in controls)
    assert memory.list_recent(include_episodes=True) == []


@pytest.mark.asyncio
async def test_cancel_does_not_deadlock_when_phrase_queue_is_full(tmp_path: Path) -> None:
    pipeline = CascadePipeline(
        MockSTT("hello"),
        FastSentenceLLM(),
        BlockingTTS(),
        MemoryStore(tmp_path / "memory.sqlite3"),
        TraceRecorder(tmp_path / "traces", trace_id="cancel-test"),
    )

    async def consume() -> None:
        async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000):
            if event.control and event.control.type == "response.text.delta":
                pipeline.cancel()

    await asyncio.wait_for(consume(), timeout=0.5)


@pytest.mark.asyncio
async def test_profile_commits_before_an_interrupted_response(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    pipeline = CascadePipeline(
        MockSTT("I like hojicha."),
        FastSentenceLLM(),
        BlockingTTS(),
        memory,
        TraceRecorder(tmp_path / "traces", trace_id="interrupted-profile-test"),
    )

    async def consume() -> None:
        async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000):
            if event.control and event.control.type == "response.text.delta":
                pipeline.cancel()

    await asyncio.wait_for(consume(), timeout=0.5)

    memories = memory.list_recent(include_episodes=True)
    assert [(item.kind, item.content) for item in memories] == [
        ("profile", "The user likes hojicha.")
    ]


@pytest.mark.asyncio
async def test_pipeline_passes_bounded_recent_turns_to_next_response(tmp_path: Path) -> None:
    llm = CapturingLLM()
    stt = MockSTT("first question")
    pipeline = CascadePipeline(
        stt,
        llm,
        MockTTS(),
        MemoryStore(tmp_path / "memory.sqlite3"),
        TraceRecorder(tmp_path / "traces", trace_id="history-test"),
    )

    _ = [event async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000)]
    stt.transcript = "follow-up question"
    _ = [event async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000)]

    assert llm.contexts[0].recent_turns == []
    assert llm.contexts[1].recent_turns == [("first question", "A short reply.")]


@pytest.mark.asyncio
async def test_pipeline_renders_first_sentence_then_prefetches_remainder(
    tmp_path: Path,
) -> None:
    tts = CapturingTTS()
    pipeline = CascadePipeline(
        MockSTT("tell me something"),
        TwoSentenceLLM(),
        tts,
        MemoryStore(tmp_path / "memory.sqlite3"),
        TraceRecorder(tmp_path / "traces", trace_id="single-tts-test"),
    )

    _ = [event async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000)]

    assert tts.requests == ["First sentence.", "Second sentence."]


@pytest.mark.asyncio
async def test_pipeline_prefetches_next_tts_phrase_before_llm_finishes(
    tmp_path: Path,
) -> None:
    llm = GatedThreeSentenceLLM()
    tts = SignallingTTS()
    pipeline = CascadePipeline(
        MockSTT("tell me something longer"),
        llm,
        tts,
        MemoryStore(tmp_path / "memory.sqlite3"),
        TraceRecorder(tmp_path / "traces", trace_id="tts-playout-queue-test"),
    )

    turn = asyncio.create_task(
        _collect_pipeline_events(pipeline, b"\x00\x00" * 320, 16_000)
    )
    await asyncio.wait_for(tts.second_request_started.wait(), timeout=0.5)

    assert tts.requests == ["First sentence.", "Second sentence."]
    assert not turn.done()

    llm.finish.set()
    await asyncio.wait_for(turn, timeout=0.5)


@pytest.mark.asyncio
async def test_pipeline_slow_consumer_respects_total_pcm_budget(
    tmp_path: Path,
) -> None:
    trace = TraceRecorder(
        tmp_path / "traces", trace_id="tts-total-playout-budget-test"
    )
    pipeline = CascadePipeline(
        MockSTT("tell me something longer"),
        TwoSentenceLLM(),
        ManyFrameTTS(),
        MemoryStore(tmp_path / "memory.sqlite3"),
        trace,
    )

    async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000):
        if event.audio is not None:
            await asyncio.sleep(0.001)

    spans = [json.loads(line) for line in trace.path.read_text().splitlines()]
    tts = next(span for span in spans if span["name"] == "tts")["attributes"]

    assert tts["server_pcm_queue_capacity_frames"] == 96
    assert 90 <= tts["server_pcm_queue_high_water_frames"] <= 96


async def _collect_pipeline_events(
    pipeline: CascadePipeline, audio: bytes, sample_rate: int
) -> list:
    return [event async for event in pipeline.run_turn(audio, sample_rate)]


@pytest.mark.asyncio
async def test_pipeline_profiles_and_recalls_a_preference_automatically(
    tmp_path: Path,
) -> None:
    llm = CapturingLLM()
    stt = MockSTT("My favorite color is lavender.")
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    pipeline = CascadePipeline(
        stt,
        llm,
        MockTTS(),
        memory,
        TraceRecorder(tmp_path / "traces", trace_id="profile-memory-test"),
    )

    _ = [event async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000)]
    stt.transcript = "What is my favorite color?"
    _ = [event async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000)]

    assert memory.list_recent()[0].kind == "profile"
    assert llm.contexts[1].memories == [
        "The user's favorite color is lavender."
    ]


@pytest.mark.asyncio
async def test_pipeline_rejects_explicit_sensitive_memory_without_crashing(
    tmp_path: Path,
) -> None:
    llm = CapturingLLM()
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    pipeline = CascadePipeline(
        MockSTT("Please remember that my password is a test-only phrase."),
        llm,
        MockTTS(),
        memory,
        TraceRecorder(tmp_path / "traces", trace_id="sensitive-memory-test"),
    )

    events = [event async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000)]
    controls = [event.control for event in events if event.control]

    rejected = next(item for item in controls if item.type == "memory.rejected")
    assert rejected.payload == {"category": "credential"}
    assert memory.list_recent() == []
    assert llm.contexts[0].action_results == [
        "sensitive credential information was not stored"
    ]


def test_device_tool_planner_is_bilingual_and_bounded() -> None:
    english = plan_tools("Please turn your head left and make the lights blue", "en")
    japanese = plan_tools("音楽をかけてダンスして", "ja")

    assert [plan.name for plan in english] == ["move_head", "set_lights"]
    assert english[0].arguments == {
        "yaw_deg": -24.0,
        "pitch_deg": 45.0,
        "duration_ms": 550,
    }
    assert "execution is unconfirmed" in english[0].result_summary
    assert japanese[0].name == "play_routine"
    assert japanese[0].arguments == {"name": "dance", "intensity": 0.75, "music": True}


def test_recent_live_head_and_light_phrases_route_to_physical_tools() -> None:
    head = plan_tools("Head towards left.", "en")
    blinking = plan_tools("Can you actually blinks your light very fast?", "en")
    default_lights = plan_tools("Okay, so make your lights do it then.", "en")

    assert [item.name for item in head] == ["move_head"]
    assert head[0].arguments["yaw_deg"] == -24.0
    assert [item.name for item in blinking] == ["set_lights"]
    assert blinking[0].arguments == {
        "red": 30,
        "green": 90,
        "blue": 255,
        "brightness": 0.25,
        "animation": "twinkle",
    }
    assert [item.name for item in default_lights] == ["set_lights"]


@pytest.mark.asyncio
async def test_physical_command_does_not_inject_unrelated_coffee_memory(
    tmp_path: Path,
) -> None:
    llm = CapturingLLM()
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    memory.record_episode(
        "Can you hear me?",
        "Yes, and your favorite drink is coffee.",
        "en",
    )
    pipeline = CascadePipeline(
        MockSTT("Head towards left."),
        llm,
        MockTTS(),
        memory,
        TraceRecorder(tmp_path / "traces", trace_id="action-memory-isolation"),
    )

    _ = [event async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000)]

    assert llm.contexts[0].memories == []
    assert llm.contexts[0].action_results == [
        "move_head was not physically confirmed before the timeout"
    ]


@pytest.mark.asyncio
async def test_camera_tool_turn_does_not_persist_visual_details_as_an_episode(
    tmp_path: Path,
) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    pipeline = CascadePipeline(
        MockSTT("Look at me and tell me how I look."),
        CapturingLLM(),
        MockTTS(),
        memory,
        TraceRecorder(tmp_path / "traces", trace_id="camera-memory-isolation"),
    )

    _ = [event async for event in pipeline.run_turn(b"\x00\x00" * 320, 16_000)]

    assert memory.list_recent(include_episodes=True) == []
    memory.close()


def test_japanese_motion_result_never_claims_completion() -> None:
    motion = plan_tools("左を向いて", "ja")[0]

    assert motion.name == "move_head"
    assert "実行は未確認" in motion.result_summary
    assert "左を向くね" in motion.result_summary


def test_motion_directions_are_complete_canonical_poses() -> None:
    expected = {
        "look left": (-24.0, 45.0),
        "look right": (24.0, 45.0),
        "look up": (0.0, 25.0),
        "look down": (0.0, 65.0),
        "look straight": (0.0, 45.0),
    }

    for transcript, (yaw, pitch) in expected.items():
        motion = plan_tools(transcript, "en")[0]
        assert motion.arguments["yaw_deg"] == yaw
        assert motion.arguments["pitch_deg"] == pitch


def test_bilingual_semantic_gestures_use_one_verified_gesture_tool() -> None:
    cases = {
        ("Please nod.", "en"): "nod",
        ("Could you double-nod?", "en"): "double_nod",
        ("Shake your head no.", "en"): "shake_no",
        ("Please bow.", "en"): "bow",
        ("Look attentive.", "en"): "attentive",
        ("うなずいてください。", "ja"): "nod",
        ("二回うなずいてください。", "ja"): "double_nod",
        ("首を横に振ってください。", "ja"): "shake_no",
        ("お辞儀してください。", "ja"): "bow",
    }
    for (transcript, language), gesture in cases.items():
        planned = plan_tools(transcript, language)
        assert [item.name for item in planned] == ["perform_gesture"]
        assert planned[0].arguments == {"name": gesture, "intensity": 0.7}


@pytest.mark.asyncio
async def test_gesture_tool_emits_one_semantic_device_control() -> None:
    command = await invoke_tool(
        "perform_gesture", {"name": "double_nod", "intensity": 0.68}
    )
    assert command.type == "gesture.play"
    assert command.payload == {"name": "double_nod", "intensity": 0.68}


def test_motion_planner_accepts_bounded_spoken_angles() -> None:
    english = plan_tools("Please move your head to yaw 12 degrees and pitch 50 degrees", "en")
    japanese = plan_tools("頭を左に18度動かして", "ja")
    clamped = plan_tools("Please turn your head right 90 degrees", "en")

    assert english[0].arguments == {
        "yaw_deg": 12.0,
        "pitch_deg": 50.0,
        "duration_ms": 550,
    }
    assert japanese[0].arguments["yaw_deg"] == -18.0
    assert clamped[0].arguments["yaw_deg"] == 35.0


def test_device_tool_planner_rejects_non_command_mentions() -> None:
    assert plan_tools("What music do you like?", "en") == []
    assert plan_tools("I left my keys at home", "en") == []
    assert plan_tools("You are right about that", "en") == []
    assert plan_tools("I wonder what you think about this", "en") == []
    assert plan_tools("She is sad about the weather", "en") == []
    assert plan_tools("Congratulations to your creator", "en") == []
    assert plan_tools("I am curious about your hardware", "en") == []
    assert plan_tools("音楽は好きですか？", "ja") == []
    assert plan_tools("左に鍵を置きました", "ja") == []

    assert plan_tools("Please play some music", "en")[0].name == "play_routine"
    assert plan_tools("Stack-chan, dance with music", "en")[0].name == "play_routine"
    assert plan_tools("Please comfort me", "en")[0].name == "play_routine"
    assert plan_tools("Stack-chan, celebrate with me", "en")[0].name == "play_routine"
    assert plan_tools("Show me a curious expression", "en")[0].name == "play_routine"
    assert plan_tools("音楽をかけて", "ja")[0].name == "play_routine"
    assert plan_tools("Please play a longer song", "en")[0].arguments == {
        "name": "dance",
        "intensity": 0.75,
        "music": True,
    }
    assert plan_tools("もう少し長い音楽を流して", "ja")[0].arguments == {
        "name": "dance",
        "intensity": 0.75,
        "music": True,
    }
    assert plan_tools("おやすみの音楽を流してください", "ja")[0].arguments == {
        "name": "good_night",
        "intensity": 0.75,
        "music": True,
    }
    assert plan_tools("Please play bedtime music", "en")[0].arguments == {
        "name": "good_night",
        "intensity": 0.75,
        "music": True,
    }


def test_six_bilingual_music_styles_route_to_distinct_embodied_presets() -> None:
    cases = (
        ("Play a victory fanfare.", "en", "celebrate"),
        ("Play an upbeat chiptune.", "en", "dance"),
        ("Play a sunrise song.", "en", "wake_up"),
        ("Play some gentle relaxing music.", "en", "comfort"),
        ("Play lo-fi focus music.", "en", "focus"),
        ("Play a lullaby.", "en", "good_night"),
        ("ファンファーレを流して。", "ja", "celebrate"),
        ("ローファイの集中用の曲を流して。", "ja", "focus"),
        ("落ち着く曲をかけて。", "ja", "comfort"),
        ("リラックスできる音楽を流して。", "ja", "comfort"),
        ("子守唄を歌って。", "ja", "good_night"),
    )
    for transcript, language, routine in cases:
        planned = plan_tools(transcript, language)
        assert [(item.name, item.arguments) for item in planned] == [
            (
                "play_routine",
                {"name": routine, "intensity": 0.75, "music": True},
            )
        ]


def test_natural_face_commands_use_the_fast_correlated_device_lane() -> None:
    english = plan_tools("Can you make a sad face?", "en")
    japanese = plan_tools("悲しい顔を見せて", "ja")
    surprising = plan_tools("Can you make a surprising face?", "en")

    assert len(english) == 1
    assert english[0].name == "set_face"
    assert english[0].arguments == {
        "state": "idle",
        "emotion": "sad",
        "intensity": 0.85,
    }
    assert len(japanese) == 1
    assert japanese[0].name == "set_face"
    assert japanese[0].arguments["emotion"] == "sad"
    assert surprising[0].arguments["emotion"] == "surprised"


def test_explicit_bilingual_photo_requests_use_the_camera_lane() -> None:
    english = plan_tools("Please take a photo of me.", "en")
    japanese = plan_tools("写真を撮ってください。", "ja")

    assert [(item.name, item.arguments) for item in english] == [
        ("move_head", {"yaw_deg": 0.0, "pitch_deg": 45.0, "duration_ms": 550}),
        ("capture_photo", {"quality": 70}),
    ]
    assert [(item.name, item.arguments) for item in japanese] == [
        ("move_head", {"yaw_deg": 0.0, "pitch_deg": 45.0, "duration_ms": 550}),
        ("capture_photo", {"quality": 70}),
    ]


def test_visual_inspection_requests_run_the_correlated_multistep_camera_lane() -> None:
    cases = (
        ("Look at me and tell me how I'm looking today.", "en"),
        ("How am I looking today?", "en"),
        ("What do I look like?", "en"),
        ("Just look at it. Here it is.", "en"),
        ("What is this?", "en"),
        ("What am I holding?", "en"),
        ("私を見て、今日の私はどう見える？", "ja"),
        ("私の服装はどう？", "ja"),
        ("これを見て。", "ja"),
        ("これ何？", "ja"),
    )

    for transcript, language in cases:
        planned = plan_tools(transcript, language)
        assert [(item.name, item.arguments) for item in planned] == [
            ("move_head", {"yaw_deg": 0.0, "pitch_deg": 45.0, "duration_ms": 550}),
            ("capture_photo", {"quality": 70}),
        ]


def test_contextual_visual_handoff_uses_recent_dialogue_without_magic_words() -> None:
    recent_turns = [
        (
            "I made a small 3D-printed object. Do you want to see it?",
            "Yes, show it to me and hold it where my camera can see it.",
        )
    ]

    planned = plan_tools("Yeah, here it is.", "en", recent_turns=recent_turns)

    assert [item.name for item in planned] == ["move_head", "capture_photo"]
    assert plan_tools("Yeah, here it is.", "en") == []
    assert plan_tools("I made an object. Would you like to see it?", "en") == []


def test_japanese_contextual_visual_handoff_requires_recent_showing_context() -> None:
    recent_turns = [("これを見たい？", "見せて。カメラの前に持ってきてね。")]

    assert [
        item.name
        for item in plan_tools("うん、これだよ。", "ja", recent_turns=recent_turns)
    ] == ["move_head", "capture_photo"]
    assert plan_tools("うん、これだよ。", "ja") == []


def test_photo_offer_confirmation_authorizes_exactly_one_correlated_still() -> None:
    english_context = [
        ("Camera.", "Would you like me to take one photo of what's in front of me?")
    ]
    japanese_context = [
        ("カメラ。", "前にあるものを写真で一枚撮りましょうか？")
    ]
    still_context = [
        ("カメラを使う提案をして。", "Would you like me to take one camera still?")
    ]
    still_photo_context = [
        ("Camera.", "Would you like me to take one still photo to check the view?")
    ]

    assert [
        item.name
        for item in plan_tools("Yes.", "en", recent_turns=english_context)
    ] == ["move_head", "capture_photo"]
    assert [
        item.name
        for item in plan_tools("はい。", "ja", recent_turns=english_context)
    ] == ["move_head", "capture_photo"]
    assert [
        item.name
        for item in plan_tools("お願いします。", "ja", recent_turns=japanese_context)
    ] == ["move_head", "capture_photo"]
    assert [item.name for item in plan_tools("はい。", "ja", recent_turns=still_context)] == [
        "move_head",
        "capture_photo",
    ]
    assert [item.name for item in plan_tools("Yes.", "en", recent_turns=still_photo_context)] == [
        "move_head",
        "capture_photo",
    ]
    assert plan_tools("Yes.", "en") == []
    assert plan_tools("はい。", "ja", recent_turns=[("元気？", "元気です。")]) == []
    assert plan_tools(
        "Yes.",
        "en",
        recent_turns=[("Camera.", "I cannot take a photo right now.")],
    ) == []


def test_bilingual_daily_routines_use_distinct_embodied_presets() -> None:
    cases = (
        ("Good morning!", "en", "wake_up", False),
        ("Let's focus.", "en", "focus", False),
        ("Good night.", "en", "good_night", False),
        ("Please play a lullaby.", "en", "good_night", True),
        ("おはよう。", "ja", "wake_up", False),
        ("集中モードにして。", "ja", "focus", False),
        ("おやすみ。", "ja", "good_night", False),
    )

    for transcript, language, routine, music in cases:
        planned = plan_tools(transcript, language)
        assert [(item.name, item.arguments) for item in planned] == [
            (
                "play_routine",
                {"name": routine, "intensity": 0.75, "music": music},
            )
        ]


def test_camera_lane_requires_an_explicit_capture_request() -> None:
    assert plan_tools("Do you have a camera?", "en") == []
    assert plan_tools("What can you see?", "en") == []
    assert plan_tools("What does this room look like?", "en") == []
    assert plan_tools("カメラはありますか？", "ja") == []


def test_bilingual_crying_face_uses_the_real_device_expression() -> None:
    english = plan_tools("Can you make a crying face?", "en")
    japanese = plan_tools("泣いている顔を見せて", "ja")

    assert english[0].arguments == {
        "state": "idle",
        "emotion": "crying",
        "intensity": 0.95,
    }
    assert japanese[0].arguments == english[0].arguments
    assert unsupported_action_feedback("Can you make a crying face?", "en") == []


def test_face_planner_does_not_turn_emotion_mentions_into_device_actions() -> None:
    assert plan_tools("She is sad about the weather", "en") == []
    assert plan_tools("Why do people look sad in that movie?", "en") == []
    assert plan_tools("悲しい顔の絵について教えて", "ja") == []


def test_non_speech_transcripts_are_rejected() -> None:
    assert not meaningful_transcript("")
    assert not meaningful_transcript("(dog barks)")
    assert not meaningful_transcript("*cough*")
    assert not meaningful_transcript("[BLANK_AUDIO]")
    assert not meaningful_transcript("[Crying]")
    assert not meaningful_transcript("ទ្្្្្្្្្")
    assert meaningful_transcript("こんにちは")


def test_speakable_phrase_starts_early_on_english_word_boundary() -> None:
    phrase, remainder = take_speakable_phrase(
        "Here is a friendly little answer that is still coming", "en"
    )

    assert phrase == ""
    assert remainder == "Here is a friendly little answer that is still coming"


def test_first_speakable_phrase_uses_lower_latency_boundary() -> None:
    phrase, remainder = take_speakable_phrase(
        "Here is a friendly little answer that is still coming",
        "en",
        first_phrase=True,
    )

    assert phrase == ""
    assert remainder == "Here is a friendly little answer that is still coming"


def test_first_speakable_phrase_does_not_emit_tiny_streaming_enumeration() -> None:
    phrase, remainder = take_speakable_phrase("One.", "en", first_phrase=True)

    assert phrase == ""
    assert remainder == "One."

    phrase, remainder = take_speakable_phrase(
        "One... two... three.", "en", first_phrase=True
    )

    assert phrase == "One... two... three."
    assert remainder == ""


def test_short_complete_reply_is_emitted_when_stream_finishes() -> None:
    phrase, remainder = take_speakable_phrase(
        "Done.", "en", first_phrase=True, force=True
    )

    assert phrase == "Done."
    assert remainder == ""


def test_speakable_phrase_starts_early_on_japanese_clause_boundary() -> None:
    phrase, remainder = take_speakable_phrase(
        "それはとても面白いお話だね、もっと聞かせてね。", "ja"
    )

    assert phrase == "それはとても面白いお話だね、もっと聞かせてね。"
    assert remainder == ""
