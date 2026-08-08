import math
import struct

from stackchan_agent.vad import (
    ConsecutiveSpeechDetector,
    EnergyTurnDetector,
    WebRtcSpeechGate,
    WindowedSpeechDetector,
    pcm16_rms,
)


def frame(amplitude: int, samples: int = 320) -> bytes:
    return struct.pack(
        f"<{samples}h",
        *(int(amplitude * math.sin(index * 0.15)) for index in range(samples)),
    )


def test_energy_turn_detector_emits_one_complete_turn() -> None:
    detector = EnergyTurnDetector(
        16_000,
        start_rms=500,
        stop_rms=250,
        start_ms=40,
        stop_ms=100,
        pre_roll_ms=40,
        min_turn_ms=100,
    )
    events = []
    for pcm in [frame(0)] * 3 + [frame(4000)] * 8 + [frame(0)] * 6:
        events.append(detector.feed(pcm))

    assert sum(event.speech_started for event in events) == 1
    completed = [event.completed_audio for event in events if event.completed_audio]
    assert len(completed) == 1
    assert len(completed[0]) >= 16_000 * 2 * 0.2
    assert detector.active is False


def test_energy_turn_detector_ignores_short_noise() -> None:
    detector = EnergyTurnDetector(16_000, start_rms=500, start_ms=80)
    events = [detector.feed(frame(4000)), detector.feed(frame(0))]
    assert not any(event.speech_started or event.completed_audio for event in events)


def test_pcm16_rms_distinguishes_motor_guard_level() -> None:
    assert pcm16_rms(bytes(640)) == 0
    assert 2750 <= pcm16_rms(frame(4000)) <= 2900


def test_start_gate_retains_real_audio_in_pre_roll() -> None:
    detector = EnergyTurnDetector(
        16_000,
        start_rms=500,
        stop_rms=250,
        start_ms=40,
        stop_ms=60,
        pre_roll_ms=200,
        min_turn_ms=100,
    )
    onset = frame(3000)
    for _ in range(4):
        assert not detector.feed(onset, allow_start=False).speech_started
    assert not detector.feed(onset, allow_start=True).speech_started
    assert detector.feed(onset, allow_start=True).speech_started
    completed = None
    for _ in range(4):
        event = detector.feed(frame(0))
        completed = completed or event.completed_audio
    assert completed is not None
    assert completed.startswith(onset)


def test_start_gate_can_be_strengthened_for_post_flush_confirmation() -> None:
    detector = EnergyTurnDetector(
        16_000,
        start_rms=500,
        start_ms=40,
        pre_roll_ms=100,
    )
    onset = frame(3000)

    detector.set_start_ms(60)
    assert not detector.feed(onset).speech_started
    assert not detector.feed(onset).speech_started
    assert detector.feed(onset).speech_started


def test_stop_gate_can_be_extended_for_a_paused_barge_request() -> None:
    detector = EnergyTurnDetector(
        16_000,
        start_rms=500,
        stop_rms=300,
        start_ms=20,
        stop_ms=40,
        min_turn_ms=20,
    )
    assert detector.feed(frame(3000)).speech_started
    detector.set_stop_ms(80)
    assert detector.feed(frame(0)).completed_audio is None
    assert detector.feed(frame(0)).completed_audio is None
    assert detector.feed(frame(0)).completed_audio is None
    assert detector.feed(frame(0)).completed_audio is not None


def test_webrtc_speech_gate_rejects_silence() -> None:
    gate = WebRtcSpeechGate(16_000, aggressiveness=2)

    assert not gate(bytes(320 * 2))


def test_consecutive_speech_detector_ducks_only_after_required_run() -> None:
    detector = ConsecutiveSpeechDetector(required_ms=60, frame_ms=20)

    assert not detector.feed(True)
    assert not detector.feed(False)
    assert not detector.feed(True)
    assert not detector.feed(True)
    assert detector.feed(True)
    assert not detector.feed(True)
    detector.reset()
    assert not detector.feed(True)


def test_windowed_speech_detector_tolerates_short_mora_gaps() -> None:
    detector = WindowedSpeechDetector(required_ms=120, window_ms=400, frame_ms=20)

    for value in (True, True, False, True, False, True, True):
        assert not detector.feed(value)
    assert detector.feed(True)
    assert not detector.feed(True)

    detector.reset()
    assert not detector.feed(True)


def test_turn_detector_exposes_bounded_start_audio() -> None:
    detector = EnergyTurnDetector(16_000, start_rms=100, start_ms=40, pre_roll_ms=60)
    frame = (1_000).to_bytes(2, "little", signed=True) * 320

    assert not detector.feed(frame).speech_started
    assert detector.feed(frame).speech_started

    assert detector.snapshot_audio()
    assert len(detector.snapshot_audio()) <= 16_000 * 2 * 60 // 1_000


def test_begin_turn_endpoints_only_audio_after_verified_prefix() -> None:
    detector = EnergyTurnDetector(
        16_000,
        start_rms=100,
        stop_rms=50,
        start_ms=40,
        stop_ms=40,
        pre_roll_ms=100,
        min_turn_ms=40,
    )
    loud = frame(1000)
    silence = frame(0)

    detector.feed(loud, allow_start=False)
    detector.begin_turn()
    detector.feed(loud)
    assert detector.feed(silence).completed_audio is None
    completed = detector.feed(silence)

    assert completed.completed_audio == loud + silence + silence
