import asyncio

from stackchan_agent.config import Settings
from stackchan_agent.maai_runtime import MaaiBehaviorArbiter, MaaiRuntime, _probability


def settings(**overrides: object) -> Settings:
    return Settings(
        provider="mock",
        maai_enabled=True,
        maai_shadow_mode=True,
        **overrides,
    )


def test_probability_flattens_tensor_shaped_json_without_trusting_nan() -> None:
    assert _probability([[0.2, 0.8], [0.1]]) == 0.8
    assert _probability({"score": float("nan")}) == 0.0


def test_arbiter_emits_sparse_japanese_nod_and_respects_cooldown() -> None:
    arbiter = MaaiBehaviorArbiter(settings())
    result = {
        "nod_jp": {
            "p_nod_short": [0.1],
            "p_nod_long": [0.84],
            "p_nod_long_p": [0.2],
        }
    }
    first = arbiter.decide(
        result,
        language="ja",
        user_speaking=True,
        robot_speaking=False,
        conversation_suspended=False,
        motion_busy=False,
        now=10.0,
    )
    assert first is not None
    assert first.behavior == "nod_long"
    assert first.pitch_deg == 57.0
    assert (
        arbiter.decide(
            result,
            language="ja",
            user_speaking=True,
            robot_speaking=False,
            conversation_suspended=False,
            motion_busy=False,
            now=10.5,
        )
        is None
    )


def test_arbiter_does_not_apply_japanese_nod_model_to_english() -> None:
    arbiter = MaaiBehaviorArbiter(settings())
    result = {"nod_jp": {"p_nod_short": [0.99]}}
    assert (
        arbiter.decide(
            result,
            language="en",
            user_speaking=True,
            robot_speaking=False,
            conversation_suspended=False,
            motion_busy=False,
            now=10.0,
        )
        is None
    )


def test_arbiter_suppresses_backchannel_during_robot_speech_or_codex_mode() -> None:
    result = {"backchannel_en": {"p_bc": [0.91]}}
    for speaking, suspended in ((True, False), (False, True)):
        decision = MaaiBehaviorArbiter(settings()).decide(
            result,
            language="en",
            user_speaking=True,
            robot_speaking=speaking,
            conversation_suspended=suspended,
            motion_busy=False,
            now=10.0,
        )
        assert decision is None


def test_runtime_drops_stale_audio_instead_of_growing_latency() -> None:
    runtime = MaaiRuntime(settings())
    frame = b"\x01\x00" * 320
    render_frame = b"\x02\x00" * 320
    for _ in range(5):
        runtime.feed_render(render_frame)
        runtime.feed_capture(frame)
    for _ in range(5):
        runtime.feed_capture(frame)
    assert runtime.frames_submitted == 2
    assert runtime.frames_dropped == 1
    sequence, mic, render, _ = runtime._frames.get_nowait()
    assert sequence == 2
    assert mic == frame * 5
    assert render == bytes(len(frame) * 5)


def test_runtime_result_queue_returns_only_latest_prediction() -> None:
    runtime = MaaiRuntime(settings())
    runtime._results.put_nowait({"sequence": 1})
    assert runtime.take_result() == {"sequence": 1}
    assert runtime.take_result() is None


def test_disabled_runtime_is_a_noop() -> None:
    runtime = MaaiRuntime(Settings(provider="mock", maai_enabled=False))
    runtime.feed_capture(b"\x00\x00" * 320)
    assert runtime.frames_submitted == 0
    asyncio.run(runtime.start())
    assert runtime._process is None
