import pytest

from stackchan_agent.protocol import (
    AudioFlags,
    AudioFrame,
    AudioStream,
    ControlMessage,
    ImageFormat,
    ImageFrame,
)


def test_audio_frame_round_trip() -> None:
    original = AudioFrame(
        stream=AudioStream.MICROPHONE,
        flags=AudioFlags.START,
        sequence=42,
        timestamp_ms=1234,
        pcm=b"\x01\x00\xff\xff",
    )
    assert AudioFrame.decode(original.encode()) == original


def test_physical_render_reference_round_trip() -> None:
    original = AudioFrame(
        stream=AudioStream.PHYSICAL_RENDER,
        sequence=7,
        timestamp_ms=9,
        pcm=b"\x01\x00" * 320,
    )

    assert AudioFrame.decode(original.encode()) == original


def test_audio_frame_rejects_odd_pcm() -> None:
    encoded = AudioFrame(
        stream=AudioStream.MICROPHONE,
        sequence=0,
        timestamp_ms=0,
        pcm=b"\x00\x00",
    ).encode()
    with pytest.raises(ValueError, match="odd byte"):
        AudioFrame.decode(encoded + b"\x00")


def test_control_accepts_flat_payload_for_firmware_convenience() -> None:
    decoded = ControlMessage.decode('{"type":"barge_in","at_ms":123}')
    assert decoded.type == "barge_in"
    assert decoded.payload == {"at_ms": 123}


def test_barge_in_preserves_screen_double_tap_reason() -> None:
    decoded = ControlMessage.decode(
        '{"type":"barge_in","payload":{"reason":"screen_double_tap"}}'
    )

    assert decoded.type == "barge_in"
    assert decoded.payload == {"reason": "screen_double_tap"}


def test_image_frame_round_trip() -> None:
    original = ImageFrame(
        request_id="0123456789abcdef0123456789abcdef",
        width=320,
        height=240,
        format=ImageFormat.JPEG,
        data=b"\xff\xd8camera\xff\xd9",
    )

    assert ImageFrame.decode(original.encode()) == original


def test_image_frame_rejects_invalid_magic() -> None:
    original = ImageFrame(
        request_id="0123456789abcdef0123456789abcdef",
        width=320,
        height=240,
        data=b"jpeg",
    ).encode()

    with pytest.raises(ValueError, match="magic"):
        ImageFrame.decode(b"NOPE" + original[4:])
