import math
from array import array

ROUTINE_MELODIES: dict[str, tuple[int, ...]] = {
    "greet": (659, 784, 988, 1319),
    "celebrate": (523, 659, 784, 1047, 1319, 1568),
    "curious": (784, 0, 988, 1175),
    "comfort": (440, 523, 659, 523),
    "dance": (523, 659, 784, 1047, 784, 659, 587, 784),
}

ROUTINE_NOTE_MS = {
    "greet": 120,
    "celebrate": 110,
    "curious": 160,
    "comfort": 180,
    "dance": 110,
}


def signature_jingle(
    routine: str,
    *,
    sample_rate: int = 24_000,
    frame_ms: int = 20,
    note_ms: int | None = None,
    amplitude: int = 6_500,
) -> list[bytes]:
    """Return a cute, frame-aligned PCM16 musical signature for a routine."""
    melody = ROUTINE_MELODIES.get(routine, ROUTINE_MELODIES["greet"])
    note_ms = note_ms or ROUTINE_NOTE_MS.get(routine, ROUTINE_NOTE_MS["greet"])
    frame_samples = sample_rate * frame_ms // 1000
    note_samples = sample_rate * note_ms // 1000
    gap_samples = min(note_samples // 4, sample_rate * 20 // 1000)
    tone_samples = note_samples - gap_samples
    attack_samples = max(1, sample_rate * 8 // 1000)
    release_samples = max(1, sample_rate * 28 // 1000)
    pcm = array("h")
    phase = 0.0
    for frequency in melody:
        for index in range(note_samples):
            if frequency == 0 or index >= tone_samples:
                pcm.append(0)
                continue
            envelope = min(
                1.0,
                index / attack_samples,
                (tone_samples - index - 1) / release_samples,
            )
            elapsed = index / sample_rate
            vibrato = 1.0 + 0.004 * math.sin(2 * math.pi * 5.0 * elapsed)
            voice = (
                0.82 * math.sin(phase)
                + 0.14 * math.sin(phase * 2.0)
                + 0.04 * math.sin(phase * 0.5)
            )
            value = int(voice * amplitude * max(0.0, envelope))
            pcm.append(value)
            phase += 2 * math.pi * frequency * vibrato / sample_rate
    remainder = len(pcm) % frame_samples
    if remainder:
        pcm.extend([0] * (frame_samples - remainder))
    raw = pcm.tobytes()
    frame_bytes = frame_samples * 2
    return [raw[offset : offset + frame_bytes] for offset in range(0, len(raw), frame_bytes)]
