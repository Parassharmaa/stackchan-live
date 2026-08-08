from stackchan_agent.metrics import speech_error_rate


def test_english_error_rate_normalizes_number_words_and_stack_chan_hyphen() -> None:
    assert (
        speech_error_rate(
            "Stack Chan, count slowly from one to ten.",
            "Stack-chan, count slowly from 1 to 10.",
            "en",
        )
        == 0
    )


def test_japanese_error_rate_ignores_punctuation_but_keeps_number_errors() -> None:
    assert speech_error_rate("1から10まで。", "1から10まで", "ja") == 0
    assert speech_error_rate("1から10まで。", "2から10まで。", "ja") > 0
