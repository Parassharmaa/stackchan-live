import asyncio
import io
import struct
import wave

import pytest

from stackchan_agent.local_providers import (
    BilingualWhisperSTT,
    SupertonicTTS,
    WhisperServerSTT,
    WhisperTranscription,
    bounded_response_piece,
    detect_language,
    normalize_tts_prosody,
    response_is_complete,
    response_matches_language,
    response_sentence_budget,
    sanitize_for_tts,
)
from stackchan_agent.providers import STTProvider, TurnContext


class StubSTT(STTProvider):
    def __init__(self, result: tuple[str, str], delay: float = 0) -> None:
        self.result = result
        self.delay = delay
        self.cancelled = False
        self.called = False

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> tuple[str, str]:
        self.called = True
        try:
            await asyncio.sleep(self.delay)
            return self.result
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class StubConfidenceSTT(WhisperServerSTT):
    def __init__(self, text: str, confidence: float, *, language: str = "ja") -> None:
        super().__init__("http://unused", confidence=True)
        self.result = WhisperTranscription(text, language, confidence)
        self.called = False

    async def transcribe_detailed(self, pcm16: bytes, sample_rate: int) -> WhisperTranscription:
        self.called = True
        return self.result


class CountingSupertonic(SupertonicTTS):
    def __init__(self) -> None:
        super().__init__("http://unused")
        self.renders = 0

    async def _render(self, text: str, language: str) -> tuple[bytes, ...]:
        self.renders += 1
        return (f"{language}:{text}".encode(),)


def test_detect_language_handles_english_japanese_and_code_switching() -> None:
    assert detect_language("Please look to the left") == "en"
    assert detect_language("こんにちは、元気ですか") == "ja"
    assert detect_language("Stack-chan、左を向いて") == "ja"


def test_response_language_and_joke_completion_guards() -> None:
    assert response_matches_language("元気だよ！", "ja")
    assert response_matches_language("1、2、3。", "ja")
    assert not response_matches_language("Why did the chicken cross?", "ja")
    assert not response_is_complete("なぜ鶏は道を渡ったの？", "ja", joke=True)
    assert response_is_complete("鶏は向こう側へ行きたかったから！", "ja", joke=True)


def test_sanitize_for_tts_removes_emoji_but_keeps_japanese() -> None:
    assert sanitize_for_tts("覚えてるよ！ ☕😊\ufe0f") == "覚えてるよ！"


def test_sanitize_for_tts_preserves_spoken_technical_symbols() -> None:
    assert sanitize_for_tts("C++ costs $5 and x+y=3.") == "C++ costs $5 and x+y=3."


def test_tts_prosody_normalizes_ellipsis_enumerations() -> None:
    assert (
        normalize_tts_prosody("One… two... three…… four.", "en")
        == "One, two, three, four."
    )
    assert normalize_tts_prosody("いち……に…さん。", "ja") == "いち、に、さん。"


@pytest.mark.asyncio
async def test_supertonic_preloaded_phrase_reuses_pcm_without_rendering_again() -> None:
    provider = CountingSupertonic()

    await provider.preload("Okay!", "en")
    first = [frame async for frame in provider.synthesize("Okay!", "en")]
    second = [frame async for frame in provider.synthesize("Okay!", "en")]

    assert first == second == [b"en:Okay!"]
    assert provider.renders == 1


def test_supertonic_trims_silent_padding_around_cached_interjection() -> None:
    silence = struct.pack("<480h", *([0] * 480))
    speech = struct.pack("<480h", *([2000] * 480))
    frames = (silence,) * 10 + (speech,) * 4 + (silence,) * 10

    trimmed = SupertonicTTS._trim_interjection(frames)

    assert trimmed == (silence,) * 3 + (speech,) * 4 + (silence,) * 3


def test_streamed_response_is_bounded_to_two_concise_sentences() -> None:
    assert bounded_response_piece("Hello ", "there. Extra", "en") == (
        "there. Extra",
        False,
    )
    assert bounded_response_piece("Hello there. ", "Second answer. Extra", "en") == (
        "Second answer.",
        True,
    )
    japanese = "あ" * 239
    assert bounded_response_piece(japanese, "いう", "ja") == ("い", True)


def test_joke_stream_keeps_punchline_after_setup_question() -> None:
    assert bounded_response_piece(
        "Why did the robot ",
        "dance?",
        "en",
        allow_question_setup=True,
        max_sentences=1,
    ) == ("dance?", False)
    assert bounded_response_piece(
        "Why did the robot dance? ",
        "It had good algorithms! Extra",
        "en",
        allow_question_setup=True,
        max_sentences=1,
    ) == ("It had good algorithms!", True)


def test_response_depth_adapts_to_spoken_intent() -> None:
    assert response_sentence_budget(TurnContext("Hello there", "en", [])) == 3
    assert response_sentence_budget(TurnContext("Explain how this works", "en", [])) == 4
    assert response_sentence_budget(TurnContext("Answer briefly", "en", [])) == 1
    assert response_sentence_budget(TurnContext("Count from one to ten", "en", [])) == 1
    assert response_sentence_budget(TurnContext("1から10まで数えて", "ja", [])) == 1
    assert response_sentence_budget(
        TurnContext("Did the head move?", "en", [], ["motion completed"])
    ) == 2
    assert response_sentence_budget(
        TurnContext("What drink do I like?", "en", ["I like coffee"])
    ) == 2


def test_numeric_count_markers_do_not_end_streamed_response() -> None:
    assert bounded_response_piece("", "1. 2. 3. Done.", "en", max_sentences=1) == (
        "1. 2. 3. Done.",
        True,
    )
    assert bounded_response_piece("", "1。2。3。おわり！", "ja", max_sentences=1) == (
        "1。2。3。おわり！",
        True,
    )


def test_repeated_dots_are_a_spoken_pause_not_two_sentences() -> None:
    assert bounded_response_piece(
        "", "One.. Two.. Three.. Four.. Five..", "en", max_sentences=2
    ) == ("One.. Two.. Three.. Four.. Five..", False)


def test_whisper_server_encodes_mono_pcm_as_wav() -> None:
    payload = WhisperServerSTT._wav(b"\x01\x00\xff\xff" * 160, 16_000)
    with wave.open(io.BytesIO(payload), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16_000
        assert handle.getnframes() == 320


def test_whisper_server_keeps_domain_prompt_as_request_configuration() -> None:
    provider = WhisperServerSTT("http://127.0.0.1:8178", prompt="Stack-chan", language="ja")

    assert provider.prompt == "Stack-chan"
    assert provider.language == "ja"


@pytest.mark.asyncio
async def test_bilingual_router_returns_fast_english_and_cancels_slow_decode() -> None:
    fast = StubSTT(("hello", "en"))
    japanese = StubSTT(("こんにちは", "ja"), delay=1)

    assert await BilingualWhisperSTT(fast, japanese).transcribe(b"audio", 16_000) == (
        "hello",
        "en",
    )
    assert not japanese.called


@pytest.mark.asyncio
async def test_bilingual_router_selects_large_japanese_decode_without_small() -> None:
    fast = StubSTT(("上段", "ja"))
    japanese = StubSTT(("冗談", "ja"))

    assert await BilingualWhisperSTT(fast, japanese).transcribe(b"audio", 16_000) == (
        "冗談",
        "ja",
    )


@pytest.mark.asyncio
async def test_bilingual_router_accepts_confident_small_japanese_result() -> None:
    fast = StubSTT(("短い冗談", "ja"))
    small = StubConfidenceSTT("短いジョーク", -0.1)
    large = StubConfidenceSTT("短いジョークを言って", -0.01)
    router = BilingualWhisperSTT(fast, large, small, confidence_threshold=-0.18)

    assert await router.transcribe(b"audio", 16_000) == ("短いジョーク", "ja")
    assert not large.called
    assert router.last_route["route"] == "small"


@pytest.mark.asyncio
async def test_bilingual_router_falls_back_and_selects_higher_confidence() -> None:
    fast = StubSTT(("短い冗談", "ja"))
    small = StubConfidenceSTT("短いボックス", -0.25)
    large = StubConfidenceSTT("短いジョーク", -0.05)
    router = BilingualWhisperSTT(fast, large, small, confidence_threshold=-0.18)

    assert await router.transcribe(b"audio", 16_000) == ("短いジョーク", "ja")
    assert large.called
    assert router.last_route["route"] == "small_large"


@pytest.mark.asyncio
async def test_bilingual_router_retains_small_when_fallback_is_less_confident() -> None:
    fast = StubSTT(("短い冗談", "ja"))
    small = StubConfidenceSTT("短いジョーク", -0.25)
    large = StubConfidenceSTT("ストップ", -0.35)
    router = BilingualWhisperSTT(fast, large, small, confidence_threshold=-0.18)

    assert await router.transcribe(b"audio", 16_000) == ("短いジョーク", "ja")
    assert router.last_route["route"] == "small_retained"


@pytest.mark.asyncio
async def test_bilingual_router_recovers_caption_after_japanese_turn() -> None:
    fast = StubSTT(("短い冗談", "ja"))
    small = StubConfidenceSTT("短いジョークを言ってください", -0.05)
    large = StubConfidenceSTT("短いジョーク", -0.01)
    router = BilingualWhisperSTT(fast, large, small, confidence_threshold=-0.18)

    assert await router.transcribe(b"prompt", 16_000) == (
        "短いジョークを言ってください",
        "ja",
    )
    fast.result = ("(Sounds of speech.)", "en")
    small.called = False

    assert await router.transcribe(b"interrupt", 16_000) == (
        "短いジョークを言ってください",
        "ja",
    )
    assert router.last_route["caption_recovery"] is True


@pytest.mark.asyncio
async def test_bilingual_router_does_not_recover_initial_english_caption() -> None:
    fast = StubSTT(("(Music.)", "en"))
    small = StubConfidenceSTT("音楽をかけて", -0.05)
    large = StubConfidenceSTT("音楽", -0.01)
    router = BilingualWhisperSTT(fast, large, small, confidence_threshold=-0.18)

    assert await router.transcribe(b"audio", 16_000) == ("(Music.)", "en")
    assert not small.called


@pytest.mark.asyncio
async def test_bilingual_router_uses_small_model_for_confident_english_barge() -> None:
    fast = StubSTT(("wrong base result", "en"))
    japanese = StubConfidenceSTT("未使用", -0.1)
    small = StubConfidenceSTT(
        "Stop, tell me a short joke instead.", -0.08, language="en"
    )
    large = StubConfidenceSTT("unused", -0.01, language="en")
    router = BilingualWhisperSTT(
        fast,
        japanese,
        english_barge_fast=small,
        english_barge_robust=large,
    )

    router.prefer_robust_next_turn("en")

    assert await router.transcribe(b"audio", 16_000) == (
        "Stop, tell me a short joke instead.",
        "en",
    )
    assert not fast.called
    assert not large.called
    assert router.last_route["route"] == "english_barge_small"


@pytest.mark.asyncio
async def test_bilingual_router_falls_back_for_uncertain_english_barge() -> None:
    fast = StubSTT(("wrong base result", "en"))
    japanese = StubConfidenceSTT("未使用", -0.1)
    small = StubConfidenceSTT("It's all for my stead.", -0.35, language="en")
    large = StubConfidenceSTT(
        "Stop, tell me a short joke instead.", -0.09, language="en"
    )
    router = BilingualWhisperSTT(
        fast,
        japanese,
        english_barge_fast=small,
        english_barge_robust=large,
    )

    router.prefer_robust_next_turn("en")

    assert await router.transcribe(b"audio", 16_000) == (
        "Stop, tell me a short joke instead.",
        "en",
    )
    assert large.called
    assert router.last_route["route"] == "english_barge_large"


@pytest.mark.asyncio
async def test_bilingual_router_forces_japanese_after_verified_mixed_barge() -> None:
    fast = StubSTT(("Stop! short joke", "en"))
    small = StubConfidenceSTT("ストップ。短いジョークを言ってください。", -0.08)
    large = StubConfidenceSTT("短いジョークを言ってください。", -0.01)
    router = BilingualWhisperSTT(fast, large, small, confidence_threshold=-0.18)

    router.prefer_robust_next_turn("ja")

    assert await router.transcribe(b"audio", 16_000) == (
        "ストップ。短いジョークを言ってください。",
        "ja",
    )
    assert small.called
    assert not large.called


@pytest.mark.asyncio
async def test_bilingual_router_uses_specialized_japanese_post_barge_model() -> None:
    fast = StubSTT(("wrong base result", "en"))
    japanese = StubConfidenceSTT("条約を言って", -0.4)
    small = StubConfidenceSTT("条約を言って", -0.3)
    robust = StubConfidenceSTT("ジョークを言って", -0.17)
    router = BilingualWhisperSTT(
        fast,
        japanese,
        small,
        japanese_barge_robust=robust,
        confidence_threshold=-0.18,
    )

    router.prefer_robust_next_turn("ja")

    assert await router.transcribe(b"audio", 16_000) == (
        "ジョークを言って",
        "ja",
    )
    assert robust.called
    assert not fast.called
    assert not small.called
    assert not japanese.called
    assert router.last_route["route"] == "japanese_barge_large"
