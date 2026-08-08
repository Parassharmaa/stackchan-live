#!/usr/bin/env python3
"""Exercise the real WebSocket cascade with bilingual fixture audio."""

import argparse
import asyncio
import hashlib
import hmac
import json
import secrets
import time
import wave
from pathlib import Path

import websockets
from stackchan_agent.config import Settings
from stackchan_agent.protocol import AudioFlags, AudioFrame, AudioStream, ControlMessage

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = {
    "en": ROOT / "artifacts/benchmarks/fixtures/en.wav",
    "ja": ROOT / "artifacts/benchmarks/fixtures/ja.wav",
}


async def probe(url: str, language: str, *, interrupt: bool = False) -> dict:
    path = FIXTURES[language]
    with wave.open(str(path), "rb") as handle:
        sample_rate = handle.getframerate()
        pcm = handle.readframes(handle.getnframes())
    frame_bytes = sample_rate // 50 * 2
    configured_token = Settings().device_token
    if configured_token is None or not configured_token.get_secret_value().strip():
        raise RuntimeError("software probe requires a configured device pairing token")
    token = configured_token.get_secret_value()
    device_id = f"probe-{language}"

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
        acknowledgement = ControlMessage.decode(await socket.recv())
        expected_server_response = hmac.new(
            token.encode(),
            f"stackchan-v1:server:{nonce}:{device_nonce}:{device_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if acknowledgement.type != "hello.ack" or not hmac.compare_digest(
            str(acknowledgement.payload.get("server_response", "")),
            expected_server_response,
        ):
            raise RuntimeError("server failed mutual pairing proof")
        for sequence, offset in enumerate(range(0, len(pcm), frame_bytes)):
            await socket.send(
                AudioFrame(
                    stream=AudioStream.MICROPHONE,
                    sequence=sequence,
                    timestamp_ms=sequence * 20,
                    pcm=pcm[offset : offset + frame_bytes],
                ).encode()
            )
        started = time.perf_counter()
        await socket.send(json.dumps({"type": "turn.commit", "payload": {}}))

        milestones: dict[str, float] = {}
        transcript = ""
        response = ""
        controls: list[dict] = []
        audio_frames: list[AudioFrame] = []
        saw_thinking = False
        sent_barge_in = False
        while True:
            message = await asyncio.wait_for(socket.recv(), timeout=30)
            elapsed_ms = (time.perf_counter() - started) * 1000
            if isinstance(message, bytes):
                frame = AudioFrame.decode(message)
                audio_frames.append(frame)
                milestones.setdefault("first_audio_ms", elapsed_ms)
                if interrupt and not sent_barge_in:
                    sent_barge_in = True
                    await socket.send(json.dumps({"type": "barge_in", "payload": {}}))
                continue

            event = ControlMessage.decode(message)
            if event.type in {
                "memory.stored",
                "motion.set",
                "lights.set",
                "routine.play",
            }:
                controls.append(
                    {
                        "type": event.type,
                        **({"request_id": event.request_id} if event.request_id else {}),
                        **event.payload,
                    }
                )
            if event.request_id and event.type in {
                "face.set",
                "motion.set",
                "lights.set",
                "routine.play",
            }:
                tool = {
                    "face.set": "set_face",
                    "motion.set": "move_head",
                    "lights.set": "set_lights",
                    "routine.play": "play_routine",
                }[event.type]
                await socket.send(
                    ControlMessage(
                        type="tool.result",
                        request_id=event.request_id,
                        payload={
                            "tool": tool,
                            "stage": "completed",
                            "success": True,
                            "detail": "software probe acknowledged correlated command",
                        },
                    ).encode()
                )
            if event.type == "session.state":
                state = event.payload.get("state")
                if state == "thinking":
                    saw_thinking = True
                    milestones.setdefault("thinking_ms", elapsed_ms)
                elif state == "speaking":
                    milestones.setdefault("speaking_ms", elapsed_ms)
                elif state == "idle" and saw_thinking:
                    milestones["complete_ms"] = elapsed_ms
                    break
            elif event.type == "transcript.final":
                transcript = str(event.payload.get("text", ""))
                milestones["transcript_ms"] = elapsed_ms
            elif event.type == "response.text.delta":
                milestones.setdefault("first_text_ms", elapsed_ms)
            elif event.type == "response.text.done":
                response = str(event.payload.get("text", ""))
            elif event.type == "playback.flush":
                milestones["flush_ms"] = elapsed_ms
                if interrupt:
                    break
            elif event.type == "error":
                raise RuntimeError(event.payload)

    if not interrupt:
        assert audio_frames, "pipeline emitted no speaker audio"
        assert audio_frames[0].flags & AudioFlags.START
        assert audio_frames[-1].flags & AudioFlags.END
        assert [frame.sequence for frame in audio_frames] == list(range(len(audio_frames)))
        assert any(item["type"] == "motion.set" for item in controls)
    return {
        "language": language,
        "interrupt": interrupt,
        "transcript": transcript,
        "response": response,
        "controls": controls,
        "memory_request_recognized": any(
            item["type"] == "memory.stored" for item in controls
        ),
        "audio_frames": len(audio_frames),
        "audio_seconds": sum(len(frame.pcm) for frame in audio_frames) / 2 / 24_000,
        **milestones,
    }


async def main_async(url: str) -> list[dict]:
    results = [await probe(url, language) for language in FIXTURES]
    results.append(await probe(url, "en", interrupt=True))
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(main_async(args.url)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
