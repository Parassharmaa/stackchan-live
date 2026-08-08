---
description: Design safe opt-in scheduled check-ins that combine conversation, memory, routines, and one-shot sensor or camera observations.
---

For a scheduled check-in, require an explicit cadence, IANA timezone, and quiet-hours boundary before enabling anything. Prefer lightweight prompts such as a morning greeting, focus reminder, stretch cue, or bedtime wind-down. Use remembered preferences only when relevant, never wake the user with unsolicited music, and never capture the camera unless that specific recurring capture was separately authorized. Use `create_schedule` only after every required field is explicit. Use `list_schedules`, `set_schedule_enabled`, and `delete_schedule` so every schedule remains visible, pausable, resumable, and removable.
