# Codex mode

Stack-chan can act as a BLE controller for Codex. Its Wi-Fi connection stays
alive while conversation audio pauses, so the two interfaces do not compete for
the microphone or speaker.

## Pair

1. Flash the firmware with `pixi run firmware-upload`.
2. Open macOS Bluetooth settings and pair **Stack-chan Codex**.
3. Follow the setup prompt in ChatGPT and allow Input Monitoring on macOS.
   Later, configure it from **ChatGPT Settings > Codex Micro**.
4. Swipe left on Stack-chan's face. Swipe right to return.

The large top row selects one of six tasks without moving Stack-chan's head. Their
colors mean ready (white), working (blue), complete (green), needs input
(amber), error (red), or unassigned (dark). When available, the Mac runtime
shows each selected task's actual Codex title in the center card. The icon row provides Fast, a
fresh New Chat, Fork (continue the current context in a new chat), two-tap
Archive, one-shot Steer, and hold-to-talk. Releasing hold-to-talk waits for
desktop transcription to finish and sends the resulting message automatically.
After Stack-chan queues a follow-up during a running task, Steer lights up and
uses Codex Micro's native submit action to promote the first queued message into
the active turn. When a task needs approval, the controls become large decline
and approve icons.

Stack-chan's body lights mirror the selected agent. Bluetooth bonds and the
factory Wi-Fi credentials are both retained across normal firmware uploads.
Codex mode suspends Stack-chan's laptop conversation pipeline, microphone, and
voice playback so UI cues and desktop dictation cannot overlap. Swipe right to
resume the live voice interface.
Its head adopts one restrained pose when the host reports working, completion,
approval, or error. Touch navigation never moves the head, and motion pauses
during speech.

This is a community-built compatible controller, not official Codex Micro
hardware. See [third-party notices](../THIRD_PARTY_NOTICES.md).
