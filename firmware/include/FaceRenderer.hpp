#pragma once

#include <M5Unified.h>

#include <array>

#include "CodexBleController.hpp"

namespace stackchan {

enum class FaceState : uint8_t {
  booting,
  disconnected,
  idle,
  listening,
  thinking,
  speaking,
  happy,
  error,
};

class FaceRenderer {
 public:
  explicit FaceRenderer(M5GFX& display);
  bool begin();
  void setState(FaceState state);
  void setEmotion(const String& emotion, float intensity);
  void setSpeechEnergy(float energy);
  void setGaze(float x, float y);
  void setStatus(const String& status);
  void setCodexMode(bool enabled);
  bool codexMode() const { return codex_mode_; }
  void setCodexSelectedAgent(uint8_t index);
  void setCodexAgentState(uint8_t index, CodexAgentState state, uint32_t color,
                          uint8_t effect, float speed);
  void setCodexIndicatorAnimation(bool enabled, uint16_t fallback_period_ms) {
    codex_indicator_animation_ = enabled;
    codex_indicator_period_ms_ = fallback_period_ms < 240 ? 240 : fallback_period_ms;
  }
  void setCodexVoiceState(CodexVoiceState state) { codex_voice_state_ = state; }
  void setCodexArchiveArmed(bool armed) { codex_archive_armed_ = armed; }
  void setCodexQueuedFollowup(bool queued) { codex_queued_followup_ = queued; }
  void update(uint32_t now_ms);
  bool speakingFramesCached() const { return speaking_frames_cached_; }
  uint32_t speakingMouthTransitions() const { return speaking_mouth_transitions_; }
  uint32_t speakingBlinks() const { return speaking_blinks_; }

 private:
  void draw(uint32_t now_ms);
  void drawCodex(uint32_t now_ms);
  void drawFaceHud(uint32_t now_ms);
  void renderAsset(int index, const uint8_t* data, size_t length);

  M5GFX& display_;
  M5Canvas canvas_;
  M5Canvas speaking_soft_canvas_;
  M5Canvas speaking_excited_canvas_;
  M5Canvas blink_canvas_;
  bool speaking_frames_cached_ = false;
  bool blink_frame_cached_ = false;
  FaceState state_ = FaceState::booting;
  String emotion_ = "neutral";
  String status_ = "Starting";
  float intensity_ = 0.5f;
  float speech_energy_ = 0.0f;
  float requested_gaze_x_ = 0.0f;
  float requested_gaze_y_ = 0.0f;
  uint32_t next_blink_ms_ = 1800;
  uint32_t blink_start_ms_ = 0;
  uint32_t last_frame_ms_ = 0;
  int logical_asset_index_ = -1;
  int displayed_asset_index_ = -1;
  bool speech_excited_ = false;
  uint32_t speech_variant_changed_ms_ = 0;
  uint32_t speaking_mouth_transitions_ = 0;
  uint32_t speaking_blinks_ = 0;
  uint32_t last_hud_draw_ms_ = 0;
  uint32_t last_battery_read_ms_ = 0;
  int battery_level_ = 0;
  bool codex_mode_ = false;
  uint8_t codex_selected_agent_ = 0;
  std::array<CodexAgentState, 6> codex_agent_states_{};
  std::array<uint32_t, 6> codex_agent_colors_{};
  std::array<uint8_t, 6> codex_agent_effects_{};
  std::array<float, 6> codex_agent_speeds_{};
  bool codex_indicator_animation_ = false;
  uint16_t codex_indicator_period_ms_ = 720;
  CodexVoiceState codex_voice_state_ = CodexVoiceState::idle;
  bool codex_archive_armed_ = false;
  bool codex_queued_followup_ = false;
};

FaceState faceStateFromString(const String& value);

}  // namespace stackchan
