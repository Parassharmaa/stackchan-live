#pragma once

#include <Arduino.h>

namespace stackchan {

struct MotionCompletion {
  bool success = false;
  int yaw_raw = -1;
  int pitch_raw = -1;
  int yaw_target_raw = -1;
  int pitch_target_raw = -1;
  int yaw_error_raw = -1;
  int pitch_error_raw = -1;
  const char* detail = "not ready";
};

struct MotionDiagnostic {
  bool success = false;
  bool power_enabled = false;
  bool power_released = false;
  bool yaw_feedback = false;
  bool pitch_feedback = false;
  int yaw_raw = -1;
  int pitch_raw = -1;
  int yaw_min = -1;
  int yaw_max = -1;
  int pitch_min = -1;
  int pitch_max = -1;
  bool id_scan_performed = false;
  uint16_t detected_id_mask = 0;
  int detected_positions[9]{};
  uint8_t io_address = 0;
  const char* detail = "not run";
};

class MotionController {
 public:
  bool begin();
  bool diagnose(MotionDiagnostic& diagnostic);
  bool move(bool has_yaw, float yaw_deg, bool has_pitch, float pitch_deg,
            uint16_t duration_ms);
  void update(uint32_t now_ms);
  bool takeCompletion(MotionCompletion& completion);
  const char* lastError() const { return last_error_; }
  bool verified() const { return verified_; }
  bool active() const { return pending_; }

 private:
  static constexpr uint8_t kYawId = 1;
  static constexpr uint8_t kPitchId = 2;
  static constexpr uint8_t kGoalPositionRegister = 42;
  static constexpr uint8_t kTorqueEnableRegister = 40;
  static constexpr uint8_t kPresentPositionRegister = 56;
  static constexpr uint8_t kMinAngleRegister = 9;
  static constexpr uint8_t kIoAddressLow = 0x6F;
  static constexpr uint8_t kIoAddressHigh = 0x71;
  static constexpr uint32_t kServoBaud = 1000000;

  bool setServoPower(bool enabled);
  bool armAfterWarmup();
  void failPending(const char* detail);
  bool readRegister(uint8_t id, uint8_t address, uint8_t* output, uint8_t length);
  bool writeRegister(uint8_t id, uint8_t address, const uint8_t* data, uint8_t length);
  bool receiveStatus(uint8_t id, uint8_t* output, uint8_t expected_length,
                     uint16_t timeout_ms = 100);
  void sendPacket(uint8_t id, uint8_t instruction, uint8_t address,
                  const uint8_t* data, uint8_t length);
  bool readPosition(uint8_t id, int& position);
  bool readLimits(uint8_t id, int& minimum, int& maximum);
  bool writeGoal(uint8_t id, int position, uint16_t duration_ms);
  bool setTorque(uint8_t id, bool enabled);
  bool positionMode(uint8_t id);

  bool initialized_ = false;
  bool ready_ = false;
  bool verified_ = false;
  bool pending_ = false;
  bool completion_ready_ = false;
  enum class Phase : uint8_t { idle, warming, moving };
  Phase phase_ = Phase::idle;
  uint8_t io_address_ = kIoAddressLow;
  int yaw_zero_ = 460;
  int pitch_zero_ = 620;
  int yaw_raw_ = -1;
  int pitch_raw_ = -1;
  int target_yaw_raw_ = -1;
  int target_pitch_raw_ = -1;
  bool pending_yaw_ = false;
  bool pending_pitch_ = false;
  float requested_yaw_deg_ = 0.0f;
  float requested_pitch_deg_ = 45.0f;
  uint16_t requested_duration_ms_ = 450;
  uint32_t completion_due_ms_ = 0;
  MotionCompletion completion_;
  const char* last_error_ = "not initialized";
};

}  // namespace stackchan
