def edit_distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, 1):
        current = [left_index]
        for right_index, right_value in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_value != right_value),
                )
            )
        previous = current
    return previous[-1]


def speech_error_rate(reference: str, hypothesis: str, language: str) -> float:
    """Return WER for English and CER for Japanese after semantic normalization."""
    punctuation = "，。！？、,.!?：:'\"-"
    if language == "ja":
        table = str.maketrans("", "", punctuation)
        expected = list("".join(reference.translate(table).split()))
        actual = list("".join(hypothesis.translate(table).split()))
    else:
        number_words = {
            "one": "1",
            "two": "2",
            "three": "3",
            "four": "4",
            "five": "5",
            "six": "6",
            "seven": "7",
            "eight": "8",
            "nine": "9",
            "ten": "10",
        }
        table = str.maketrans({character: " " for character in punctuation})
        expected = [
            number_words.get(token, token)
            for token in reference.casefold().translate(table).split()
        ]
        actual = [
            number_words.get(token, token)
            for token in hypothesis.casefold().translate(table).split()
        ]
    return edit_distance(expected, actual) / max(1, len(expected))
