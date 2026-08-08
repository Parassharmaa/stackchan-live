from array import array

from stackchan_agent.echo import EchoCanceller


def test_24k_to_16k_resampler_preserves_duration_and_endpoints() -> None:
    source = array("h", range(240))

    output = array("h")
    output.frombytes(EchoCanceller.resample_24k_to_16k(source.tobytes()))

    assert len(output) == 160
    assert output[0] == 0
    assert output[1] == 1
    assert output[-1] == 238


def test_echo_processor_accepts_twenty_millisecond_frames() -> None:
    echo = EchoCanceller(delay_ms=160)
    echo.feed_render_24k(bytes(480 * 2))

    clean = echo.process_capture_16k(bytes(320 * 2))

    assert len(clean) == 640
    assert echo.frames_processed == 2
    assert echo.raw_rms == 0


def test_double_talk_gate_rejects_echo_and_aec_instability() -> None:
    echo = EchoCanceller(delay_ms=160)

    echo.raw_rms, echo.clean_rms = 20_000, 900
    assert not echo.confident_near_end()
    echo.raw_rms, echo.clean_rms = 4_000, 1_200
    assert echo.confident_near_end()
    echo.capture_render_correlation = 0.95
    assert not echo.confident_near_end()
    echo.capture_render_correlation = 0.0
    echo.raw_rms, echo.clean_rms = 1_000, 3_000
    assert not echo.confident_near_end()

    old_processor = echo._processor
    echo.reset()
    assert echo._processor is not old_processor


def test_end_render_preserves_adapted_processor() -> None:
    echo = EchoCanceller(delay_ms=160)
    echo.feed_render_24k(bytes(480 * 2))
    processor = echo._processor

    echo.end_render()

    assert echo._processor is processor
    assert not echo.render_recent


def test_capture_bypasses_aec_without_a_recent_render_reference() -> None:
    echo = EchoCanceller(delay_ms=160)
    pcm = bytes(range(128)) * 5

    assert echo.process_capture_16k(pcm) == pcm
    assert echo.raw_rms == echo.clean_rms


def test_render_correlation_identifies_matching_capture() -> None:
    echo = EchoCanceller(delay_ms=0)
    render = array("h", (index * 100 - 8000 for index in range(240))).tobytes()
    capture = EchoCanceller.resample_24k_to_16k(render)

    echo.feed_render_24k(render)
    echo.process_capture_16k(capture)

    assert echo.capture_render_correlation > 0.99
    assert echo.capture_render_lag_ms == 0


def test_render_reference_gain_matches_physical_ducking() -> None:
    echo = EchoCanceller(delay_ms=0)
    render = array("h", [8_000] * 240).tobytes()

    echo.feed_render_24k(render, gain=0.05)
    reference = array("h")
    reference.frombytes(echo._render_history[-1])

    assert set(reference) == {400}


def test_aligned_render_projection_removes_scalar_echo() -> None:
    echo = EchoCanceller(delay_ms=0)
    render_samples = array("h", (index * 80 - 6_000 for index in range(240)))
    render = render_samples.tobytes()
    capture_reference = EchoCanceller.resample_24k_to_16k(render)
    capture_samples = array("h")
    capture_samples.frombytes(capture_reference)
    capture = array("h", (sample // 2 for sample in capture_samples)).tobytes()
    echo.feed_render_24k(render)

    projected = echo.remove_aligned_render(capture)

    assert EchoCanceller._rms(projected) < EchoCanceller._rms(capture) * 0.02
