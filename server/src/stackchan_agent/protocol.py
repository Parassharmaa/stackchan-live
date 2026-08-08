import json
import struct
from enum import IntEnum, IntFlag
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAGIC = b"STKA"
PROTOCOL_VERSION = 1
_HEADER = struct.Struct("<4sBBHII")
_IMAGE_HEADER = struct.Struct("<4sBBHH32s")


class AudioStream(IntEnum):
    MICROPHONE = 1
    SPEAKER = 2
    PHYSICAL_RENDER = 3


class AudioFlags(IntFlag):
    NONE = 0
    START = 1
    END = 2
    CANCELLED = 4


class ImageFormat(IntEnum):
    JPEG = 1


class ImageFrame(BaseModel):
    """One correlated camera still from the physical device."""

    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    width: int = Field(gt=0, le=4096)
    height: int = Field(gt=0, le=4096)
    format: ImageFormat = ImageFormat.JPEG
    data: bytes = Field(min_length=1, max_length=2_000_000)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def encode(self) -> bytes:
        return _IMAGE_HEADER.pack(
            b"STKI",
            PROTOCOL_VERSION,
            int(self.format),
            self.width,
            self.height,
            self.request_id.encode("ascii"),
        ) + self.data

    @classmethod
    def decode(cls, payload: bytes) -> "ImageFrame":
        if len(payload) <= _IMAGE_HEADER.size:
            raise ValueError("image frame is shorter than its header")
        magic, version, image_format, width, height, request_id = _IMAGE_HEADER.unpack_from(
            payload
        )
        if magic != b"STKI":
            raise ValueError("invalid image frame magic")
        if version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported image protocol version: {version}")
        try:
            decoded_request_id = request_id.decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError("image request id is not ASCII") from error
        return cls(
            request_id=decoded_request_id,
            width=width,
            height=height,
            format=ImageFormat(image_format),
            data=payload[_IMAGE_HEADER.size :],
        )


class AudioFrame(BaseModel):
    stream: AudioStream
    flags: AudioFlags = AudioFlags.NONE
    sequence: int = Field(ge=0, le=0xFFFFFFFF)
    timestamp_ms: int = Field(ge=0, le=0xFFFFFFFF)
    pcm: bytes

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def encode(self) -> bytes:
        return _HEADER.pack(
            MAGIC,
            PROTOCOL_VERSION,
            int(self.stream),
            int(self.flags),
            self.sequence,
            self.timestamp_ms,
        ) + self.pcm

    @classmethod
    def decode(cls, payload: bytes) -> "AudioFrame":
        if len(payload) < _HEADER.size:
            raise ValueError("audio frame is shorter than its header")
        magic, version, stream, flags, sequence, timestamp_ms = _HEADER.unpack_from(payload)
        if magic != MAGIC:
            raise ValueError("invalid audio frame magic")
        if version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported audio protocol version: {version}")
        pcm = payload[_HEADER.size :]
        if len(pcm) % 2:
            raise ValueError("PCM16 payload has an odd byte count")
        return cls(
            stream=AudioStream(stream),
            flags=AudioFlags(flags),
            sequence=sequence,
            timestamp_ms=timestamp_ms,
            pcm=pcm,
        )


class ControlMessage(BaseModel):
    type: str
    request_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def encode(self) -> str:
        return self.model_dump_json(exclude_none=True)

    @classmethod
    def decode(cls, payload: str) -> "ControlMessage":
        raw = json.loads(payload)
        if "type" not in raw:
            raise ValueError("control message is missing type")
        if "payload" not in raw:
            raw["payload"] = {k: v for k, v in raw.items() if k not in {"type", "request_id"}}
            raw = {k: v for k, v in raw.items() if k in {"type", "request_id", "payload"}}
        return cls.model_validate(raw)


def control(
    message_type: str, *, request_id: str | None = None, **payload: Any
) -> ControlMessage:
    return ControlMessage(type=message_type, request_id=request_id, payload=payload)
