from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN_SOURCE = ROOT / "firmware/src/main.cpp"
LIGHT_SOURCE = ROOT / "firmware/src/LightController.cpp"
LIGHT_HEADER = ROOT / "firmware/include/LightController.hpp"


def test_explicit_light_scene_survives_reply_and_idle_until_next_turn() -> None:
    source = MAIN_SOURCE.read_text()

    assert "bool held_lights_active = false;" in source
    assert 'if (state == "listening") clearHeldLights();' in source
    assert source.count("if (held_lights_active) {") >= 2
    assert "applyHeldLights();" in source
    assert "held_lights_active = true;" in source
    assert "held_light_animation =" in source


def test_light_driver_configures_stackchan_rgb_data_pin_before_frames() -> None:
    source = LIGHT_SOURCE.read_text()
    header = LIGHT_HEADER.read_text()

    assert "kLedDataPinMask = 1 << (13 - 8)" in header
    assert "bool LightController::configureDataPin()" in source
    assert "mode |= kLedDataPinMask" in source
    assert "pull_up |= kLedDataPinMask" in source
    assert "pull_down &= ~kLedDataPinMask" in source
    assert "drive &= ~kLedDataPinMask" in source
    assert "const uint8_t led_count = kLedCount" in source
    assert "delay(200);" in source
    assert "const uint32_t deadline = millis() + 1400" in source
