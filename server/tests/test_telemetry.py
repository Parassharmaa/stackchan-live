import wave
from pathlib import Path

from stackchan_agent.telemetry import TraceRecorder


def test_audio_capture_is_explicit_and_writes_valid_pcm16(tmp_path: Path) -> None:
    disabled = TraceRecorder(tmp_path, trace_id="disabled")
    assert disabled.capture_pcm16(bytes(320), 16_000) is None

    enabled = TraceRecorder(tmp_path, trace_id="enabled", capture_audio=True)
    path = enabled.capture_pcm16(bytes(320), 16_000)
    assert path is not None
    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16_000
        assert handle.getnframes() == 160

    labeled = enabled.capture_pcm16(bytes(320), 16_000, label="barge-aec")
    assert labeled is not None
    assert labeled.name == "enabled-turn-002-barge-aec.wav"
