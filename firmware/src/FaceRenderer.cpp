#include "FaceRenderer.hpp"

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

void FaceRenderer::setCodexConnected(bool connected) {
  codex_connected_ = connected;
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

  canvas_.fillRoundRect(8, 7, 36, 28, 10, color565(kCodexPanel));
  canvas_.setTextColor(color565(kCodexText));
  canvas_.drawString("<", 26, 21);
  canvas_.setTextColor(color565(kCodexText));
  canvas_.drawString("CODEX CONTROL", 146, 16);
  const uint32_t connection_color = codex_connected_ ? 0x00FF4C : 0xFF6D00;
  canvas_.fillRoundRect(256, 7, 56, 28, 10, color565(0x25283A));
  canvas_.fillCircle(268, 21, 4, color565(connection_color));
  canvas_.setTextColor(color565(connection_color));
  canvas_.drawString(codex_connected_ ? "LIVE" : "PAIR", 290, 21);

  constexpr int agent_x[6] = {34, 84, 134, 184, 234, 284};
  for (uint8_t index = 0; index < 6; ++index) {
    const uint32_t color = stateColor(index);
    const bool selected = index == codex_selected_agent_;
    const bool pulse = codex_agent_states_[index] == CodexAgentState::working ||
                       codex_agent_states_[index] == CodexAgentState::needs_input;
    const int radius = selected ? 18 : 15;
    if (pulse) {
      const int halo = 18 + static_cast<int>((now_ms / 180) % 3);
      canvas_.drawCircle(agent_x[index], 67, halo, color565(color));
    }
    canvas_.fillCircle(agent_x[index], 67, radius, color565(color));
    canvas_.setTextColor(color565(color == 0xFFFFFF ? 0x11121A : 0xFFFFFF));
    canvas_.drawNumber(index + 1, agent_x[index], 67);
    if (selected) {
      canvas_.drawRoundRect(agent_x[index] - 22, 43, 44, 48, 14,
                            color565(kCodexAccent));
    }
  }

  const CodexAgentState selected_state = codex_agent_states_[codex_selected_agent_];
  const uint32_t selected_color = stateColor(codex_selected_agent_);
  canvas_.fillRoundRect(12, 99, 296, 70, 18, color565(kCodexPanel));
  canvas_.fillRoundRect(22, 108, 70, 52, 17, color565(0x292D42));
  canvas_.drawRoundRect(22, 108, 70, 52, 17, color565(selected_color));
  // A tiny original Stack-chan face keeps the control surface expressive.
  const int face_center_x = 57;
  const int face_center_y = 134;
  const uint16_t face_ink = color565(kCodexText);
  if (selected_state == CodexAgentState::complete) {
    canvas_.drawLine(42, 129, 48, 132, face_ink);
    canvas_.drawLine(66, 132, 72, 129, face_ink);
    canvas_.drawLine(47, 141, 56, 146, face_ink);
    canvas_.drawLine(56, 146, 67, 140, face_ink);
  } else if (selected_state == CodexAgentState::error) {
    canvas_.fillCircle(46, 130, 2, face_ink);
    canvas_.fillCircle(68, 130, 2, face_ink);
    canvas_.drawLine(48, 146, 57, 141, face_ink);
    canvas_.drawLine(57, 141, 66, 146, face_ink);
  } else if (selected_state == CodexAgentState::needs_input) {
    canvas_.fillCircle(46, 130, 2, face_ink);
    canvas_.fillCircle(68, 130, 2, face_ink);
    canvas_.setTextColor(color565(selected_color));
    canvas_.setTextSize(2);
    canvas_.drawString("?", face_center_x, 143);
    canvas_.setTextSize(1);
  } else {
    const int blink_offset = selected_state == CodexAgentState::working
                                 ? static_cast<int>((now_ms / 300) % 2)
                                 : 0;
    canvas_.fillCircle(46, 130 + blink_offset, 2, face_ink);
    canvas_.fillCircle(68, 130 - blink_offset, 2, face_ink);
    canvas_.drawLine(51, 142, 63, 142, face_ink);
  }
  canvas_.setTextDatum(textdatum_t::top_left);
  canvas_.setTextColor(color565(kCodexMuted));
  canvas_.drawString(String("ACTIVE AGENT  ") + (codex_selected_agent_ + 1),
                     106, 112);
  canvas_.setTextColor(color565(kCodexText));
  canvas_.setTextSize(2);
  canvas_.drawString(codexAgentStateName(selected_state), 106, 132);
  canvas_.setTextSize(1);
  canvas_.setTextDatum(textdatum_t::middle_center);

  const bool approval = selected_state == CodexAgentState::needs_input;
  if (approval) {
    canvas_.fillRoundRect(8, 181, 148, 51, 15, color565(0x521B2B));
    canvas_.fillRoundRect(164, 181, 148, 51, 15, color565(0x12452C));
    canvas_.setTextColor(color565(0xFF9AB2));
    canvas_.drawString("DECLINE", 82, 206);
    canvas_.setTextColor(color565(0x73FF9E));
    canvas_.drawString("APPROVE", 238, 206);
  } else {
    const char* labels[4] = {"FAST", "PLAN", "AI", "HOLD MIC"};
    for (int index = 0; index < 4; ++index) {
      const int x = 8 + index * 78;
      canvas_.fillRoundRect(x, 181, 70, 51, 15,
                            color565(index == 3 ? 0x39253E : kCodexPanel));
      canvas_.setTextColor(color565(index == 3 ? kCodexAccent : kCodexText));
      canvas_.drawString(labels[index], x + 35, 206);
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
