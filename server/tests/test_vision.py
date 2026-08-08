from stackchan_agent.vision import summarize_vision


def test_vision_summary_filters_weak_labels() -> None:
    summary = summarize_vision(
        {
            "faceCount": 1,
            "labels": [
                {"name": "coffee_cup", "confidence": 0.82},
                {"name": "moon", "confidence": 0.05},
            ],
            "text": ["Stack-chan"],
        }
    )

    assert summary == "detected 1 face; likely scene labels: coffee cup; readable text: Stack-chan"


def test_vision_summary_is_honest_when_nothing_is_confident() -> None:
    summary = summarize_vision(
        {"faceCount": 0, "labels": [{"name": "moon", "confidence": 0.05}], "text": []}
    )

    assert summary == "local vision could not identify the scene confidently"
