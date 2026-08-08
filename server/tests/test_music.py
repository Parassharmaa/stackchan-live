from array import array

from stackchan_agent.music import ROUTINE_MELODIES, signature_jingle


def test_every_routine_has_a_short_frame_aligned_signature() -> None:
    for routine in ROUTINE_MELODIES:
        frames = signature_jingle(routine)
        assert 24 <= len(frames) <= 50
        assert all(len(frame) == 960 for frame in frames)
        assert any(frame != bytes(len(frame)) for frame in frames)
        samples = array("h")
        samples.frombytes(b"".join(frames))
        assert 2_000 < max(map(abs, samples)) < 12_000
