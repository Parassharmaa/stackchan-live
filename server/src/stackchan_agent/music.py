import math
from array import array

ROUTINE_MELODIES: dict[str, tuple[int, ...]] = {
    "greet": (659, 784, 988, 1319),
    # Original mini-songs. Rests keep them light enough for the small speaker.
    "celebrate": (
        523, 659, 784, 1047, 0, 784, 988, 1319,
        1047, 0, 659, 784, 988, 1175, 1319, 0,
        1047, 1319, 1568, 1319, 1047, 988, 784, 1047,
        0, 784, 988, 1319, 1568, 1319, 1047, 1568,
    ),
    "curious": (784, 0, 988, 1175),
    # Gentle waltz: a slower, lower register for calm/relax requests.
    "comfort": (
        392, 494, 587, 494, 440, 523, 659, 523,
        392, 494, 587, 698, 587, 494, 440, 0,
        349, 440, 523, 440, 392, 494, 587, 494,
        440, 523, 659, 784, 659, 523, 440, 0,
    ),
    "dance": (
        523, 659, 784, 1047, 784, 659, 587, 784,
        0, 659, 784, 988, 1175, 988, 784, 659,
        523, 659, 784, 1047, 1175, 1047, 988, 784,
        0, 587, 784, 988, 784, 659, 523, 659,
        784, 988, 1175, 1319, 1175, 988, 784, 659,
        784, 1047, 988, 784, 659, 587, 523, 784,
    ),
    "wake_up": (
        392, 523, 659, 784, 0, 523, 659, 784,
        988, 0, 659, 784, 988, 1175, 988, 784,
        659, 784, 988, 1175, 1319, 1175, 988, 784,
        0, 659, 784, 988, 1175, 1319, 1568, 1319,
    ),
    # Repeating lo-fi arpeggio: predictable enough to sit under a focus cue.
    "focus": (
        330, 494, 659, 494, 370, 554, 740, 554,
        392, 587, 784, 587, 370, 554, 740, 554,
        330, 494, 659, 494, 294, 440, 587, 440,
        330, 494, 659, 784, 659, 494, 392, 0,
        330, 494, 659, 494, 370, 554, 740, 554,
    ),
    "good_night": (
        784, 659, 523, 0, 659, 523, 440, 0,
        523, 659, 784, 659, 523, 440, 392, 0,
        659, 523, 440, 392, 440, 523, 659, 0,
        523, 440, 392, 330, 392, 440, 392, 262,
    ),
}

ROUTINE_NOTE_MS = {
    "greet": 120,
    "celebrate": 150,
    "curious": 160,
    "comfort": 180,
    "dance": 140,
    "wake_up": 150,
    "focus": 150,
    "good_night": 220,
}

# Six user-facing styles, all synthesized locally and routed through the same
# observable, interruptible PCM queue as speech.
MUSIC_STYLE_ROUTINES: dict[str, str] = {
    "fanfare": "celebrate",
    "chiptune_dance": "dance",
    "sunrise": "wake_up",
    "gentle_waltz": "comfort",
    "lofi_focus": "focus",
    "lullaby": "good_night",
}


def music_duration_seconds(routine: str, *, note_ms: int | None = None) -> float:
    """Return the exact frame-independent duration of a routine composition."""
    melody = ROUTINE_MELODIES.get(routine, ROUTINE_MELODIES["greet"])
    duration_ms = note_ms or ROUTINE_NOTE_MS.get(routine, ROUTINE_NOTE_MS["greet"])
    return len(melody) * duration_ms / 1_000


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
