from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLE_SOURCE = ROOT / "firmware/src/CodexBleController.cpp"
MAIN_SOURCE = ROOT / "firmware/src/main.cpp"
FACE_SOURCE = ROOT / "firmware/src/FaceRenderer.cpp"
FACE_HEADER = ROOT / "firmware/include/FaceRenderer.hpp"
CODEX_ICONS = ROOT / "firmware/include/CodexIcons.hpp"
AUDIO_SOURCE = ROOT / "firmware/src/AudioEndpoint.cpp"
APP_SOURCE = ROOT / "server/src/stackchan_agent/app.py"

REPORT_LENGTH = 63
CHUNK_LENGTH = 61
CHANNEL_JSON_RPC = 2


def frame(payload: str) -> list[bytes]:
    encoded = payload.encode()
    reports = []
    for offset in range(0, len(encoded), CHUNK_LENGTH):
        chunk = encoded[offset : offset + CHUNK_LENGTH]
        reports.append(
            bytes([CHANNEL_JSON_RPC, len(chunk)]) + chunk.ljust(CHUNK_LENGTH, b"\0")
        )
    return reports


def reassemble(reports: list[bytes]) -> str:
    return b"".join(report[2 : 2 + report[1]] for report in reports).decode()


def test_vendor_reports_are_fixed_size_and_reassemble_fragmented_rpc() -> None:
    payload = json.dumps(
        {
            "method": "v.oai.thstatus",
            "params": [
                {"id": index, "c": 0x304FFE, "b": 1, "e": 2, "s": 1}
                for index in range(6)
            ],
            "id": 23,
        },
        separators=(",", ":"),
    )
    reports = frame(payload)
    assert len(reports) > 1
    assert all(len(report) == REPORT_LENGTH for report in reports)
    assert all(
        report[0] == CHANNEL_JSON_RPC and report[1] <= CHUNK_LENGTH
        for report in reports
    )
    assert reassemble(reports) == payload


def test_firmware_locks_protocol_constants_and_crlf_termination() -> None:
    source = BLE_SOURCE.read_text()
    header = (ROOT / "firmware/include/CodexBleController.hpp").read_text()
    assert "kVendorReportId = 6" in header
    assert "kReportLength = 63" in header
    assert "kChunkLength = 61" in header
    assert '"v.oai.thstatus"' in source
    assert 'payload += "\\r\\n"' in source
    assert "0x85, 0x06" in source  # vendor report ID 6 in the HID descriptor


def test_device_status_serializes_charging_state_as_json_boolean() -> None:
    source = BLE_SOURCE.read_text()
    assert 'result["is_charging"]' in source
    assert "M5.Power.isCharging() == m5::Power_Class::is_charging" in source
    assert 'result["is_charging"] = M5.Power.isCharging();' not in source


def test_all_six_status_colors_have_semantic_ui_states() -> None:
    source = BLE_SOURCE.read_text()
    expected = {
        "0x304FFE": "working",
        "0x00FF4C": "complete",
        "0xFF6D00": "needs_input",
        "0xFF0033": "error",
    }
    for color, state in expected.items():
        assert re.search(rf"{color}.*CodexAgentState::{state}", source)
    assert "CodexAgentState::idle" in source
    assert "CodexAgentState::off" in source


def test_touch_ui_maps_agents_actions_and_contextual_approval() -> None:
    main = MAIN_SOURCE.read_text()
    face = FACE_SOURCE.read_text()
    assert "codex.toggleFastMode()" in main
    assert "codex.togglePlanMode()" not in main
    assert "codex.startNewChat()" in main
    assert "codex.continueInNewChat()" in main
    assert "codex.steerQueuedFollowup()" in main
    assert "codex.decline() : codex.approve()" in main
    assert "codex.submitComposer()" in main
    assert "codex.setMicPressed(true)" in main
    assert "codex.setMicPressed(false)" in main
    assert "codex.selectAgent" in main
    assert "isCodexControlRelease(" in main
    assert 'Serial.printf("codex-ui: control release' in main
    assert "constrain(x / 50, 0, 5)" in main
    assert "detail.wasFlicked() || detail.wasDragged() || detail.wasReleased()" in main
    assert "abs(detail.distanceX()) >= 40" in main
    assert "M5.Touch.setFlickThresh(24)" in main
    assert 'Serial.printf("codex-ui: swipe' in main
    assert "detail.distanceX() < 0" in main
    assert "detail.distanceX() > 0" in main
    assert "last_codex_motion_state" in main
    assert "motion.move(true, yaw, true, pitch" in main
    assert "last_codex_motion_agent" not in main
    assert "drawCodexLauncher" not in face
    assert 'drawCircle(82, 181, 20' in face
    assert 'drawCircle(238, 181, 20' in face
    assert 'fillRoundRect(8, 184, 304, 48' in face
    assert '"PLAN"' not in face
    for label in ('"FAST"', '"NEW"', '"FORK"', '"STEER"', '"ARCHIVE"'):
        assert label not in face
    assert '"CODEX CONTROL"' not in face
    assert '"VOICE PAUSED"' not in face
    assert 'drawString("<"' not in face


def test_approved_phosphor_fill_icons_are_rendered_as_firmware_bitmaps() -> None:
    face = FACE_SOURCE.read_text()
    icons = CODEX_ICONS.read_text()
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    expected = (
        "lightning_28",
        "chat_circle_dots_28",
        "git_fork_28",
        "archive_28",
        "arrow_bend_up_right_28",
        "microphone_36",
        "list_checks_20",
    )
    for name in expected:
        assert f"uint8_t {name}[] PROGMEM" in icons
        assert f"codex_icons::{name}" in face
    assert "Phosphor Icons" in notices
    assert (ROOT / "third_party/phosphor-icons/LICENSE").is_file()
    assert not re.search(r"void draw(?:Bolt|NewChat|Fork|Archive|Steer|Mic)Icon", face)


def test_codex_status_panel_is_factual_and_blocks_underlying_controls() -> None:
    main = MAIN_SOURCE.read_text()
    face = FACE_SOURCE.read_text()
    header = FACE_HEADER.read_text()
    assert "setCodexStatusOpen" in header
    assert "codexStatusOpen" in header
    assert "TASK STATUS" in face
    assert "Work is in progress" in face
    assert "Completed on laptop" in face
    assert 'codex_connected_ ? "Connected" : "Disconnected"' in face
    assert 'codex_queued_followup_ ? "Follow-up waiting" : "Nothing queued"' in face
    assert "y >= 59 && y < 124 && x >= 260" in main
    assert "if (face.codexStatusOpen()) return;" in main
    assert "!face.codexStatusOpen() && detail.y >= 181" in main


def test_avatar_hud_shows_ntp_time_and_live_battery_level() -> None:
    main = MAIN_SOURCE.read_text()
    face = FACE_SOURCE.read_text()
    local_config = (ROOT / "firmware/include/LocalConfig.example.hpp").read_text()
    assert "drawFaceHud(now_ms);" in face
    assert "M5.Power.getBatteryLevel()" in face
    assert "now_ms - last_battery_read_ms_ >= 30000" in face
    assert 'snprintf(battery_text, sizeof(battery_text), "%d%%", battery_level_)' in face
    assert "textdatum_t::middle_right" in face
    assert 'strftime(clock_text, sizeof(clock_text), "%H:%M"' in face
    assert 'configTzTime(STACKCHAN_TIMEZONE, "pool.ntp.org", "time.nist.gov")' in main
    assert '#define STACKCHAN_TIMEZONE "JST-9"' in local_config


def test_agent_touch_emits_double_activation_and_uses_duplex_safe_sounds() -> None:
    ble = BLE_SOURCE.read_text()
    main = MAIN_SOURCE.read_text()
    audio = AUDIO_SOURCE.read_text()
    assert "kAgentFocusTapGapMs = 55" in ble
    assert "const bool first = sendTap(key);" in ble
    assert "const bool second = sendTap(key);" in ble
    assert "face.setCodexSelectedAgent" in main
    for effect in (
        "agent_select",
        "fast",
        "assistant",
        "approve",
        "decline",
    ):
        assert f"UiSoundEffect::{effect}" in main
    assert "bool AudioEndpoint::playUiSound" in audio
    assert "playback_queue_" in audio
    assert "if (playback_active_ || playback_count_ != 0" in audio
    assert "M5.Speaker.tone" not in audio
    assert "audio.uiSoundActive()" in main
    assert "zeroPadUiSoundFrame(" in audio
    assert "uiSoundPcmFrameLength(" in audio
    assert "write16Be(kAw88298Address, 0x06, 0x14C5)" in audio
    assert "write16Be(kAw88298Address, 0x06, 0x14C7)" not in audio
    assert "const size_t writes = ui_sound_active_" in audio
    assert "Only the end of the complete cue is padded" in audio


def test_codex_controls_are_not_swallowed_by_voice_playback_guard() -> None:
    main = MAIN_SOURCE.read_text()
    codex_branch = main.index(
        "tap_route == stackchan::ScreenTapRoute::codex_control",
        main.index("void handleTouch"),
    )
    playback_guard = main.index(
        "tap_route == stackchan::ScreenTapRoute::interrupt_playback", codex_branch
    )
    select_agent = main.index("codex.selectAgent", codex_branch)
    assert codex_branch < select_agent < playback_guard
    assert "Codex controls above are exempt from this guard" in main


def test_codex_mode_does_not_replace_wifi_voice_transport() -> None:
    main = MAIN_SOURCE.read_text()
    assert "socket_client.loop();" in main
    assert "codex.update();" in main
    assert "connectWifiFromFactoryNvs" in main
    assert "WiFi.setSleep(true)" in main
    assert "WiFi.setSleep(false)" not in main
    assert "NimBLEDevice::deleteAllBonds" not in BLE_SOURCE.read_text()


def test_codex_audio_session_is_isolated_and_resumes_on_exit() -> None:
    main = MAIN_SOURCE.read_text()
    audio = AUDIO_SOURCE.read_text()
    app = APP_SOURCE.read_text()
    enter = main.index("void enterCodexMode()")
    exit_mode = main.index("void exitCodexMode()")
    touch = main.index("void handleTouch", exit_mode)
    assert "audio.setConversationPaused(true);" in main[enter:exit_mode]
    assert "flushAudioWithSensorGuard();" in main[enter:exit_mode]
    assert 'sendControl("conversation.suspend");' in main[enter:exit_mode]
    assert "audio.setConversationPaused(false);" in main[exit_mode:touch]
    assert 'sendControl("conversation.resume");' in main[exit_mode:touch]
    reconnect = main.index("if (face.codexMode()) {", main.index('type == "hello.ack"'))
    assert 'sendControl("conversation.suspend");' in main[reconnect : reconnect + 180]
    assert "sendCodexFocused(codex.selectedAgent());" in main[reconnect : reconnect + 180]
    assert "if (conversation_paused_) return;" in audio
    assert "if (conversation_paused_ || !connected_" in audio
    assert "if (audio.conversationPaused()) break;" in main
    assert 'command.type == "conversation.suspend"' in app
    assert 'command.type == "conversation.resume"' in app
    assert "await stop_playback(\"codex_mode\")" in app
    assert "if conversation_suspended:" in app
    assert "await conversation_resumed.wait()" in app


def test_archive_is_a_confirmed_native_keyboard_shortcut() -> None:
    main = MAIN_SOURCE.read_text()
    face = FACE_SOURCE.read_text()
    ble = BLE_SOURCE.read_text()
    policy = (ROOT / "firmware/include/CodexInteractionPolicy.hpp").read_text()
    assert "kCodexArchiveModifiers = 0x0A" in policy
    assert "kCodexArchiveKey = 0x04" in policy
    assert "g_keyboard_input = g_hid->getInputReport(1)" in ble
    assert "sendKeyboardChord(kCodexArchiveModifiers, kCodexArchiveKey)" in ble
    assert "codex.archiveThread()" in main
    assert "codex_archive_armed_until_ms = now_ms + 2500" in main
    assert "codex_archive_armed_" in face
    assert "fill = 0x543716" in face


def test_new_chat_uses_native_shortcut_and_queued_steer_uses_micro_action() -> None:
    main = MAIN_SOURCE.read_text()
    ble = BLE_SOURCE.read_text()
    policy = (ROOT / "firmware/include/CodexInteractionPolicy.hpp").read_text()
    assert "kCodexNewChatModifiers = 0x08" in policy
    assert "kCodexNewChatKey = 0x11" in policy
    assert "sendKeyboardChord(kCodexNewChatModifiers, kCodexNewChatKey)" in ble
    assert "bool CodexBleController::steerQueuedFollowup()" in ble
    assert "const bool sent = submitComposer();" in ble
    assert "codex.startNewChat()" in main
    assert "codex.continueInNewChat()" in main
    assert "codex.steerQueuedFollowup()" in main
    assert "codex_queued_followup = sent && was_working" in main
    assert "face.setCodexQueuedFollowup(codex_queued_followup)" in main


def test_codex_titles_are_bound_only_after_the_physical_slot_is_focused() -> None:
    app = APP_SOURCE.read_text()
    main = MAIN_SOURCE.read_text()
    face = FACE_SOURCE.read_text()
    assert "recent_codex_titles" in app
    assert 'command.type == "codex.focused"' in app
    assert '"codex.session", index=index, title=titles[0]' in app
    assert 'type == "codex.session"' in main
    assert 'document["type"] = "codex.focused"' in main
    assert 'document["payload"]["index"] = index' in main
    assert "sendCodexFocused(static_cast<uint8_t>(agent))" in main
    assert "sendCodexFocused(codex.selectedAgent())" in main
    assert 'control("codex.sessions", titles=titles)' not in app
    assert "face.setCodexAgentTitle" in main
    assert "codex_agent_titles_[codex_selected_agent_]" in face
    assert 'String("ACTIVE AGENT  ")' not in face


def test_mic_release_waits_for_host_completion_then_submits() -> None:
    main = MAIN_SOURCE.read_text()
    ble = BLE_SOURCE.read_text()
    assert 'strcmp(method, "v.oai.rgbcfg") == 0' in ble
    assert "decodeCodexVoiceLighting(effect, color)" in ble
    assert "codex_submit_pending = true;" in main
    assert "shouldSubmitCodexDictation(host_state, elapsed_ms)" in main
    assert "codex.submitComposer()" in main
    assert 'Serial.printf("codex-ui: dictation submit' in main


def test_current_codex_action_protocol() -> None:
    header = (ROOT / "firmware/include/CodexInteractionPolicy.hpp").read_text()
    ble = BLE_SOURCE.read_text()
    for value in (6, 7, 8, 9, 12):
        assert f"= {value}" in header
    assert 'event["m"] = "v.oai.rad";' not in ble
    assert "togglePlanMode" not in ble
