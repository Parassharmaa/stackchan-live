#pragma once

#include <M5Unified.h>

namespace stackchan {

enum class LightAnimation : uint8_t {
  solid,
  pulse,
  rainbow,
  chase,
  twinkle,
};

class LightController {
 public:
  bool begin();
  bool set(uint8_t red, uint8_t green, uint8_t blue, float brightness,
           LightAnimation animation);
  bool off();
  void update(uint32_t now_ms);
  bool present() const { return present_; }
  bool lastWriteSuccessful() const { return last_write_successful_; }

 private:
  static constexpr uint8_t kAddressLow = 0x6F;
  static constexpr uint8_t kAddressHigh = 0x71;
  static constexpr uint8_t kLedConfigRegister = 0x24;
  static constexpr uint8_t kLedRamRegister = 0x30;
  static constexpr uint8_t kI2cConfigRegister = 0x23;
  static constexpr uint8_t kLedCount = 12;
  static constexpr uint32_t kBusFrequency = 100000;
  static constexpr uint32_t kFrameIntervalMs = 40;

  bool writeFrame(const uint16_t (&colors)[kLedCount]);
  static uint16_t rgb565(uint8_t red, uint8_t green, uint8_t blue);
  static uint16_t wheel(uint8_t position, float brightness);

  bool present_ = false;
  uint8_t address_ = kAddressLow;
  uint8_t red_ = 0;
  uint8_t green_ = 0;
  uint8_t blue_ = 0;
  float brightness_ = 0.0f;
  LightAnimation animation_ = LightAnimation::solid;
  uint32_t animation_started_ms_ = 0;
  uint32_t last_frame_ms_ = 0;
  uint32_t random_state_ = 0x534B4348;
  bool dirty_ = true;
  bool last_write_successful_ = false;
};

LightAnimation lightAnimationFromString(const char* value);

}  // namespace stackchan
