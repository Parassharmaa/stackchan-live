#!/usr/bin/env python3
"""Validate an immediate top-sensor reaction through the real TTS service."""

import argparse
import asyncio
import hashlib
import hmac
import json
import secrets
import time

import websockets
from stackchan_agent.config import Settings
from stackchan_agent.protocol import AudioFlags, AudioFrame, ControlMessage


async def probe(url: str, gesture: str) -> dict:
    configured_token = Settings().device_token
    if configured_token is None or not configured_token.get_secret_value().strip():
        raise RuntimeError("sensor probe requires a configured device pairing token")
    token = configured_token.get_secret_value()
    device_id = "sensor-probe"
    async with websockets.connect(url, max_size=8 * 1024 * 1024) as socket:
        challenge = ControlMessage.decode(await socket.recv())
        if challenge.type != "auth.challenge":
            raise RuntimeError("server did not begin the pairing challenge")
        nonce = str(challenge.payload.get("nonce", ""))
        device_nonce = secrets.token_hex(32)
        auth_response = hmac.new(
            token.encode(),
            f"stackchan-v1:device:{nonce}:{device_nonce}:{device_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        await socket.send(
            json.dumps(
                {
                    "type": "hello",
                    "payload": {
                        "protocol_version": 1,
                        "device_id": device_id,
                        "device_nonce": device_nonce,
                        "auth_response": auth_response,
                        "model": "software-probe",
                        "turn_detection": "manual",
                        "test_session": True,
                    },
                }
            )
        )
        # The handshake emits hello.ack followed by the initial idle state.
        # Drain both so that stale idle cannot terminate the sensor reaction
        # loop before its routine and generated audio arrive.
        while True:
            # Eve warms a dedicated session before the acknowledgement. A cold
            # local sandbox can legitimately take several seconds, so this
            # probe must not report the head sensor as broken after only 3 s.
            handshake = await asyncio.wait_for(socket.recv(), timeout=20)
            if isinstance(handshake, str):
                event = ControlMessage.decode(handshake)
                if event.type == "hello.ack":
                    expected_server_response = hmac.new(
                        token.encode(),
                        (
                            f"stackchan-v1:server:{nonce}:{device_nonce}:{device_id}"
                        ).encode(),
                        hashlib.sha256,
                    ).hexdigest()
                    if not hmac.compare_digest(
                        str(event.payload.get("server_response", "")),
                        expected_server_response,
                    ):
                        raise RuntimeError("server failed mutual pairing proof")
                if event.type == "session.state" and event.payload.get("state") == "idle":
                    break
        started = time.perf_counter()
        await socket.send(
            json.dumps(
                {
                    "type": "sensor.head",
                    "payload": {"gesture": gesture, "zone": 2, "strength": 3},
                }
            )
        )
        audio: list[AudioFrame] = []
        routine = None
        routine_ms = None
        text = None
        speaking_ms = None
        first_audio_ms = None
        while True:
            message = await asyncio.wait_for(socket.recv(), timeout=15)
            elapsed_ms = (time.perf_counter() - started) * 1000
            if isinstance(message, bytes):
                audio.append(AudioFrame.decode(message))
                first_audio_ms = first_audio_ms or elapsed_ms
                continue
            event = ControlMessage.decode(message)
            if event.type == "routine.play":
                routine = event.payload.get("name")
                routine_ms = elapsed_ms
            elif event.type == "session.state" and event.payload.get("state") == "speaking":
                speaking_ms = elapsed_ms
            elif event.type == "response.text.done":
                text = event.payload.get("text")
            elif event.type == "session.state" and event.payload.get("state") == "idle":
                break
        expected_routine = {
            "touch": "curious",
            "hold": "comfort",
            "swipe_forward": "dance",
            "swipe_backward": "curious",
        }[gesture]
        assert routine == expected_routine
        assert routine_ms is not None and routine_ms < 500
        assert audio and audio[0].flags & AudioFlags.START
        assert audio[-1].flags & AudioFlags.END
        assert text
        return {
            "routine": routine,
            "gesture": gesture,
            "text": text,
            "routine_ms": routine_ms,
            "speaking_ms": speaking_ms,
            "first_audio_ms": first_audio_ms,
            "audio_frames": len(audio),
            "audio_seconds": sum(len(frame.pcm) for frame in audio) / 2 / 24_000,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--gesture",
        choices=("touch", "hold", "swipe_forward", "swipe_backward"),
        default="touch",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(probe(args.url, args.gesture)), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
