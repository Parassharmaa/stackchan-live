from stackchan_agent.config import Settings


def test_default_voice_uses_youthful_bilingual_profile() -> None:
    settings = Settings(_env_file=None)

    assert settings.supertonic_voice == "F4"
    assert settings.supertonic_speed == 1.10
    assert settings.supertonic_steps == 5
