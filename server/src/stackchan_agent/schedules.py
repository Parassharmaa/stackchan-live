import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROUTINES = {
    "greet",
    "celebrate",
    "curious",
    "comfort",
    "dance",
    "wake_up",
    "focus",
    "good_night",
}


@dataclass(frozen=True, slots=True)
class Schedule:
    id: int
    device_id: str
    label: str
    prompt: str
    language: str
    routine: str
    music: bool
    capture_photo: bool
    recurrence: str
    timezone: str
    local_time: str
    quiet_start: str
    quiet_end: str
    next_fire_at: float
    enabled: bool
    lease_until: float | None
    last_status: str | None
    last_fired_at: float | None
    created_at: float
    updated_at: float


def _clock(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value)
    if match is None:
        raise ValueError("time must use 24-hour HH:MM format")
    return int(match.group(1)), int(match.group(2))


def _zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ValueError("timezone must be a valid IANA timezone") from error


def _inside_quiet_hours(local: datetime, start: str, end: str) -> bool:
    start_clock = _clock(start)
    end_clock = _clock(end)
    current = (local.hour, local.minute)
    if start_clock == end_clock:
        return False
    if start_clock < end_clock:
        return start_clock <= current < end_clock
    return current >= start_clock or current < end_clock


def _after_quiet_hours(local: datetime, start: str, end: str) -> datetime:
    if not _inside_quiet_hours(local, start, end):
        return local
    end_hour, end_minute = _clock(end)
    candidate = local.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


def first_fire_at(
    *,
    recurrence: str,
    local_time: str,
    timezone_name: str,
    quiet_start: str,
    quiet_end: str,
    now: float | None = None,
) -> float:
    zone = _zone(timezone_name)
    now_utc = datetime.fromtimestamp(now if now is not None else time.time(), UTC)
    local_now = now_utc.astimezone(zone)
    if recurrence == "once":
        try:
            candidate = datetime.fromisoformat(local_time)
        except ValueError as error:
            raise ValueError("one-shot local_time must be YYYY-MM-DDTHH:MM") from error
        if candidate.tzinfo is not None:
            raise ValueError("one-shot local_time must not include an offset")
        candidate = candidate.replace(tzinfo=zone, second=0, microsecond=0)
        if candidate <= local_now:
            raise ValueError("one-shot time must be in the future")
    elif recurrence == "daily":
        hour, minute = _clock(local_time)
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(days=1)
    else:
        raise ValueError("recurrence must be once or daily")
    return _after_quiet_hours(candidate, quiet_start, quiet_end).astimezone(UTC).timestamp()


def next_daily_fire(schedule: Schedule, *, after: float) -> float:
    zone = _zone(schedule.timezone)
    local_after = datetime.fromtimestamp(after, UTC).astimezone(zone)
    hour, minute = _clock(schedule.local_time)
    candidate = local_after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_after:
        candidate += timedelta(days=1)
    return (
        _after_quiet_hours(candidate, schedule.quiet_start, schedule.quiet_end)
        .astimezone(UTC)
        .timestamp()
    )


class ScheduleStore:
    """Durable, local-only schedules with short execution leases."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                label TEXT NOT NULL,
                prompt TEXT NOT NULL,
                language TEXT NOT NULL,
                routine TEXT NOT NULL,
                music INTEGER NOT NULL,
                capture_photo INTEGER NOT NULL,
                recurrence TEXT NOT NULL,
                timezone TEXT NOT NULL,
                local_time TEXT NOT NULL,
                quiet_start TEXT NOT NULL,
                quiet_end TEXT NOT NULL,
                next_fire_at REAL NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                lease_until REAL,
                last_status TEXT,
                last_fired_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS schedules_due ON schedules(enabled, next_fire_at)"
        )
        self.connection.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> Schedule:
        values = dict(row)
        values["music"] = bool(values["music"])
        values["capture_photo"] = bool(values["capture_photo"])
        values["enabled"] = bool(values["enabled"])
        return Schedule(**values)

    def create(
        self,
        *,
        device_id: str,
        label: str,
        prompt: str,
        language: str,
        routine: str,
        music: bool,
        capture_photo: bool,
        recurrence: str,
        timezone_name: str,
        local_time: str,
        quiet_start: str,
        quiet_end: str,
        now: float | None = None,
    ) -> Schedule:
        if language not in {"en", "ja"}:
            raise ValueError("language must be en or ja")
        if routine not in ROUTINES:
            raise ValueError("routine is not allowlisted")
        label = " ".join(label.split())
        prompt = " ".join(prompt.split())
        if not label or len(label) > 80:
            raise ValueError("label must contain 1 to 80 characters")
        if not prompt or len(prompt) > 500:
            raise ValueError("prompt must contain 1 to 500 characters")
        if music and routine != "dance":
            raise ValueError("scheduled music is available only with the dance routine")
        _clock(quiet_start)
        _clock(quiet_end)
        timestamp = now if now is not None else time.time()
        next_fire = first_fire_at(
            recurrence=recurrence,
            local_time=local_time,
            timezone_name=timezone_name,
            quiet_start=quiet_start,
            quiet_end=quiet_end,
            now=timestamp,
        )
        cursor = self.connection.execute(
            """
            INSERT INTO schedules (
                device_id, label, prompt, language, routine, music, capture_photo,
                recurrence, timezone, local_time, quiet_start, quiet_end,
                next_fire_at, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                device_id,
                label,
                prompt,
                language,
                routine,
                int(music),
                int(capture_photo),
                recurrence,
                timezone_name,
                local_time,
                quiet_start,
                quiet_end,
                next_fire,
                timestamp,
                timestamp,
            ),
        )
        self.connection.commit()
        return self.get(int(cursor.lastrowid))

    def get(self, schedule_id: int) -> Schedule:
        row = self.connection.execute(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
        ).fetchone()
        if row is None:
            raise KeyError(schedule_id)
        return self._row(row)

    def list(self, device_id: str, *, include_disabled: bool = False) -> list[Schedule]:
        query = "SELECT * FROM schedules WHERE device_id = ?"
        if not include_disabled:
            query += " AND enabled = 1"
        query += " ORDER BY next_fire_at, id"
        return [self._row(row) for row in self.connection.execute(query, (device_id,)).fetchall()]

    def set_enabled(self, schedule_id: int, device_id: str, enabled: bool) -> Schedule:
        schedule = self.get(schedule_id)
        if schedule.device_id != device_id:
            raise KeyError(schedule_id)
        if (
            enabled
            and schedule.recurrence == "once"
            and schedule.last_status == "completed"
        ):
            raise ValueError("a completed one-shot schedule cannot be resumed")
        now = time.time()
        cursor = self.connection.execute(
            """
            UPDATE schedules
            SET enabled = ?, lease_until = NULL, updated_at = ?
            WHERE id = ? AND device_id = ?
            """,
            (int(enabled), now, schedule_id, device_id),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            raise KeyError(schedule_id)
        return self.get(schedule_id)

    def delete(self, schedule_id: int, device_id: str) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM schedules WHERE id = ? AND device_id = ?",
            (schedule_id, device_id),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def claim_due(
        self, device_id: str, *, now: float | None = None, lease_seconds: float = 120
    ) -> Schedule | None:
        timestamp = now if now is not None else time.time()
        with self.connection:
            row = self.connection.execute(
                """
                SELECT * FROM schedules
                WHERE device_id = ? AND enabled = 1 AND next_fire_at <= ?
                  AND (lease_until IS NULL OR lease_until <= ?)
                ORDER BY next_fire_at, id LIMIT 1
                """,
                (device_id, timestamp, timestamp),
            ).fetchone()
            if row is None:
                return None
            cursor = self.connection.execute(
                """
                UPDATE schedules SET lease_until = ?, last_status = 'claimed', updated_at = ?
                WHERE id = ? AND (lease_until IS NULL OR lease_until <= ?)
                """,
                (timestamp + lease_seconds, timestamp, int(row["id"]), timestamp),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(int(row["id"]))

    def complete(self, schedule_id: int, *, fired_at: float | None = None) -> Schedule:
        schedule = self.get(schedule_id)
        timestamp = fired_at if fired_at is not None else time.time()
        if schedule.recurrence == "daily":
            next_fire = next_daily_fire(schedule, after=timestamp)
            enabled = 1
        else:
            next_fire = schedule.next_fire_at
            enabled = 0
        self.connection.execute(
            """
            UPDATE schedules
            SET enabled = ?, next_fire_at = ?, lease_until = NULL,
                last_status = 'completed', last_fired_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (enabled, next_fire, timestamp, timestamp, schedule_id),
        )
        self.connection.commit()
        return self.get(schedule_id)

    def release(self, schedule_id: int, status: str, *, retry_at: float | None = None) -> None:
        timestamp = time.time()
        self.connection.execute(
            """
            UPDATE schedules
            SET lease_until = NULL, last_status = ?, next_fire_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status[:40],
                retry_at if retry_at is not None else timestamp + 60,
                timestamp,
                schedule_id,
            ),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
