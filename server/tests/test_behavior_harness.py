import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
AUDIT_SCRIPT = SCRIPTS / "audit_conversation_trace.py"
AUDIT_SPEC = importlib.util.spec_from_file_location(
    "audit_conversation_trace", AUDIT_SCRIPT
)
assert AUDIT_SPEC and AUDIT_SPEC.loader
AUDIT_MODULE = importlib.util.module_from_spec(AUDIT_SPEC)
sys.modules[AUDIT_SPEC.name] = AUDIT_MODULE
AUDIT_SPEC.loader.exec_module(AUDIT_MODULE)

SCRIPT = SCRIPTS / "benchmark_text_behaviors.py"
SPEC = importlib.util.spec_from_file_location("benchmark_text_behaviors", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_memory_behavior_does_not_reward_a_silent_wake_reply() -> None:
    result = MODULE.judge_memory_behavior(
        {"memories": [], "response": ""},
        {
            "memories": ["ユーザーはパラスと呼ばれたいです。"],
            "response": "パラス",
        },
    )

    assert result["occurrences"][0]["verdict"] == "false"
    assert result["verdict"] == "false"


def test_embodied_behavior_does_not_reward_a_silent_state_reply() -> None:
    result = MODULE.judge_embodied_behavior(
        {"response": ""},
        {"response": "Would you like me to take one photo?"},
        [],
        ["move_head", "capture_photo"],
        [],
    )

    assert result["occurrences"][0]["verdict"] == "false"
    assert result["verdict"] == "false"
