import importlib.util
import re
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_text_behaviors.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("benchmark_text_behaviors", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_behavior_specs_are_structurally_valid_and_judgeable() -> None:
    root = Path(__file__).resolve().parents[2] / ".agents" / "behaviors"
    specs = sorted(root.glob("*/BEHAVIOR.md"))

    assert {path.parent.name for path in specs} == {
        "grounded-embodied-actions",
        "relevant-personal-memory",
    }
    for path in specs:
        parts = path.read_text(encoding="utf-8").split("---", 2)
        assert parts[0] == ""
        metadata = dict(
            line.split(":", 1) for line in parts[1].strip().splitlines() if ":" in line
        )
        name = metadata["name"].strip()
        description = metadata["description"].strip()
        body = parts[2].strip()
        assert name == path.parent.name
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
        assert len(name) <= 64
        assert 0 < len(description) <= 1_024
        assert body.startswith("# ")
        assert "**Failure modes:**" in body


def test_fold_verdicts_does_not_turn_all_not_applicable_into_a_pass() -> None:
    assert MODULE.fold_verdicts(["na", "na"]) == "na"
    assert MODULE.fold_verdicts(["na", "true"]) == "true"
    assert MODULE.fold_verdicts(["true", "false", "na"]) == "false"


def test_memory_behavior_calibrates_positive_and_negative_trajectories() -> None:
    positive = MODULE.judge_memory_behavior(
        {"memories": [], "response": "はい、ここにいるよ。"},
        {
            "memories": ["ユーザーはパラスと呼ばれたいです。"],
            "response": "You prefer to be called パラス.",
        },
    )
    negative = MODULE.judge_memory_behavior(
        {
            "memories": ["私はコーヒーが好きです。"],
            "response": "コーヒーが好きなんですね。",
        },
        {
            "memories": ["ユーザーはパラスと呼ばれたいです。"],
            "response": "You prefer to be called Palas.",
        },
    )

    assert positive["verdict"] == "true"
    assert negative["verdict"] == "false"


def test_embodied_behavior_calibrates_positive_negative_and_outside_scope() -> None:
    positive = MODULE.judge_embodied_behavior(
        {"response": "どの色にしたい？"},
        {"response": "Would you like me to take one camera still?"},
        [],
        ["move_head", "capture_photo"],
        [],
    )
    lucky_correct_negative = MODULE.judge_embodied_behavior(
        {"response": "今、ライトは青になっています。"},
        {"response": "Would you like me to take one camera still?"},
        [],
        [],
        [],
    )

    assert positive["verdict"] == "true"
    assert positive["occurrences"][2]["verdict"] == "na"
    assert lucky_correct_negative["verdict"] == "false"
