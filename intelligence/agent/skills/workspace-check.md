---
description: Run an explicitly authorized one-shot or scheduled workspace check using one local camera still and a concise grounded spoken observation.
---

Use this skill only when the user directly asks Stack-chan to inspect the visible workspace, or explicitly authorizes one still at each scheduled occurrence. For a scheduled check, require the cadence, IANA timezone, quiet hours, and an explicit recurring-camera choice before calling `create_schedule`. Use the `curious` routine and a prompt that asks for one factual visual summary plus at most one practical observation. Treat local Vision output as incomplete evidence: mention only visible objects or conditions it reports, never identify people, infer sensitive traits, claim security monitoring, or report that an unseen area is safe. Make the schedule easy to list, pause, and delete. If nothing useful is visible, say so briefly instead of inventing a finding.
