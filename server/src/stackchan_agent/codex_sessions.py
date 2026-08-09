from __future__ import annotations

import re
import sqlite3
from pathlib import Path


def discover_codex_state_db(codex_home: Path | None = None) -> Path | None:
    root = codex_home or Path.home() / ".codex"
    candidates = list(root.glob("state_*.sqlite"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _display_title(value: str, limit: int = 48) -> str:
    title = re.sub(r"\s+", " ", value).strip()
    if len(title) <= limit:
        return title
    return title[: limit - 3].rstrip() + "..."


def recent_codex_titles(
    limit: int = 6, *, state_db: Path | None = None
) -> list[str]:
    """Read recent visible task titles without modifying Codex state."""
    path = state_db or discover_codex_state_db()
    if path is None or not path.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)
        try:
            rows = connection.execute(
                """
                SELECT COALESCE(NULLIF(name, ''), NULLIF(title, ''),
                                NULLIF(first_user_message, ''))
                FROM threads
                WHERE archived = 0 AND preview <> ''
                ORDER BY recency_at_ms DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        return []
    return [_display_title(str(row[0])) for row in rows if row[0]]
