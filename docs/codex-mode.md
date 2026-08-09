# Codex mode

Stack-chan can act as a BLE controller for Codex while its Wi-Fi voice runtime
continues normally.

## Pair

1. Flash the firmware with `pixi run firmware-upload`.
2. Open macOS Bluetooth settings and pair **Stack-chan Codex**.
3. Follow the setup prompt in ChatGPT and allow Input Monitoring on macOS.
   Later, configure it from **ChatGPT Settings > Codex Micro**.
4. Swipe left on Stack-chan's face. Swipe right to return.

The top row selects one of six agents. Their colors mean ready (white), working
(blue), complete (green), needs input (amber), error (red), or unassigned
(dark). The bottom row provides Fast, Plan, AI/new-task, and hold-to-talk actions.
When an agent needs approval, it becomes large Decline and Approve buttons.

Stack-chan's body lights mirror the selected agent. Bluetooth bonds and the
factory Wi-Fi credentials are both retained across normal firmware uploads.
Its head adopts one restrained pose when the host reports working, completion,
approval, or error. Touch navigation never moves the head, and motion pauses
during speech.

This is a community-built compatible controller, not official Codex Micro
hardware. See [third-party notices](../THIRD_PARTY_NOTICES.md).
