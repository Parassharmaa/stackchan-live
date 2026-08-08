import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_eve_intelligence.py"
SPEC = importlib.util.spec_from_file_location("benchmark_eve_intelligence", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_durability_log_scan_ignores_history_and_finds_new_corruption(tmp_path: Path) -> None:
    log = tmp_path / "eve.log"
    log.write_text(
        json.dumps({"at": "before", "detail": "REPLAY_DIVERGENCE"}) + "\n",
        encoding="utf-8",
    )
    offsets = MODULE.snapshot_log_offsets(tmp_path)

    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": "ordinary", "detail": "healthy"}) + "\n")
        handle.write(
            json.dumps({"at": "after", "detail": "CORRUPTED_EVENT_LOG"}) + "\n"
        )

    assert MODULE.new_workflow_durability_events(tmp_path, offsets) == [
        {"marker": "CORRUPTED_EVENT_LOG", "at": "after", "log": "eve.log"}
    ]


def test_durability_log_scan_reads_a_log_created_during_run(tmp_path: Path) -> None:
    offsets = MODULE.snapshot_log_offsets(tmp_path)
    (tmp_path / "new.log").write_text("REPLAY_DIVERGENCE\n", encoding="utf-8")

    assert MODULE.new_workflow_durability_events(tmp_path, offsets) == [
        {"marker": "REPLAY_DIVERGENCE", "at": "", "log": "new.log"}
    ]


def test_durability_log_scan_combines_directory_and_headless_log(tmp_path: Path) -> None:
    structured = tmp_path / "structured"
    structured.mkdir()
    diagnostic = structured / "dev.log"
    headless = tmp_path / "eve.log"
    diagnostic.write_text("healthy\n", encoding="utf-8")
    headless.write_text("healthy\n", encoding="utf-8")
    sources = (structured, headless)
    offsets = MODULE.snapshot_log_offsets(sources)

    with diagnostic.open("a", encoding="utf-8") as handle:
        handle.write("REPLAY_DIVERGENCE\n")
    with headless.open("a", encoding="utf-8") as handle:
        handle.write("CORRUPTED_EVENT_LOG\n")

    assert MODULE.new_workflow_durability_events(sources, offsets) == [
        {"marker": "CORRUPTED_EVENT_LOG", "at": "", "log": "eve.log"},
        {"marker": "REPLAY_DIVERGENCE", "at": "", "log": "dev.log"},
    ]
