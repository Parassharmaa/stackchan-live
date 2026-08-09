#pragma once

#include <cstddef>
#include <cstdint>

namespace stackchan {

enum class ScreenTapRoute : uint8_t {
  none,
  codex_control,
  interrupt_playback,
  conversation,
};

constexpr int touchTravelMagnitude(int distance) {
  return distance < 0 ? -distance : distance;
}

constexpr bool isCodexControlRelease(bool codex_mode, bool was_released,
                                     int distance_x, int distance_y,
                                     int max_travel = 24) {
  return codex_mode && was_released &&
         touchTravelMagnitude(distance_x) <= max_travel &&
         touchTravelMagnitude(distance_y) <= max_travel;
}

// Keep the interaction decision independent of M5Unified so the exact policy
// can be executed on the laptop in regression tests.
constexpr ScreenTapRoute routeScreenTap(bool codex_mode, bool playback_active,
                                        uint8_t click_count) {
  if (codex_mode) return ScreenTapRoute::codex_control;
  if (playback_active) {
    return click_count >= 2 ? ScreenTapRoute::interrupt_playback
                            : ScreenTapRoute::none;
  }
  return ScreenTapRoute::conversation;
}

// The I2S DMA path is clocked in complete 20 ms packets. Any non-empty UI-tone
// tail must therefore occupy a full output frame; the unwritten samples are
// explicitly zero-padded by AudioEndpoint.
constexpr size_t uiSoundPcmFrameLength(size_t generated_samples,
                                       size_t output_samples_per_frame) {
  return generated_samples == 0 ? 0 : output_samples_per_frame;
}

template <typename Sample>
inline void zeroPadUiSoundFrame(Sample* frame, size_t generated_samples,
                                size_t output_samples_per_frame) {
  for (size_t index = generated_samples; index < output_samples_per_frame;
       ++index) {
    frame[index] = 0;
  }
}

}  // namespace stackchan
