from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLE_SOURCE = ROOT / "firmware/src/CodexBleController.cpp"
MAIN_SOURCE = ROOT / "firmware/src/main.cpp"
FACE_SOURCE = ROOT / "firmware/src/FaceRenderer.cpp"
AUDIO_SOURCE = ROOT / "firmware/src/AudioEndpoint.cpp"

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
    for action in (0, 3, 4):
        assert f"codex.sendAction({action})" in main
    assert "codex.sendAction(decline ? 2 : 1)" in main
    assert "codex.setMicPressed(true)" in main
    assert "codex.setMicPressed(false)" in main
    assert "codex.selectAgent" in main
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
    assert '"DECLINE"' in face
    assert '"APPROVE"' in face
    assert '"FAST"' in face and '"PLAN"' in face and '"HOLD MIC"' in face


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
        "plan",
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


def test_codex_mode_does_not_replace_wifi_voice_transport() -> None:
    main = MAIN_SOURCE.read_text()
    assert "socket_client.loop();" in main
    assert "codex.update();" in main
    assert "connectWifiFromFactoryNvs" in main
    assert "WiFi.setSleep(true)" in main
    assert "WiFi.setSleep(false)" not in main
    assert "NimBLEDevice::deleteAllBonds" not in BLE_SOURCE.read_text()
