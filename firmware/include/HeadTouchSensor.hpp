#pragma once

#include <M5Unified.h>

namespace stackchan {

enum class HeadGesture : uint8_t {
  none,
  touch,
  hold,
  swipe_forward,
  swipe_backward,
  release,
};

class HeadTouchSensor {
 public:
  bool begin();
  HeadGesture update(uint32_t now_ms);
  void resetGesture();
  bool present() const { return present_; }
  bool ready() const { return ready_; }
  uint8_t zone() const { return zone_; }
  uint8_t strength() const { return strength_; }
  uint8_t gestureZone() const { return gesture_zone_; }
  uint8_t gestureStrength() const { return gesture_strength_; }
  static bool strongMultiZoneContact(uint8_t raw_output);
  uint8_t rawOutput() const { return raw_output_; }
  uint32_t pollCount() const { return poll_count_; }
  uint32_t readFailures() const { return read_failures_; }
  bool lastReadOk() const { return last_read_ok_; }

 private:
  static constexpr uint8_t kAddress = 0x68;
  static constexpr uint8_t kOutputRegister = 0x10;
  static constexpr uint8_t kSensitivityFirstRegister = 0x02;
  static constexpr uint8_t kSensitivityLastRegister = 0x07;
  static constexpr uint8_t kControl1Register = 0x08;
  static constexpr uint8_t kControl2Register = 0x09;
  static constexpr uint8_t kReferenceReset1Register = 0x0A;
  static constexpr uint8_t kCalibrationHold2Register = 0x0F;
  static constexpr uint32_t kBusFrequency = 100000;
  bool configure();
  bool readOutput(uint8_t& output);

  bool present_ = false;
  bool ready_ = false;
  uint8_t zone_ = 0;
  uint8_t strength_ = 0;
  uint8_t raw_output_ = 0;
  uint8_t start_zone_ = 0;
  uint8_t furthest_zone_ = 0;
  uint8_t gesture_zone_ = 0;
  uint8_t gesture_strength_ = 0;
  uint8_t max_strength_ = 0;
  uint8_t pending_zone_ = 0;
  uint8_t pending_strength_ = 0;
  uint8_t activation_samples_ = 0;
  uint8_t release_samples_ = 0;
  uint32_t touch_started_ms_ = 0;
  uint32_t last_poll_ms_ = 0;
  uint32_t poll_count_ = 0;
  uint32_t read_failures_ = 0;
  bool last_read_ok_ = false;
  bool hold_reported_ = false;
};

const char* headGestureName(HeadGesture gesture);

}  // namespace stackchan
