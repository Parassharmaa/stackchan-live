from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from stackchan_agent.codex_sessions import discover_codex_state_db, recent_codex_titles


def _create_state_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            name TEXT,
            title TEXT NOT NULL,
            first_user_message TEXT NOT NULL,
            archived INTEGER NOT NULL,
            preview TEXT NOT NULL,
            recency_at_ms INTEGER NOT NULL
        )
        """
    )
    connection.executemany(
        "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("old", None, "Older task", "old", 0, "visible", 10),
            ("new", "Named task", "Fallback", "new", 0, "visible", 30),
            ("hidden", None, "Hidden", "hidden", 0, "", 40),
            ("archived", None, "Archived", "archived", 1, "visible", 50),
        ],
    )
    connection.commit()
    connection.close()


def test_recent_codex_titles_are_read_only_visible_and_ordered(tmp_path: Path) -> None:
    state_db = tmp_path / "state_5.sqlite"
    _create_state_db(state_db)

    assert recent_codex_titles(state_db=state_db) == ["Named task", "Older task"]


def test_title_is_whitespace_normalized_and_bounded(tmp_path: Path) -> None:
    state_db = tmp_path / "state_6.sqlite"
    _create_state_db(state_db)
    connection = sqlite3.connect(state_db)
    connection.execute(
        "UPDATE threads SET name = ? WHERE id = 'new'",
        ("A very long\nchat title " + "x" * 80,),
    )
    connection.commit()
    connection.close()

    title = recent_codex_titles(state_db=state_db, limit=1)[0]
    assert "\n" not in title
    assert len(title) == 48
    assert title.endswith("...")


def test_discovery_uses_latest_state_database(tmp_path: Path) -> None:
    older = tmp_path / "state_4.sqlite"
    newer = tmp_path / "state_5.sqlite"
    older.touch()
    newer.touch()
    os.utime(older, ns=(1, 1))
    os.utime(newer, ns=(2, 2))

    assert discover_codex_state_db(tmp_path) == newer


def test_missing_or_invalid_state_database_is_non_fatal(tmp_path: Path) -> None:
    assert recent_codex_titles(state_db=tmp_path / "missing.sqlite") == []
    invalid = tmp_path / "state_5.sqlite"
    invalid.write_text("not sqlite")
    assert recent_codex_titles(state_db=invalid) == []
