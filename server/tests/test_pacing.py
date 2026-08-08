import asyncio

from stackchan_agent.app import (
    barge_confidence_is_sufficient,
    barge_probe_interval_ms,
    barge_turn_language,
    cross_language_barge_has_acoustic_support,
    cross_language_control_cue_can_confirm,
    decode_barge_probes,
    decode_raw_barge_continuation,
    has_barge_intent_evidence,
    has_explicit_barge_cue,
    has_prior_explicit_barge_cue,
    has_raw_control_cue_support,
    has_replacement_request_cue,
    independent_same_language_cue_confirmed,
    is_actionable_barge_continuation,
    is_meaningful_barge_transcript,
    is_stable_barge_language_switch,
    is_substantial_natural_barge,
    looks_like_render_echo,
    merge_audio_without_overlap,
    motion_capture_allows_speech_start,
    motion_capture_is_guarded,
    ordinary_capture_allows_speech_start,
    pairing_proof,
    pairing_response_matches,
    preliminary_cue_has_independent_support,
    rebase_pacing_after_gap,
    retain_recent_pcm16,
    retain_semantic_window_and_suffix,
    select_barge_probe,
    select_preliminary_barge_cue,
    semantic_cue_can_open_listening_window,
    should_accept_head_gesture,
    should_open_acoustic_listening_window,
    tool_result_is_terminal,
)
from stackchan_agent.config import Settings
from stackchan_agent.local_providers import WhisperTranscription


def test_motion_capture_guard_tracks_terminal_firmware_feedback() -> None:
    assert motion_capture_is_guarded({"motion-1"}, now=20.0, guarded_until=10.0)
    assert motion_capture_is_guarded(set(), now=20.0, guarded_until=20.35)
    assert not motion_capture_is_guarded(set(), now=20.36, guarded_until=20.35)


def test_motion_capture_rejects_servo_energy_until_voiced_tail_evidence() -> None:
    assert not motion_capture_allows_speech_start(
        {"motion-1"},
        guarded=True,
        clean_rms=8_000,
        minimum_rms=1_100,
        voice_frame=True,
    )
    assert not motion_capture_allows_speech_start(
        set(),
        guarded=True,
        clean_rms=1_900,
        minimum_rms=1_100,
        voice_frame=False,
    )
    assert motion_capture_allows_speech_start(
        set(),
        guarded=True,
        clean_rms=1_900,
        minimum_rms=1_100,
        voice_frame=True,
    )


def test_pending_turn_rejects_background_capture_until_playback_barge_lane() -> None:
    assert not ordinary_capture_allows_speech_start(
        turn_active=True, motion_allows_start=True
    )
    assert ordinary_capture_allows_speech_start(
        turn_active=False, motion_allows_start=True
    )
    assert not ordinary_capture_allows_speech_start(
        turn_active=False, motion_allows_start=False
    )


def test_bilingual_barge_probe_keeps_cross_language_japanese_cue_together() -> None:
    assert barge_probe_interval_ms("ja", english_ms=420, japanese_ms=700) == 700


def test_initial_barge_probe_outlasts_the_playback_reaction_delay() -> None:
    settings = Settings(provider="mock")

    assert settings.barge_in_preroll_ms >= 1200
    assert settings.barge_in_initial_probe_ms == 1100
    assert settings.barge_in_initial_probe_ms > settings.barge_in_probe_ja_ms
    assert settings.barge_in_probe_window_ms >= (
        settings.barge_in_preroll_ms + settings.barge_in_initial_probe_ms
    )
    assert settings.barge_in_confirmation_timeout_ms >= 6000


def test_physical_render_reference_uses_stricter_double_talk_onset() -> None:
    settings = Settings(provider="mock")

    assert settings.barge_in_physical_min_clean_ratio > settings.barge_in_min_clean_ratio
    assert settings.barge_in_physical_min_clean_rms > settings.barge_in_min_rms
    assert (
        settings.barge_in_physical_max_render_correlation
        < settings.barge_in_max_render_correlation
    )


def test_raw_control_cue_can_corroborate_cross_language_clean_cue() -> None:
    assert preliminary_cue_has_independent_support(
        raw_control_cue_supported=True,
        cross_language_acoustic_supported=False,
    )
    assert not preliminary_cue_has_independent_support(
        raw_control_cue_supported=False,
        cross_language_acoustic_supported=False,
    )


def test_duck_handoff_retains_only_recent_complete_pcm16_samples() -> None:
    audio = bytes(range(100))

    assert retain_recent_pcm16(audio, sample_rate=10, duration_ms=1000) == audio[-20:]
    assert retain_recent_pcm16(audio, sample_rate=10, duration_ms=1050) == audio[-20:]
    assert retain_recent_pcm16(audio, sample_rate=10, duration_ms=0) == b""


def test_strong_separated_double_talk_can_only_open_listening_window() -> None:
    assert should_open_acoustic_listening_window(
        enabled=True,
        raw_rms=17_862,
        clean_rms=18_307,
        render_correlation=0.218,
        minimum_raw_rms=12_000,
        maximum_render_correlation=0.30,
        minimum_clean_ratio=0.55,
        maximum_clean_ratio=1.25,
    )
    assert not should_open_acoustic_listening_window(
        enabled=True,
        raw_rms=7_736,
        clean_rms=8_150,
        render_correlation=0.160,
        minimum_raw_rms=12_000,
        maximum_render_correlation=0.30,
        minimum_clean_ratio=0.55,
        maximum_clean_ratio=1.25,
    )


def test_one_physical_head_pat_burst_is_coalesced_without_blocking_later_pats() -> None:
    assert should_accept_head_gesture(now=10.0, last_accepted_at=0.0, cooldown_seconds=2.5)
    assert not should_accept_head_gesture(
        now=10.58, last_accepted_at=10.0, cooldown_seconds=2.5
    )
    assert not should_accept_head_gesture(
        now=10.95, last_accepted_at=10.0, cooldown_seconds=2.5
    )
    assert should_accept_head_gesture(
        now=12.51, last_accepted_at=10.0, cooldown_seconds=2.5
    )
    assert not should_open_acoustic_listening_window(
        enabled=True,
        raw_rms=16_864,
        clean_rms=13_025,
        render_correlation=0.450,
        minimum_raw_rms=12_000,
        maximum_render_correlation=0.30,
        minimum_clean_ratio=0.55,
        maximum_clean_ratio=1.25,
    )
    assert not should_open_acoustic_listening_window(
        enabled=False,
        raw_rms=21_151,
        clean_rms=12_842,
        render_correlation=0.181,
        minimum_raw_rms=12_000,
        maximum_render_correlation=0.30,
        minimum_clean_ratio=0.55,
        maximum_clean_ratio=1.25,
    )


def test_independent_japanese_cue_requires_two_models_and_low_echo() -> None:
    probes = [
        ("ja", WhisperTranscription("待って。", "ja", -0.08)),
        ("ja", WhisperTranscription("ストップ。", "ja", -0.12)),
    ]

    assert independent_same_language_cue_confirmed(
        probes,
        preferred_language="ja",
        rendered_text="1、2、3、4、5。",
        render_correlation=0.22,
        maximum_render_correlation=0.30,
        confidence_threshold=-0.50,
    )
    assert not independent_same_language_cue_confirmed(
        probes[:1],
        preferred_language="ja",
        rendered_text="1、2、3、4、5。",
        render_correlation=0.22,
        maximum_render_correlation=0.30,
        confidence_threshold=-0.50,
    )
    assert not independent_same_language_cue_confirmed(
        probes,
        preferred_language="ja",
        rendered_text="1、2、3、4、5。",
        render_correlation=0.31,
        maximum_render_correlation=0.30,
        confidence_threshold=-0.50,
    )
    assert barge_probe_interval_ms("en", english_ms=420, japanese_ms=700) == 700


class StubDetailedSTT:
    def __init__(self, result: WhisperTranscription) -> None:
        self.result = result
        self.calls = 0

    async def transcribe_detailed(self, _audio: bytes, _sample_rate: int) -> WhisperTranscription:
        self.calls += 1
        return self.result


class StubBilingualSTT:
    def __init__(
        self,
        english: StubDetailedSTT,
        japanese: StubDetailedSTT,
        japanese_robust: StubDetailedSTT | None = None,
    ) -> None:
        self.english_barge_fast = english
        self.japanese_barge_fast = japanese
        self.japanese_barge_cue_robust = japanese_robust


def test_barge_decode_stops_after_preferred_language_render_echo() -> None:
    english = StubDetailedSTT(WhisperTranscription("The sky looks blue.", "en", -0.12))
    japanese = StubDetailedSTT(WhisperTranscription("待って。", "ja", -0.02))

    probes = asyncio.run(
        decode_barge_probes(
            StubBilingualSTT(english, japanese),
            b"audio",
            16_000,
            "en",
            "The sky looks blue because blue light scatters.",
        )
    )

    assert [language for language, _ in probes] == ["en"]
    assert english.calls == 1
    assert japanese.calls == 0


def test_barge_decode_checks_alternate_language_for_non_render_speech() -> None:
    english = StubDetailedSTT(WhisperTranscription("Marty.", "en", -0.35))
    japanese = StubDetailedSTT(WhisperTranscription("待って。", "ja", -0.02))

    probes = asyncio.run(
        decode_barge_probes(
            StubBilingualSTT(english, japanese),
            b"audio",
            16_000,
            "en",
            "The sky looks blue because blue light scatters.",
        )
    )

    assert [language for language, _ in probes] == ["en", "ja"]
    assert english.calls == 1
    assert japanese.calls == 1


def test_barge_decode_accepts_preferred_language_cue_without_alternate() -> None:
    english = StubDetailedSTT(WhisperTranscription("Stop.", "en", -0.08))
    japanese = StubDetailedSTT(WhisperTranscription("ストップ。", "ja", -0.02))

    probes = asyncio.run(
        decode_barge_probes(
            StubBilingualSTT(english, japanese),
            b"audio",
            16_000,
            "en",
            "The sky looks blue because blue light scatters.",
        )
    )

    assert [language for language, _ in probes] == ["en"]
    assert english.calls == 1
    assert japanese.calls == 0


def test_barge_decode_recovers_japanese_cue_with_large_model() -> None:
    english = StubDetailedSTT(WhisperTranscription("You.", "en", -0.4))
    japanese = StubDetailedSTT(WhisperTranscription("僕、ナナ、ハッキリ。", "ja", -0.07))
    robust = StubDetailedSTT(WhisperTranscription("ストップ。", "ja", -0.1))

    probes = asyncio.run(
        decode_barge_probes(
            StubBilingualSTT(english, japanese, robust),
            b"audio",
            16_000,
            "ja",
            "1、2、3、4、5、6、7、8、9、10。",
        )
    )

    assert [result.text for _, result in probes] == [
        "僕、ナナ、ハッキリ。",
        "ストップ。",
    ]
    assert robust.calls == 1
    assert english.calls == 0


def test_barge_decode_rechecks_both_languages_after_validated_cue() -> None:
    english = StubDetailedSTT(WhisperTranscription("Six, seven, eight.", "en", -0.12))
    japanese = StubDetailedSTT(WhisperTranscription("短いジョークを言って。", "ja", -0.10))

    probes = asyncio.run(
        decode_barge_probes(
            StubBilingualSTT(english, japanese),
            b"audio",
            16_000,
            "en",
            "One, two, three, four, five, six, seven, eight.",
            always_decode_both=True,
        )
    )

    assert [language for language, _ in probes] == ["en", "ja"]
    assert english.calls == 1
    assert japanese.calls == 1


def test_raw_continuation_uses_robust_provider_in_active_language() -> None:
    english_fast = StubDetailedSTT(WhisperTranscription("Four, five.", "en", -0.10))
    english_robust = StubDetailedSTT(
        WhisperTranscription("I need a short joke instead.", "en", -0.44)
    )
    japanese = StubDetailedSTT(WhisperTranscription("短いジョークを言って。", "ja", -0.20))
    stt = StubBilingualSTT(english_fast, japanese)
    stt.english_barge_robust = english_robust
    stt.japanese = japanese

    probes = asyncio.run(decode_raw_barge_continuation(stt, b"raw", 16_000, "en"))

    assert probes[0][1].text == "I need a short joke instead."
    assert english_robust.calls == 1


def test_preliminary_cue_can_open_window_below_flush_confidence() -> None:
    probes = [("en", WhisperTranscription("Stop.", "en", -0.936))]

    selected = select_preliminary_barge_cue(
        probes,
        "One, two, three, four.",
        confidence_threshold=-1.00,
    )
    rejected = select_preliminary_barge_cue(
        probes,
        "One, two, three, four.",
        confidence_threshold=-0.60,
    )

    assert selected == probes[0]
    assert rejected is None


def test_cross_language_control_decode_does_not_force_following_turn_language() -> None:
    assert barge_turn_language("en", "ja", explicit_cue=True) == "auto"
    assert barge_turn_language("ja", "en", explicit_cue=True) == "auto"
    assert barge_turn_language("en", "en", explicit_cue=True) == "en"
    assert barge_turn_language("en", "ja", explicit_cue=False) == "ja"


def test_pacing_keeps_small_scheduler_jitter() -> None:
    origin, target = rebase_pacing_after_gap(10.0, 10.5, 10.52, maximum_lag_seconds=0.04)

    assert origin == 10.0
    assert target == 10.5


def test_pacing_rebases_after_inference_gap_instead_of_bursting() -> None:
    origin, target = rebase_pacing_after_gap(10.0, 10.5, 10.8, maximum_lag_seconds=0.04)

    assert origin == 10.3
    assert target == 10.8


def test_confirmed_barge_audio_removes_detector_preroll_overlap_once() -> None:
    frames = [bytes([index]) * 640 for index in range(1, 6)]
    verified_probe = b"".join(frames[:4])
    detector_audio = b"".join(frames[2:])

    merged = merge_audio_without_overlap(verified_probe, detector_audio)

    assert merged == b"".join(frames)


def test_confirmed_barge_keeps_semantic_window_and_only_later_suffix() -> None:
    earlier_echo = b"robot-echo"
    semantic_window = b"stop-I-need-a-joke"
    later_tail = b"-instead"

    retained = retain_semantic_window_and_suffix(
        semantic_window, earlier_echo + semantic_window + later_tail
    )

    assert retained == semantic_window + later_tail
    assert earlier_echo not in retained


def test_natural_barge_transcripts_are_bilingual_and_not_whitelisted() -> None:
    assert is_meaningful_barge_transcript("No, the other way")
    assert is_meaningful_barge_transcript("違う、反対だよ")
    assert is_meaningful_barge_transcript("Thanks, but tell me something else")
    assert not is_meaningful_barge_transcript("[music]")
    assert not is_meaningful_barge_transcript("Hmm.")
    assert not is_meaningful_barge_transcript("Ah!")
    assert not is_meaningful_barge_transcript("んー")
    assert not is_meaningful_barge_transcript("あ！")
    assert not is_meaningful_barge_transcript("")
    assert is_substantial_natural_barge("No, the other way", "en")
    assert is_substantial_natural_barge("違う、反対だよ", "ja")
    assert not is_substantial_natural_barge("レタイト", "ja")
    assert not is_substantial_natural_barge("Nope", "en")


def test_pairing_challenge_response_binds_nonce_and_device() -> None:
    secret = "paired-secret"
    device_nonce = "device-nonce"
    response = pairing_proof(secret, "device", "fresh-nonce", device_nonce, "device-1")

    assert pairing_response_matches(secret, "fresh-nonce", device_nonce, "device-1", response)
    assert not pairing_response_matches(secret, "other-nonce", device_nonce, "device-1", response)
    assert not pairing_response_matches(
        secret, "fresh-nonce", "other-device-nonce", "device-1", response
    )
    assert not pairing_response_matches(secret, "fresh-nonce", device_nonce, "device-2", response)
    assert pairing_proof(secret, "server", "fresh-nonce", device_nonce, "device-1") != response


def test_failed_tool_result_is_terminal_even_if_stage_is_dispatched() -> None:
    assert tool_result_is_terminal({"stage": "dispatched", "success": False})
    assert tool_result_is_terminal({"stage": "rejected", "success": False})
    assert not tool_result_is_terminal({"stage": "dispatched", "success": True})


def test_render_echo_match_normalizes_spoken_numbers_and_rejects_other_requests() -> None:
    rendered = "1, 2, 3, 4, 5, 6, 7, 8, 9, 10."
    assert looks_like_render_echo("One, two, three.", rendered)
    assert looks_like_render_echo("I'm sorry.", "I'm sorry, I misunderstood you.")
    assert looks_like_render_echo(
        "The scarecrow winner.",
        "Why did the scarecrow win an award? Because he was outstanding in his field.",
    )
    assert looks_like_render_echo("1.", "1...2...3...4...")
    assert looks_like_render_echo("4.5.2.", "1、2、3、4、5、6、7、8、9、10。")
    assert looks_like_render_echo("なぜか", "なぜカエルは高い場所にいるの？ 高い場所にいるから！")
    assert looks_like_render_echo(
        "It hurt.", "Why did the cat go to the party? It heard it was a purr-tay!"
    )
    assert not looks_like_render_echo("Stop, tell me a short joke instead.", rendered)
    assert not looks_like_render_echo(
        "Stop, tell me a short joke instead.",
        "Why did the scarecrow win an award? Because he was outstanding in his field.",
    )


def test_barge_probe_prefers_active_language_with_comparable_confidence() -> None:
    probes = [
        ("en", WhisperTranscription("Stop! Tell me a joke.", "en", -0.18)),
        ("ja", WhisperTranscription("ストップ。短いジョークを言って。", "ja", -0.31)),
    ]

    language, result = select_barge_probe(probes, "ja")

    assert language == "ja"
    assert result.text.startswith("ストップ")


def test_barge_probe_ignores_unmeaningful_active_language_decode() -> None:
    probes = [
        ("en", WhisperTranscription("I'm sorry.", "en", -0.04)),
        ("ja", WhisperTranscription("7", "ja", -0.001)),
    ]

    language, result = select_barge_probe(probes, "ja")

    assert language == "en"
    assert result.text == "I'm sorry."


def test_barge_probe_allows_confident_cross_language_candidate() -> None:
    probes = [
        ("en", WhisperTranscription("Stop, tell me a joke.", "en", -0.14)),
        ("ja", WhisperTranscription("みんな見てくれてありがとう。", "ja", -0.93)),
    ]

    language, result = select_barge_probe(probes, "ja")

    assert language == "en"
    assert result.average_log_probability == -0.14


def test_barge_probe_prefers_explicit_cue_over_active_language_fragment() -> None:
    probes = [
        ("en", WhisperTranscription("There's four.", "en", -0.24)),
        ("ja", WhisperTranscription("待って。", "ja", -0.02)),
    ]

    language, result = select_barge_probe(probes, "en")

    assert language == "ja"
    assert result.text == "待って。"


def test_cross_language_barge_requires_explicit_stable_cue() -> None:
    assert has_explicit_barge_cue("Stop, tell me a joke.", "en")
    assert has_explicit_barge_cue("Wait.", "en")
    assert not has_explicit_barge_cue("I'm waiting with you.", "en")
    assert not has_explicit_barge_cue("That was unstoppable.", "en")
    assert not has_explicit_barge_cue("You're not going to stop me.", "en")
    assert has_explicit_barge_cue("Stack-chan, please stop now.", "en")
    assert not has_explicit_barge_cue("Tell me a joke instead.", "en")
    assert has_explicit_barge_cue("ちょっと待って。", "ja")
    assert not has_explicit_barge_cue("短いジョークを言って。", "ja")
    assert not has_explicit_barge_cue("I'm not doing it.", "en")
    assert not is_stable_barge_language_switch("en", "Stop, tell me a joke.", [])
    assert is_stable_barge_language_switch(
        "en",
        "Stop, tell me a joke instead.",
        [("en", "stop, tell me a joke.")],
    )
    assert not is_stable_barge_language_switch("ja", "待って。", [("en", "stop.")])
    assert is_stable_barge_language_switch("ja", "待って。", [("ja", "ちょっと待って。")])


def test_barge_continuation_anchor_tracks_cue_language_by_default() -> None:
    history = [("ja", "待って。")]

    assert has_prior_explicit_barge_cue(history)
    assert has_prior_explicit_barge_cue(history, "ja")
    assert not has_prior_explicit_barge_cue(history, "en")


def test_replacement_request_cue_rejects_narration() -> None:
    assert has_replacement_request_cue("Tell me a short joke instead.", "en")
    assert has_replacement_request_cue("短いジョークを言ってください。", "ja")
    assert not has_replacement_request_cue(
        "We're going to be in the molecules in our atmosphere.", "en"
    )


def test_natural_same_language_speech_does_not_interrupt_playback() -> None:
    assert not has_barge_intent_evidence("en", "No, the other way", "en", [])
    assert not has_barge_intent_evidence(
        "en", "No, the other way", "en", [("en", "no the other way")]
    )
    assert not has_barge_intent_evidence("ja", "違う、反対だよ", "ja", [])
    assert not has_barge_intent_evidence("ja", "違う、反対だよ", "ja", [("ja", "違う、反対だよ")])
    assert not has_barge_intent_evidence("en", "Stop", "en", [])
    assert has_barge_intent_evidence("en", "Stop", "en", [("en", "stop")])


def test_cross_language_barge_still_requires_cue_and_stability() -> None:
    assert not has_barge_intent_evidence("en", "Stop, tell me a joke.", "ja", [])
    assert has_barge_intent_evidence(
        "en",
        "Stop, tell me a joke.",
        "ja",
        [("en", "stop tell me a joke")],
    )
    assert not has_barge_intent_evidence(
        "en", "No, the other way", "ja", [("en", "no the other way")]
    )


def test_prior_barge_cue_anchors_the_later_replacement_request() -> None:
    assert has_prior_explicit_barge_cue([("en", "stop")])
    assert has_prior_explicit_barge_cue([("ja", "ちょっと待って")])
    assert not has_prior_explicit_barge_cue([("en", "tell me a joke instead")])
    assert has_replacement_request_cue("I need a short joke.", "en")
    assert not has_replacement_request_cue("I need.", "en")
    assert not has_replacement_request_cue("Instead.", "en")
    assert not has_replacement_request_cue("A short joke instead.", "en")


def test_cue_anchored_continuation_has_its_own_bounded_confidence_margin() -> None:
    history = [("en", "stop")]

    assert is_actionable_barge_continuation(
        "I need a short joke instead.",
        "en",
        -0.62,
        "One, two, three, four.",
        history,
        confidence_threshold=-0.70,
    )
    assert not is_actionable_barge_continuation(
        "I need a short joke instead.",
        "en",
        -0.62,
        "One, two, three, four.",
        [],
        confidence_threshold=-0.70,
    )
    assert not is_actionable_barge_continuation(
        "I need a short joke instead.",
        "en",
        -0.72,
        "One, two, three, four.",
        history,
        confidence_threshold=-0.70,
    )
    assert not is_actionable_barge_continuation(
        "One, two, three.",
        "en",
        -0.10,
        "One, two, three, four.",
        history,
        confidence_threshold=-0.70,
    )
    assert not is_actionable_barge_continuation(
        "よく分からない。",
        "ja",
        -0.08,
        "1、2、3、4、5。",
        [("ja", "ちょっと待って。")],
        confidence_threshold=-0.70,
    )


def test_final_barge_confidence_does_not_reapply_standalone_threshold() -> None:
    assert not barge_confidence_is_sufficient(
        -0.627,
        anchored_continuation=False,
        general_threshold=-0.60,
        continuation_threshold=-0.70,
    )
    assert barge_confidence_is_sufficient(
        -0.627,
        anchored_continuation=True,
        general_threshold=-0.60,
        continuation_threshold=-0.70,
    )


def test_ducked_raw_continuation_accepts_cross_decoder_control_anchor() -> None:
    japanese_cue_history = [("ja", "待って。")]

    assert not is_actionable_barge_continuation(
        "I need a short joke.",
        "en",
        -0.19,
        "One, two, three, four.",
        japanese_cue_history,
        confidence_threshold=-0.70,
    )
    assert is_actionable_barge_continuation(
        "I need a short joke.",
        "en",
        -0.19,
        "One, two, three, four.",
        japanese_cue_history,
        confidence_threshold=-0.70,
        allow_cross_language_anchor=True,
    )


def test_cross_language_barge_rejects_quiet_decoder_hallucination() -> None:
    assert cross_language_barge_has_acoustic_support(False, 1000, 6000)
    assert not cross_language_barge_has_acoustic_support(True, 1938, 6000)
    assert cross_language_barge_has_acoustic_support(True, 12532, 6000)
    assert not cross_language_barge_has_acoustic_support(
        True, 16155, 6000, preferred_decoder_is_render_echo=True
    )


def test_cross_language_control_cue_requires_bounded_stable_retry() -> None:
    assert cross_language_control_cue_can_confirm(
        False, True, playback_ducked=False, probe_stable=False
    )
    assert not cross_language_control_cue_can_confirm(
        True, True, playback_ducked=False, probe_stable=True
    )
    assert not cross_language_control_cue_can_confirm(
        True, True, playback_ducked=True, probe_stable=False
    )
    assert cross_language_control_cue_can_confirm(
        True, True, playback_ducked=True, probe_stable=True
    )


def test_cue_only_confirmation_requires_non_render_raw_support() -> None:
    rendered = "Sunlight scatters off the air molecules."
    cue = WhisperTranscription("Stop.", "en", -0.18)
    robot_echo = WhisperTranscription("Sunlight scatters off", "en", -0.10)

    assert has_raw_control_cue_support([("en", cue)], rendered, confidence_threshold=-0.60)
    assert not has_raw_control_cue_support(
        [("en", robot_echo)], rendered, confidence_threshold=-0.60
    )
    assert not has_raw_control_cue_support(
        [("en", WhisperTranscription("Stop.", "en", -0.72))],
        rendered,
        confidence_threshold=-0.60,
    )


def test_clean_cue_cannot_duck_playback_without_raw_corroboration() -> None:
    assert not semantic_cue_can_open_listening_window(
        clean_cue_supported=True, raw_control_cue_supported=False
    )
    assert semantic_cue_can_open_listening_window(
        clean_cue_supported=True, raw_control_cue_supported=True
    )
