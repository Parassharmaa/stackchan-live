from array import array

from stackchan_agent.music import (
    MUSIC_STYLE_ROUTINES,
    ROUTINE_MELODIES,
    music_duration_seconds,
    signature_jingle,
)


def test_every_routine_has_a_frame_aligned_composition() -> None:
    for routine in ROUTINE_MELODIES:
        frames = signature_jingle(routine)
        assert 24 <= len(frames) <= 400
        assert all(len(frame) == 960 for frame in frames)
        assert any(frame != bytes(len(frame)) for frame in frames)
        samples = array("h")
        samples.frombytes(b"".join(frames))
        assert 2_000 < max(map(abs, samples)) < 12_000


def test_requested_song_routines_are_long_enough_to_feel_musical() -> None:
    assert 4.5 <= music_duration_seconds("celebrate") <= 8.0
    assert 6.0 <= music_duration_seconds("dance") <= 8.0
    assert 4.5 <= music_duration_seconds("wake_up") <= 8.0
    assert 6.0 <= music_duration_seconds("good_night") <= 8.0


def test_six_distinct_music_styles_are_long_and_have_unique_compositions() -> None:
    assert set(MUSIC_STYLE_ROUTINES) == {
        "fanfare",
        "chiptune_dance",
        "sunrise",
        "gentle_waltz",
        "lofi_focus",
        "lullaby",
    }
    compositions = [ROUTINE_MELODIES[routine] for routine in MUSIC_STYLE_ROUTINES.values()]
    assert len(set(compositions)) == 6
    for routine in MUSIC_STYLE_ROUTINES.values():
        assert 4.5 <= music_duration_seconds(routine) <= 8.0


def test_signature_can_still_be_shortened_for_fast_feedback() -> None:
    frames = signature_jingle("dance", note_ms=20)
    assert len(frames) == len(ROUTINE_MELODIES["dance"])
