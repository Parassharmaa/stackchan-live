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

void FaceRenderer::setCodexSelectedAgent(uint8_t index) {
  if (index < codex_agent_states_.size()) codex_selected_agent_ = index;
}

void FaceRenderer::setCodexAgentState(uint8_t index, CodexAgentState state,
                                      uint32_t color) {
  if (index >= codex_agent_states_.size()) return;
  codex_agent_states_[index] = state;
  codex_agent_colors_[index] = color;
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

  display_.fillRoundRect(221, 5, 94, 19, 8, panel);
  display_.setTextDatum(textdatum_t::middle_left);
  display_.setTextSize(1);
  display_.setTextColor(ink);
  display_.drawString(clock_text, 227, 14);
  display_.drawRoundRect(267, 9, 19, 10, 3, ink);
  display_.fillRect(286, 12, 2, 4, ink);
  const int fill_width = battery_level_ * 15 / 100;
  if (fill_width > 0) display_.fillRect(269, 11, fill_width, 6, battery_ink);
  char battery_text[5];
  snprintf(battery_text, sizeof(battery_text), "%d%%", battery_level_);
  display_.setTextDatum(textdatum_t::middle_right);
  display_.drawString(battery_text, 311, 14);
}

void FaceRenderer::drawCodex(uint32_t now_ms) {
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

  canvas_.fillScreen(color565(kCodexBackground));
  // Low-cost depth cues keep the controller readable without spending the
  // frame budget on a full gradient while audio and BLE continue streaming.
  canvas_.drawCircle(28, 212, 86, color565(0x20243A));
  canvas_.drawCircle(292, 84, 74, color565(0x251F36));
  canvas_.setTextDatum(textdatum_t::middle_center);
  canvas_.setTextSize(1);

  constexpr int agent_x[6] = {30, 82, 134, 186, 238, 290};
  for (uint8_t index = 0; index < 6; ++index) {
    const uint32_t color = stateColor(index);
    const bool selected = index == codex_selected_agent_;
    const bool pulse = codex_agent_states_[index] == CodexAgentState::working ||
                       codex_agent_states_[index] == CodexAgentState::needs_input;
    const int radius = selected ? 22 : 19;
    if (pulse) {
      const int halo = 23 + static_cast<int>((now_ms / 180) % 3);
      canvas_.drawCircle(agent_x[index], 28, halo, color565(color));
    }
    canvas_.fillCircle(agent_x[index], 28, radius, color565(color));
    canvas_.setTextColor(color565(color == 0xFFFFFF ? 0x11121A : 0xFFFFFF));
    canvas_.setTextSize(selected ? 2 : 1);
    canvas_.drawNumber(index + 1, agent_x[index], 28);
    canvas_.setTextSize(1);
    if (selected) {
      canvas_.drawCircle(agent_x[index], 28, 25, color565(kCodexAccent));
    }
  }

  const CodexAgentState selected_state = codex_agent_states_[codex_selected_agent_];
  const uint32_t selected_color = stateColor(codex_selected_agent_);
  canvas_.fillRoundRect(12, 59, 296, 63, 17, color565(kCodexPanel));
  canvas_.fillRoundRect(22, 64, 64, 53, 15, color565(0x292D42));
  canvas_.drawRoundRect(22, 64, 64, 53, 15, color565(selected_color));
  // A tiny original Stack-chan face keeps the control surface expressive.
  const int face_center_x = 57;
  const uint16_t face_ink = color565(kCodexText);
  if (selected_state == CodexAgentState::complete) {
    canvas_.drawLine(42, 84, 48, 87, face_ink);
    canvas_.drawLine(66, 87, 72, 84, face_ink);
    canvas_.drawLine(47, 99, 56, 104, face_ink);
    canvas_.drawLine(56, 104, 67, 98, face_ink);
  } else if (selected_state == CodexAgentState::error) {
    canvas_.fillCircle(46, 85, 2, face_ink);
    canvas_.fillCircle(68, 85, 2, face_ink);
    canvas_.drawLine(48, 104, 57, 99, face_ink);
    canvas_.drawLine(57, 99, 66, 104, face_ink);
  } else if (selected_state == CodexAgentState::needs_input) {
    canvas_.fillCircle(46, 85, 2, face_ink);
    canvas_.fillCircle(68, 85, 2, face_ink);
    canvas_.setTextColor(color565(selected_color));
    canvas_.setTextSize(2);
    canvas_.drawString("?", face_center_x, 99);
    canvas_.setTextSize(1);
  } else {
    const int blink_offset = selected_state == CodexAgentState::working
                                 ? static_cast<int>((now_ms / 300) % 2)
                                 : 0;
    canvas_.fillCircle(46, 85 + blink_offset, 2, face_ink);
    canvas_.fillCircle(68, 85 - blink_offset, 2, face_ink);
    canvas_.drawLine(51, 99, 63, 99, face_ink);
  }
  // The selected slot and its live state are enough context on this tiny
  // display. Keep the center panel free of chat titles and secondary controls.
  canvas_.setTextDatum(textdatum_t::middle_left);
  canvas_.setTextColor(color565(kCodexText));
  canvas_.setTextSize(2);
  canvas_.drawString(codexAgentStateName(selected_state), 100, 90);
  canvas_.setTextSize(1);
  canvas_.setTextDatum(textdatum_t::middle_center);

  const bool approval = selected_state == CodexAgentState::needs_input;
  if (approval) {
    canvas_.fillRoundRect(8, 130, 148, 102, 17, color565(0x521B2B));
    canvas_.fillRoundRect(164, 130, 148, 102, 17, color565(0x12452C));
    const uint16_t decline_ink = color565(0xFF9AB2);
    const uint16_t approve_ink = color565(0x73FF9E);
    canvas_.drawCircle(82, 181, 20, decline_ink);
    canvas_.drawLine(72, 171, 92, 191, decline_ink);
    canvas_.drawLine(92, 171, 72, 191, decline_ink);
    canvas_.drawCircle(238, 181, 20, approve_ink);
    canvas_.drawLine(226, 181, 235, 190, approve_ink);
    canvas_.drawLine(235, 190, 252, 171, approve_ink);
  } else {
    // Five compact icon keys keep utility actions available without spending
    // scarce pixels on labels. The wide mic below remains the clear primary
    // action, matching the physical hierarchy of a dedicated controller.
    for (int index = 0; index < 5; ++index) {
      const int x = 8 + index * 62;
      uint32_t fill = kCodexPanel;
      uint32_t icon = kCodexText;
      if (index == 3 && codex_archive_armed_) {
        fill = 0x543716;
        icon = 0xFFD38A;
      }
      const bool steer_ready = index == 4 && codex_queued_followup_ &&
                               selected_state == CodexAgentState::working;
      if (index == 4 && steer_ready) {
        fill = 0x233B63;
        icon = 0x9CC7FF;
      } else if (index == 4) {
        fill = 0x191B26;
        icon = 0x5E6375;
      }
      canvas_.fillRoundRect(x, 128, 56, 48, 14, color565(fill));
      canvas_.drawRoundRect(x, 128, 56, 48, 14, color565(0x303446));
      canvas_.setTextColor(color565(icon));
      const uint16_t ink = color565(kCodexText);
      if (index == 0) {
        canvas_.drawBitmap(x + 14, 138, codex_icons::lightning_28, 28, 28, ink);
      } else if (index == 1) {
        canvas_.drawBitmap(x + 14, 138, codex_icons::chat_circle_dots_28, 28,
                           28, ink);
      } else if (index == 2) {
        canvas_.drawBitmap(x + 14, 138, codex_icons::git_fork_28, 28, 28, ink);
      } else if (index == 3) {
        canvas_.drawBitmap(x + 14, 138, codex_icons::archive_28, 28, 28,
                           color565(icon));
      } else {
        const uint16_t steer_ink = color565(icon);
        canvas_.drawBitmap(x + 14, 138, codex_icons::arrow_bend_up_right_28,
                           28, 28, steer_ink);
        if (steer_ready) canvas_.fillCircle(x + 43, 166, 3, steer_ink);
      }
    }

    uint32_t mic_color = 0x39253E;
    uint32_t mic_text = kCodexAccent;
    if (codex_voice_state_ == CodexVoiceState::recording) {
      mic_color = 0x5A1734;
      mic_text = 0xFFABC7;
    } else if (codex_voice_state_ == CodexVoiceState::processing) {
      mic_color = 0x29334D;
      mic_text = 0xB9C8FF;
    } else if (codex_voice_state_ == CodexVoiceState::completed) {
      mic_color = 0x174531;
      mic_text = 0x8BFFBC;
    }
    canvas_.fillRoundRect(8, 184, 304, 48, 15, color565(mic_color));
    canvas_.drawRoundRect(8, 184, 304, 48, 15, color565(0x593B60));
    canvas_.setTextColor(color565(mic_text));
    const uint16_t mic_ink = color565(mic_text);
    canvas_.drawBitmap(142, 189, codex_icons::microphone_36, 36, 36, mic_ink);
    if (codex_voice_state_ == CodexVoiceState::processing) {
      const int active_dot = static_cast<int>((now_ms / 220) % 3);
      for (int dot = 0; dot < 3; ++dot) {
        canvas_.fillCircle(185 + dot * 7, 208, 2,
                           color565(dot == active_dot ? 0xFFFFFF : 0x586383));
      }
    } else if (codex_voice_state_ == CodexVoiceState::recording) {
      canvas_.drawCircle(160, 207, 20 + static_cast<int>((now_ms / 180) % 3),
                         mic_ink);
    }
  }
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
