from datetime import UTC, datetime
from pathlib import Path

import pytest

from stackchan_agent.schedules import ScheduleStore, first_fire_at


def epoch(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp()


def test_daily_schedule_respects_timezone_and_overnight_quiet_hours(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "schedules.sqlite3")
    schedule = store.create(
        device_id="stack-1",
        label="Early greeting",
        prompt="Give me a gentle morning greeting.",
        language="en",
        routine="wake_up",
        music=False,
        capture_photo=False,
        recurrence="daily",
        timezone_name="Asia/Tokyo",
        local_time="06:30",
        quiet_start="22:00",
        quiet_end="07:00",
        now=epoch("2026-08-08T20:00:00"),
    )

    assert schedule.next_fire_at == epoch("2026-08-08T22:00:00")
    store.close()


def test_claim_complete_and_restart_preserve_daily_schedule(tmp_path: Path) -> None:
    path = tmp_path / "schedules.sqlite3"
    now = epoch("2026-08-08T00:00:00")
    store = ScheduleStore(path)
    schedule = store.create(
        device_id="stack-1",
        label="Focus check",
        prompt="Ask whether I am ready for a focused work session.",
        language="en",
        routine="focus",
        music=False,
        capture_photo=False,
        recurrence="daily",
        timezone_name="UTC",
        local_time="00:01",
        quiet_start="23:00",
        quiet_end="00:00",
        now=now,
    )
    assert store.claim_due("stack-1", now=now + 30) is None
    claimed = store.claim_due("stack-1", now=now + 60)
    assert claimed is not None and claimed.id == schedule.id
    assert store.claim_due("stack-1", now=now + 61) is None
    completed = store.complete(schedule.id, fired_at=now + 62)
    assert completed.last_status == "completed"
    assert completed.next_fire_at == epoch("2026-08-09T00:01:00")
    store.close()

    reopened = ScheduleStore(path)
    assert reopened.get(schedule.id).next_fire_at == completed.next_fire_at
    reopened.close()


def test_one_shot_disables_after_completion_and_can_be_deleted(tmp_path: Path) -> None:
    store = ScheduleStore(tmp_path / "schedules.sqlite3")
    now = epoch("2026-08-08T00:00:00")
    schedule = store.create(
        device_id="stack-1",
        label="Stretch",
        prompt="Remind me to stretch.",
        language="en",
        routine="greet",
        music=False,
        capture_photo=False,
        recurrence="once",
        timezone_name="UTC",
        local_time="2026-08-08T00:01",
        quiet_start="23:00",
        quiet_end="07:00",
        now=now,
    )
    store.complete(schedule.id, fired_at=now + 60)
    assert store.list("stack-1") == []
    assert store.list("stack-1", include_disabled=True)[0].enabled is False
    with pytest.raises(ValueError, match="cannot be resumed"):
        store.set_enabled(schedule.id, "stack-1", True)
    assert store.delete(schedule.id, "stack-1") is True
    assert store.delete(schedule.id, "stack-1") is False
    store.close()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timezone_name": "Mars/Olympus"}, "IANA timezone"),
        ({"recurrence": "weekly"}, "once or daily"),
        ({"local_time": "yesterday"}, "YYYY-MM-DDTHH:MM"),
    ],
)
def test_invalid_schedule_input_fails_closed(kwargs: dict[str, str], message: str) -> None:
    values = {
        "recurrence": "once",
        "local_time": "2026-08-08T00:01",
        "timezone_name": "UTC",
        "quiet_start": "23:00",
        "quiet_end": "07:00",
        "now": epoch("2026-08-08T00:00:00"),
        **kwargs,
    }
    with pytest.raises(ValueError, match=message):
        first_fire_at(**values)
