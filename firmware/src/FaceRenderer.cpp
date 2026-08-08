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
  Asset target{0, faces::neutral_png, faces::neutral_png_len};
  auto select = [&](int index, const uint8_t* data, size_t length) {
    target = {index, data, length};
  };

  if (emotion_ == "petted") {
    select(11, faces::petted_png, faces::petted_png_len);
  } else if (emotion_ == "playful") {
    select(10, faces::playful_png, faces::playful_png_len);
  } else if (emotion_ == "worried" || emotion_ == "sad" || state_ == FaceState::error ||
             state_ == FaceState::disconnected) {
    select(9, faces::worried_png, faces::worried_png_len);
  } else if (emotion_ == "sleepy" || state_ == FaceState::booting) {
    select(6, faces::sleepy_png, faces::sleepy_png_len);
  } else if (emotion_ == "shy") {
    select(7, faces::shy_png, faces::shy_png_len);
  } else if (emotion_ == "surprised") {
    select(5, faces::surprised_png, faces::surprised_png_len);
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
