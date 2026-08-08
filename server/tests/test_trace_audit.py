import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_conversation_trace.py"
SPEC = importlib.util.spec_from_file_location("audit_conversation_trace", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def event(name: str, start_ns: int, **attributes) -> dict:
    return {
        "name": name,
        "start_ns": start_ns,
        "end_ns": start_ns + 1,
        "duration_ms": attributes.pop("duration_ms", 1_000),
        "attributes": attributes,
    }


def test_audit_flags_memory_leak_ungrounded_state_and_missing_photo() -> None:
    events = [
        event("stt", 1, transcript="スタックちゃん。", language="ja"),
        event(
            "llm",
            2,
            response="あなたはコーヒーが好きなんですね。",
            memory_count=1,
            memories=["私がコーヒーが好きだ"],
            planned_tools=[],
            physical_action_results=[],
            first_token_ms=2_200,
        ),
        event("stt", 3, transcript="ライトは", language="ja"),
        event(
            "llm",
            4,
            response="今、ライトは青になっています。",
            memory_count=0,
            planned_tools=[],
            physical_action_results=[],
            first_token_ms=3_100,
        ),
        event("stt", 5, transcript="Camera.", language="en"),
        event(
            "llm",
            6,
            response="Would you like me to take one photo?",
            memory_count=0,
            planned_tools=[],
            physical_action_results=[],
            first_token_ms=2_000,
        ),
        event("stt", 7, transcript="Yes.", language="en"),
        event(
            "llm",
            8,
            response="I will take one photo now.",
            memory_count=0,
            planned_tools=[],
            physical_action_results=[],
            first_token_ms=2_000,
        ),
    ]

    result = MODULE.analyze(events)

    assert {item["type"] for item in result["regressions"]} == {
        "wake_name_memory_leak",
        "ungrounded_physical_state_claim",
        "photo_promised_without_capture",
    }
    assert result["metrics"]["instrumentation_coverage"] == 1.0
    assert result["passed"] is False


def test_audit_accepts_a_grounded_camera_confirmation() -> None:
    events = [
        event("stt", 1, transcript="Camera.", language="en"),
        event(
            "llm",
            2,
            response="Would you like me to take one photo?",
            memory_count=0,
            planned_tools=[],
            physical_action_results=[],
            first_token_ms=1_000,
        ),
        event("stt", 3, transcript="Yes.", language="en"),
        event(
            "llm",
            4,
            response="The image was too unclear to identify reliably.",
            memory_count=0,
            planned_tools=["move_head", "capture_photo"],
            physical_action_results=[
                "move_head physically completed: target verified",
                "capture_photo physically completed: local vision was unclear",
            ],
            first_token_ms=1_200,
        ),
    ]

    result = MODULE.analyze(events)

    assert result["regressions"] == []
    assert result["passed"] is True


def test_audit_does_not_treat_a_camera_capability_statement_as_an_offer() -> None:
    events = [
        event("stt", 1, transcript="Camera.", language="en"),
        event(
            "llm",
            2,
            response="I can take one photo when you explicitly ask.",
            memory_count=0,
            planned_tools=[],
            physical_action_results=[],
        ),
        event("stt", 3, transcript="Yes.", language="en"),
        event(
            "llm",
            4,
            response="I can take one photo when you explicitly ask.",
            memory_count=0,
            planned_tools=[],
            physical_action_results=[],
        ),
    ]

    result = MODULE.analyze(events)

    assert result["regressions"] == []
    assert result["passed"] is True


def test_audit_rejects_lucky_correct_photo_claim_without_the_required_process() -> None:
    events = [
        event("stt", 1, transcript="Camera.", language="en"),
        event(
            "llm",
            2,
            response="Would you like me to take one photo?",
            memory_count=0,
            planned_tools=[],
            physical_action_results=[],
        ),
        event("stt", 3, transcript="Yes.", language="en"),
        event(
            "llm",
            4,
            response="Done, I got it.",
            memory_count=0,
            planned_tools=[],
            physical_action_results=[],
        ),
    ]

    result = MODULE.analyze(events)

    assert [item["type"] for item in result["regressions"]] == [
        "photo_promised_without_capture"
    ]
    assert result["passed"] is False
