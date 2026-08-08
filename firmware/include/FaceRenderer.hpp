#pragma once

#include <M5Unified.h>

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
  void update(uint32_t now_ms);
  bool speakingFramesCached() const { return speaking_frames_cached_; }
  uint32_t speakingMouthTransitions() const { return speaking_mouth_transitions_; }
  uint32_t speakingBlinks() const { return speaking_blinks_; }

 private:
  void draw(uint32_t now_ms);
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
};

FaceState faceStateFromString(const String& value);

}  // namespace stackchan
