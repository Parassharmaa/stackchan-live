#include "FaceRenderer.hpp"

#include <time.h>

#include "CodexIcons.hpp"
#include "generated/FaceAssets.hpp"

namespace stackchan {
namespace {

constexpr uint32_t kBackground = 0xF5F3FA;
constexpr uint32_t kBlinkDurationMs = 140;
constexpr uint32_t kSpeechVariantHoldMs = 110;
constexpr float kSpeechAnimationFloor = 0.10f;
constexpr int32_t kAnimatedPatchX = 60;
constexpr int32_t kAnimatedPatchY = 70;
constexpr int32_t kAnimatedPatchWidth = 200;
constexpr int32_t kAnimatedPatchHeight = 115;
constexpr uint32_t kCodexBackground = 0x11121A;
constexpr uint32_t kCodexPanel = 0x1D2030;
constexpr uint32_t kCodexText = 0xF7F4EE;
constexpr uint32_t kCodexMuted = 0x8E94A8;
constexpr uint32_t kCodexAccent = 0xFF6B9A;
constexpr uint32_t kCodexHeartbeatPeriodMs = 900;

struct Asset {
  int index;
  const uint8_t* data;
  size_t length;
};

}  // namespace

FaceRenderer::FaceRenderer(M5GFX& display)
    : display_(display),
      canvas_(&display),
      speaking_soft_canvas_(&display),
      speaking_excited_canvas_(&display),
      blink_canvas_(&display) {}

bool FaceRenderer::begin() {
  canvas_.setColorDepth(16);
  if (!canvas_.createSprite(display_.width(), display_.height())) return false;

  // Speaking swaps happen while audio frames arrive. Decode both mouth shapes
  // once into PSRAM so animation never blocks the WebSocket/audio loop on PNG
  // decompression.
  speaking_soft_canvas_.setColorDepth(16);
  speaking_excited_canvas_.setColorDepth(16);
  speaking_soft_canvas_.setPsram(true);
  speaking_excited_canvas_.setPsram(true);
  speaking_frames_cached_ =
      speaking_soft_canvas_.createSprite(kAnimatedPatchWidth, kAnimatedPatchHeight) &&
      speaking_excited_canvas_.createSprite(kAnimatedPatchWidth,
                                             kAnimatedPatchHeight);
  if (speaking_frames_cached_) {
    speaking_soft_canvas_.fillScreen(kBackground);
    speaking_soft_canvas_.drawPng(faces::speaking_soft_png,
                                  faces::speaking_soft_png_len,
                                  -kAnimatedPatchX, -kAnimatedPatchY);
    speaking_excited_canvas_.fillScreen(kBackground);
    speaking_excited_canvas_.drawPng(faces::speaking_excited_png,
                                     faces::speaking_excited_png_len,
                                     -kAnimatedPatchX, -kAnimatedPatchY);
  }
  blink_canvas_.setColorDepth(16);
  blink_canvas_.setPsram(true);
  blink_frame_cached_ =
      blink_canvas_.createSprite(kAnimatedPatchWidth, kAnimatedPatchHeight);
  if (blink_frame_cached_) {
    blink_canvas_.fillScreen(kBackground);
    blink_canvas_.drawPng(faces::blink_png, faces::blink_png_len,
                          -kAnimatedPatchX, -kAnimatedPatchY);
  }
  next_blink_ms_ = millis() + 1800;
  draw(millis());
  return true;
}

void FaceRenderer::setState(FaceState state) {
  if (state_ != state && state != FaceState::happy) emotion_ = "neutral";
  state_ = state;
  if (state == FaceState::happy && emotion_ == "neutral") emotion_ = "happy";
}

void FaceRenderer::setEmotion(const String& emotion, float intensity) {
  emotion_ = emotion;
  intensity_ = constrain(intensity, 0.0f, 1.0f);
}

void FaceRenderer::setGaze(float x, float y) {
  // Retained in the protocol for future eye-layer packs.
  requested_gaze_x_ = constrain(x, -1.0f, 1.0f);
  requested_gaze_y_ = constrain(y, -1.0f, 1.0f);
}

void FaceRenderer::setSpeechEnergy(float energy) {
  speech_energy_ = constrain(energy, 0.0f, 1.0f);
}

void FaceRenderer::setStatus(const String& status) { status_ = status; }

void FaceRenderer::setCodexMode(bool enabled) {
  if (codex_mode_ == enabled) return;
  codex_mode_ = enabled;
  displayed_asset_index_ = -1;
  last_frame_ms_ = 0;
}

void FaceRenderer::setSettingsMode(bool enabled) {
  if (settings_mode_ == enabled) return;
  settings_mode_ = enabled;
  displayed_asset_index_ = -1;
  last_frame_ms_ = 0;
}

void FaceRenderer::setCodexSelectedAgent(uint8_t index) {
  if (index < codex_agent_states_.size()) codex_selected_agent_ = index;
}

void FaceRenderer::setCodexAgentState(uint8_t index, CodexAgentState state,
                                      uint32_t color, uint8_t effect,
                                      float speed) {
  if (index >= codex_agent_states_.size()) return;
  codex_agent_states_[index] = state;
  codex_agent_colors_[index] = color;
  codex_agent_effects_[index] = effect;
  codex_agent_speeds_[index] = speed;
}

void FaceRenderer::update(uint32_t now_ms) {
  if (now_ms - last_frame_ms_ < 33) return;
  last_frame_ms_ = now_ms;

  if (blink_start_ms_ == 0 && now_ms >= next_blink_ms_ &&
      state_ != FaceState::thinking) {
    blink_start_ms_ = now_ms;
  } else if (blink_start_ms_ != 0 && now_ms - blink_start_ms_ >= kBlinkDurationMs) {
    blink_start_ms_ = 0;
    next_blink_ms_ = now_ms + 2600 + esp_random() % 3000;
  }
  draw(now_ms);
}

void FaceRenderer::renderAsset(int index, const uint8_t* data, size_t length) {
  if (displayed_asset_index_ == index) return;
  if (state_ == FaceState::speaking && (index == 4 || index == 8)) {
    ++speaking_mouth_transitions_;
  } else if (state_ == FaceState::speaking && index == 12) {
    ++speaking_blinks_;
  }
  if (speaking_frames_cached_ && index == 4) {
    speaking_soft_canvas_.pushSprite(kAnimatedPatchX, kAnimatedPatchY);
    displayed_asset_index_ = index;
    return;
  }
  if (speaking_frames_cached_ && index == 8) {
    speaking_excited_canvas_.pushSprite(kAnimatedPatchX, kAnimatedPatchY);
    displayed_asset_index_ = index;
    return;
  }
  if (blink_frame_cached_ && index == 12) {
    blink_canvas_.pushSprite(kAnimatedPatchX, kAnimatedPatchY);
    displayed_asset_index_ = index;
    return;
  }
  canvas_.fillScreen(kBackground);
  canvas_.drawPng(data, length, 0, 0);
  canvas_.pushSprite(0, 0);
  displayed_asset_index_ = index;
}

void FaceRenderer::draw(uint32_t now_ms) {
  if (settings_mode_) {
    drawSettings();
    return;
  }
  if (codex_mode_) {
    drawCodex(now_ms);
    return;
  }
  Asset target{0, faces::neutral_png, faces::neutral_png_len};
  auto select = [&](int index, const uint8_t* data, size_t length) {
    target = {index, data, length};
  };

  if (emotion_ == "petted") {
    select(11, faces::petted_png, faces::petted_png_len);
  } else if (emotion_ == "crying") {
    select(13, faces::crying_png, faces::crying_png_len);
  } else if (emotion_ == "playful" || emotion_ == "excited") {
    select(10, faces::playful_png, faces::playful_png_len);
  } else if (emotion_ == "love") {
    select(11, faces::petted_png, faces::petted_png_len);
  } else if (emotion_ == "worried" || emotion_ == "sad" || state_ == FaceState::error ||
             state_ == FaceState::disconnected) {
    select(9, faces::worried_png, faces::worried_png_len);
  } else if (emotion_ == "sleepy" || state_ == FaceState::booting) {
    select(6, faces::sleepy_png, faces::sleepy_png_len);
  } else if (emotion_ == "shy") {
    select(7, faces::shy_png, faces::shy_png_len);
  } else if (emotion_ == "surprised") {
    select(5, faces::surprised_png, faces::surprised_png_len);
  } else if (emotion_ == "curious") {
    select(3, faces::thinking_png, faces::thinking_png_len);
  } else if (state_ == FaceState::speaking) {
    const bool can_change = now_ms - speech_variant_changed_ms_ >= kSpeechVariantHoldMs;
    if (can_change) {
      // Energy alone often stays within one narrow band for neural TTS, which
      // left the mouth on a single frame. Use energy as a voiced gate and a
      // short alternating cadence as the lip-sync carrier. Pauses close the
      // mouth; stronger phonemes still keep the open shape prominent.
      speech_excited_ = speech_energy_ >= kSpeechAnimationFloor
                            ? !speech_excited_
                            : false;
      speech_variant_changed_ms_ = now_ms;
    }
    if (speech_excited_) {
      select(8, faces::speaking_excited_png, faces::speaking_excited_png_len);
    } else {
      select(4, faces::speaking_soft_png, faces::speaking_soft_png_len);
    }
  } else if (state_ == FaceState::thinking) {
    select(3, faces::thinking_png, faces::thinking_png_len);
  } else if (state_ == FaceState::listening) {
    select(2, faces::listening_png, faces::listening_png_len);
  } else if (emotion_ == "happy" || state_ == FaceState::happy) {
    select(1, faces::happy_png, faces::happy_png_len);
  }

  const Asset blink{12, faces::blink_png, faces::blink_png_len};
  const bool neutral_blink =
      blink_start_ms_ != 0 &&
      (target.index == 0 || target.index == 2 || target.index == 4 ||
       target.index == 8);
  if (neutral_blink) {
    renderAsset(blink.index, blink.data, blink.length);
  } else {
    logical_asset_index_ = target.index;
    renderAsset(target.index, target.data, target.length);
  }
  drawFaceHud(now_ms);
}

void FaceRenderer::drawSettings() {
  auto color565 = [&](uint32_t color) {
    return canvas_.color565((color >> 16) & 0xFF, (color >> 8) & 0xFF,
                            color & 0xFF);
  };
  canvas_.fillScreen(color565(kCodexBackground));
  canvas_.fillCircle(286, 26, 72, color565(0x251F36));
  canvas_.setTextDatum(textdatum_t::middle_center);
  canvas_.setTextColor(color565(kCodexText));
  canvas_.setTextSize(2);
  canvas_.drawString("Language", 160, 42);
  const char* modes[] = {"AUTO", "EN", "JP"};
  const char* values[] = {"auto", "en", "ja"};
  for (int index = 0; index < 3; ++index) {
    const int y = 76 + index * 52;
    const bool selected = language_mode_ == values[index];
    canvas_.fillRoundRect(38, y, 244, 42, 14,
                          color565(selected ? kCodexAccent : kCodexPanel));
    canvas_.setTextColor(color565(selected ? 0x11121A : kCodexText));
    canvas_.drawString(modes[index], 160, y + 21);
    if (selected) canvas_.fillCircle(257, y + 21, 5, color565(0xFFFFFF));
  }
  canvas_.pushSprite(0, 0);
}

void FaceRenderer::drawFaceHud(uint32_t now_ms) {
  // Keep the HUD responsive without polling the power IC or repainting over
  // the audio/display bus on every 30 fps face-animation frame.
  if (last_hud_draw_ms_ != 0 && now_ms - last_hud_draw_ms_ < 250) return;
  last_hud_draw_ms_ = now_ms;
  if (last_battery_read_ms_ == 0 || now_ms - last_battery_read_ms_ >= 30000) {
    battery_level_ = constrain(M5.Power.getBatteryLevel(), 0, 100);
    last_battery_read_ms_ = now_ms;
  }
  const uint16_t panel = display_.color565(255, 252, 255);
  const uint16_t ink = display_.color565(82, 73, 94);
  const uint16_t battery_ink = display_.color565(86, 180, 122);
  char clock_text[6] = "--:--";
  const time_t epoch = time(nullptr);
  if (epoch > 24 * 60 * 60) {
    struct tm local_time {};
    localtime_r(&epoch, &local_time);
    strftime(clock_text, sizeof(clock_text), "%H:%M", &local_time);
  }

  display_.fillRoundRect(241, 5, 74, 19, 8, panel);
  display_.setTextDatum(textdatum_t::middle_left);
  display_.setTextSize(1);
  display_.setTextColor(ink);
  display_.drawString(clock_text, 247, 14);
  display_.drawRoundRect(289, 9, 19, 10, 3, ink);
  display_.fillRect(308, 12, 2, 4, ink);
  const int fill_width = battery_level_ * 15 / 100;
  if (fill_width > 0) display_.fillRect(291, 11, fill_width, 6, battery_ink);
}

void FaceRenderer::drawCodex(uint32_t now_ms) {
  drawCodexReference(now_ms);
  return;
}

void FaceRenderer::drawCodexReference(uint32_t now_ms) {
  auto color565 = [&](uint32_t color) {
    return canvas_.color565((color >> 16) & 0xFF, (color >> 8) & 0xFF,
                            color & 0xFF);
  };
  auto stateColor = [&](uint8_t index) -> uint32_t {
    const uint32_t host = codex_agent_colors_[index];
    if (host != 0) return host;
    switch (codex_agent_states_[index]) {
      case CodexAgentState::working: return 0x304FFE;
      case CodexAgentState::complete: return 0x00FF4C;
      case CodexAgentState::needs_input: return 0xFF6D00;
      case CodexAgentState::error: return 0xFF0033;
      case CodexAgentState::idle: return 0xFFFFFF;
      case CodexAgentState::off: return 0x353848;
    }
    return 0x353848;
  };

  const uint32_t cycle = now_ms / kCodexHeartbeatPeriodMs;
  if (cycle != codex_heartbeat_cycle_) {
    if (codex_heartbeat_last_ms_ != 0) {
      const uint32_t interval = now_ms - codex_heartbeat_last_ms_;
      codex_heartbeat_interval_ms_ = static_cast<uint16_t>(min(interval, 65535U));
      const uint32_t drift = interval > kCodexHeartbeatPeriodMs
                                 ? interval - kCodexHeartbeatPeriodMs
                                 : kCodexHeartbeatPeriodMs - interval;
      codex_heartbeat_drift_ms_ = static_cast<uint16_t>(min(drift, 65535U));
      if (interval > kCodexHeartbeatPeriodMs + 120) ++codex_heartbeat_misses_;
    }
    codex_heartbeat_cycle_ = cycle;
    codex_heartbeat_last_ms_ = now_ms;
    ++codex_heartbeat_ticks_;
  }

  canvas_.fillScreen(color565(0x17181D));
  canvas_.fillRoundRect(18, 7, 284, 226, 18, color565(0x0C0D12));
  canvas_.drawRoundRect(18, 7, 284, 226, 18, color565(0x30323B));
  canvas_.drawRoundRect(20, 9, 280, 222, 16, color565(0x22242D));

  auto key = [&](int control, int x, int y, int width, int height,
                 uint32_t fill, uint32_t border, bool selected = false) {
    const int press = codex_pressed_control_ == control ? 2 : 0;
    canvas_.fillRoundRect(x, y + 3, width, height, 13, color565(0x090A0E));
    canvas_.fillRoundRect(x, y + press, width, height, 13, color565(border));
    canvas_.fillRoundRect(x + 2, y + 2 + press, width - 4, height - 4, 11,
                          color565(fill));
    canvas_.drawFastHLine(x + 9, y + 3 + press, width - 18,
                          color565(selected ? 0x8D97FF : 0x4A4D58));
  };

  constexpr int column_x[4] = {28, 94, 160, 226};
  constexpr int slot_x[6] = {94, 160, 28, 94, 160, 226};
  constexpr int slot_y[6] = {16, 16, 64, 64, 64, 64};
  for (uint8_t index = 0; index < 6; ++index) {
    const bool selected = index == codex_selected_agent_;
    const auto state = codex_agent_states_[index];
    const uint32_t state_color = stateColor(index);
    uint32_t fill = state == CodexAgentState::off ? 0x050608 : 0xB9BBC1;
    uint32_t border = state == CodexAgentState::off ? 0x292B33 : 0xD7D8DC;
    if (selected || state == CodexAgentState::working) {
      fill = 0x4856B6;
      border = 0x6975D6;
    } else if (state == CodexAgentState::error) {
      fill = 0x7A2B42;
      border = 0xA14561;
    } else if (state == CodexAgentState::needs_input) {
      fill = 0x80612A;
      border = 0xB78D42;
    }
    key(index, slot_x[index], slot_y[index], 58, 42, fill, border, selected);
    const uint16_t dot = color565(
        state == CodexAgentState::off ? 0x111218
                                     : (state_color == 0xFFFFFF ? 0x7B6CDC
                                                               : state_color));
    canvas_.fillCircle(slot_x[index] + 29, slot_y[index] + 21, 6, dot);
    if (selected) canvas_.drawCircle(slot_x[index] + 29, slot_y[index] + 21, 8,
                                     color565(0x9A91F0));
  }

  // The two corner controls mirror the reference hardware: a restrained
  // status dial and a dark BLE connection well.
  canvas_.fillCircle(57, 37, 25, color565(0x20222B));
  canvas_.fillTriangle(34, 38, 57, 14, 72, 18, color565(0x30333D));
  canvas_.drawCircle(57, 37, 25, color565(0x393C47));
  canvas_.fillRoundRect(226, 16, 58, 42, 13, color565(0x292B32));
  canvas_.fillCircle(255, 37, 16,
                     color565(codex_agent_states_[codex_selected_agent_] ==
                                      CodexAgentState::working
                                  ? 0x161A2B
                                  : 0x020304));

  const uint16_t icon_ink = color565(kCodexText);
  for (int index = 0; index < 4; ++index) {
    uint32_t fill = 0x181920;
    uint32_t border = 0x343640;
    const bool steer_ready = index == 3 && codex_queued_followup_ &&
                             codex_agent_states_[codex_selected_agent_] ==
                                 CodexAgentState::working;
    if (steer_ready) {
      fill = 0x26355A;
      border = 0x405B95;
    }
    key(6 + index, column_x[index], 112, 58, 46, fill, border);
    if (index == 0) {
      canvas_.drawBitmap(column_x[index] + 15, 121, codex_icons::lightning_28,
                         28, 28, icon_ink);
    } else if (index == 1) {
      canvas_.drawBitmap(column_x[index] + 15, 121,
                         codex_icons::chat_circle_dots_28, 28, 28, icon_ink);
    } else if (index == 2) {
      canvas_.drawBitmap(column_x[index] + 15, 121, codex_icons::git_fork_28,
                         28, 28, icon_ink);
    } else {
      canvas_.drawBitmap(column_x[index] + 15, 121,
                         codex_icons::arrow_bend_up_right_28, 28, 28,
                         color565(steer_ready ? 0xA9C8FF : 0x777B87));
    }
  }

  // Bottom-left heartbeat: one phase advances every 300 ms. The full 900 ms
  // cycle is measured above, so missed frames are observable rather than
  // merely decorative.
  key(10, 28, 164, 58, 62, 0x15161D, 0x282A33);
  const int heartbeat_phase = static_cast<int>((now_ms % kCodexHeartbeatPeriodMs) /
                                                (kCodexHeartbeatPeriodMs / 3));
  constexpr uint32_t heartbeat_colors[3] = {0xA9C9FF, 0xD8D590, 0x8E92A2};
  for (int index = 0; index < 3; ++index) {
    canvas_.fillCircle(37, 183 + index * 7, index == heartbeat_phase ? 3 : 2,
                       color565(heartbeat_colors[index]));
  }
  canvas_.fillCircle(61, 195, 15, color565(0x111219));

  uint32_t mic_fill = 0x181920;
  uint32_t mic_border = 0x373944;
  uint32_t mic_color = 0xF7F4EE;
  if (codex_voice_state_ == CodexVoiceState::recording) {
    mic_fill = 0x53223A;
    mic_border = 0x8A3D60;
    mic_color = 0xFFB3CA;
  } else if (codex_voice_state_ == CodexVoiceState::processing) {
    mic_fill = 0x273152;
    mic_border = 0x485B92;
    mic_color = 0xC5D1FF;
  } else if (codex_voice_state_ == CodexVoiceState::completed) {
    mic_fill = 0x1B4734;
    mic_border = 0x347A59;
    mic_color = 0xA1F6C4;
  }
  key(11, 94, 164, 124, 62, mic_fill, mic_border);
  canvas_.drawBitmap(138, 177, codex_icons::microphone_36, 36, 36,
                     color565(mic_color));
  if (codex_voice_state_ == CodexVoiceState::recording) {
    canvas_.drawCircle(156, 195, 21 + static_cast<int>((now_ms / 180) % 2),
                       color565(mic_color));
  }

  key(12, 226, 164, 58, 62, codex_archive_armed_ ? 0x543716 : 0x181920,
      codex_archive_armed_ ? 0x8A6229 : 0x343640);
  canvas_.drawBitmap(241, 181, codex_icons::archive_28, 28, 28,
                     color565(codex_archive_armed_ ? 0xFFD38A : kCodexText));

  canvas_.pushSprite(0, 0);
}

FaceState faceStateFromString(const String& value) {
  if (value == "booting") return FaceState::booting;
  if (value == "disconnected") return FaceState::disconnected;
  if (value == "listening") return FaceState::listening;
  if (value == "thinking") return FaceState::thinking;
  if (value == "speaking") return FaceState::speaking;
  if (value == "awaiting_approval") return FaceState::listening;
  if (value == "happy") return FaceState::happy;
  if (value == "error") return FaceState::error;
  return FaceState::idle;
}

}  // namespace stackchan
