#include "MotionController.hpp"

#include <M5Unified.h>
#include <Preferences.h>

#include <algorithm>

namespace stackchan {
namespace {

constexpr uint8_t kInstructionRead = 0x02;
constexpr uint8_t kInstructionWrite = 0x03;

}  // namespace

bool MotionController::begin() {
  Serial1.begin(kServoBaud, SERIAL_8N1, 7, 6);
  if (M5.In_I2C.scanID(kIoAddressLow, 100000)) {
    io_address_ = kIoAddressLow;
  } else if (M5.In_I2C.scanID(kIoAddressHigh, 100000)) {
    io_address_ = kIoAddressHigh;
  } else {
    last_error_ = "motor power controller missing";
    return false;
  }

  Preferences calibration;
  if (calibration.begin("servo", true)) {
    const int saved_yaw = calibration.getInt("zero_pos_1", yaw_zero_);
    const int saved_pitch = calibration.getInt("zero_pos_2", pitch_zero_);
    if (saved_yaw >= 0 && saved_yaw <= 1000) yaw_zero_ = saved_yaw;
    if (saved_pitch >= 0 && saved_pitch <= 1000) pitch_zero_ = saved_pitch;
    calibration.end();
  }
  initialized_ = true;
  last_error_ = "servo bus ready; power is off";
  Serial.printf("motion: bus ready calibration yaw=%d pitch=%d power=off\n", yaw_zero_,
                pitch_zero_);
  return true;
}

bool MotionController::setServoPower(bool enabled) {
  uint8_t mode = 0;
  uint8_t output = 0;
  uint8_t pull_up = 0;
  uint8_t pull_down = 0;
  if (!M5.In_I2C.readRegister(io_address_, 0x03, &mode, 1, 100000) ||
      !M5.In_I2C.readRegister(io_address_, 0x05, &output, 1, 100000) ||
      !M5.In_I2C.readRegister(io_address_, 0x09, &pull_up, 1, 100000) ||
      !M5.In_I2C.readRegister(io_address_, 0x0B, &pull_down, 1, 100000)) {
    return false;
  }
  mode |= 0x01;
  pull_up |= 0x01;
  pull_down &= ~0x01;
  output = enabled ? (output | 0x01) : (output & ~0x01);
  return M5.In_I2C.writeRegister(io_address_, 0x03, &mode, 1, 100000) &&
         M5.In_I2C.writeRegister(io_address_, 0x09, &pull_up, 1, 100000) &&
         M5.In_I2C.writeRegister(io_address_, 0x0B, &pull_down, 1, 100000) &&
         M5.In_I2C.writeRegister(io_address_, 0x05, &output, 1, 100000);
}

void MotionController::sendPacket(uint8_t id, uint8_t instruction, uint8_t address,
                                  const uint8_t* data, uint8_t length) {
  while (Serial1.available()) Serial1.read();
  const uint8_t packet_length = length + 3;
  uint8_t checksum = id + packet_length + instruction + address;
  Serial1.write(0xFF);
  Serial1.write(0xFF);
  Serial1.write(id);
  Serial1.write(packet_length);
  Serial1.write(instruction);
  Serial1.write(address);
  for (uint8_t index = 0; index < length; ++index) {
    Serial1.write(data[index]);
    checksum += data[index];
  }
  Serial1.write(static_cast<uint8_t>(~checksum));
  Serial1.flush();
}

bool MotionController::receiveStatus(uint8_t id, uint8_t* output,
                                     uint8_t expected_length, uint16_t timeout_ms) {
  const uint32_t started = millis();
  uint8_t previous = 0;
  while (millis() - started < timeout_ms) {
    if (!Serial1.available()) {
      delay(1);
      continue;
    }
    const uint8_t value = Serial1.read();
    if (previous == 0xFF && value == 0xFF) break;
    previous = value;
  }
  while (Serial1.available() < 3 && millis() - started < timeout_ms) delay(1);
  if (Serial1.available() < 3) return false;
  const uint8_t response_id = Serial1.read();
  const uint8_t response_length = Serial1.read();
  const uint8_t error = Serial1.read();
  if (response_id != id || response_length != expected_length + 2 || error != 0) return false;
  uint8_t checksum = response_id + response_length + error;
  for (uint8_t index = 0; index < expected_length; ++index) {
    while (!Serial1.available() && millis() - started < timeout_ms) delay(1);
    if (!Serial1.available()) return false;
    output[index] = Serial1.read();
    checksum += output[index];
  }
  while (!Serial1.available() && millis() - started < timeout_ms) delay(1);
  if (!Serial1.available()) return false;
  return Serial1.read() == static_cast<uint8_t>(~checksum);
}

bool MotionController::readRegister(uint8_t id, uint8_t address, uint8_t* output,
                                    uint8_t length) {
  for (uint8_t attempt = 0; attempt < 3; ++attempt) {
    const uint8_t requested = length;
    sendPacket(id, kInstructionRead, address, &requested, 1);
    if (receiveStatus(id, output, length)) {
      delay(10);
      return true;
    }
    delay(12);
  }
  return false;
}

bool MotionController::writeRegister(uint8_t id, uint8_t address,
                                     const uint8_t* data, uint8_t length) {
  sendPacket(id, kInstructionWrite, address, data, length);
  uint8_t ignored = 0;
  return receiveStatus(id, &ignored, 0);
}

bool MotionController::readPosition(uint8_t id, int& position) {
  uint8_t data[2]{};
  if (!readRegister(id, kPresentPositionRegister, data, sizeof(data))) return false;
  // Feetech SCSCL multi-byte registers are transmitted high byte first.
  position = (static_cast<int>(data[0]) << 8) | data[1];
  return position >= 0 && position <= 1023;
}

bool MotionController::readLimits(uint8_t id, int& minimum, int& maximum) {
  uint8_t limits[4]{};
  if (!readRegister(id, kMinAngleRegister, limits, sizeof(limits))) return false;
  minimum = (static_cast<int>(limits[0]) << 8) | limits[1];
  maximum = (static_cast<int>(limits[2]) << 8) | limits[3];
  return true;
}

bool MotionController::positionMode(uint8_t id) {
  int minimum = -1;
  int maximum = -1;
  if (!readLimits(id, minimum, maximum)) return false;
  return maximum > minimum;
}

bool MotionController::writeGoal(uint8_t id, int position, uint16_t duration_ms) {
  position = std::clamp(position, 0, 1000);
  // SCSCL goal time uses 10 ms units; the public API is expressed in ms.
  const uint16_t time_units =
      duration_ms == 0 ? 0 : std::clamp<uint16_t>((duration_ms + 5) / 10, 1, 1000);
  const uint8_t data[6] = {
      static_cast<uint8_t>(position >> 8),
      static_cast<uint8_t>(position & 0xFF),
      static_cast<uint8_t>(time_units >> 8),
      static_cast<uint8_t>(time_units & 0xFF),
      0,
      0,
  };
  return writeRegister(id, kGoalPositionRegister, data, sizeof(data));
}

bool MotionController::diagnose(MotionDiagnostic& diagnostic) {
  diagnostic = {};
  for (int& position : diagnostic.detected_positions) position = -1;
  diagnostic.io_address = io_address_;
  if (!initialized_) {
    diagnostic.detail = "servo bus is not initialized";
    last_error_ = diagnostic.detail;
    return false;
  }

  diagnostic.power_enabled = setServoPower(true);
  if (!diagnostic.power_enabled) {
    diagnostic.detail = "servo power enable failed";
    last_error_ = diagnostic.detail;
    return false;
  }

  // Read-only probe: no torque, mode, or goal register is written.
  // The yaw servo consistently needs slightly more than 500 ms after VM rises
  // before its first status packet; querying earlier creates a false negative.
  delay(650);
  const bool yaw_limits =
      readLimits(kYawId, diagnostic.yaw_min, diagnostic.yaw_max);
  diagnostic.yaw_feedback = readPosition(kYawId, diagnostic.yaw_raw);
  const bool pitch_limits =
      readLimits(kPitchId, diagnostic.pitch_min, diagnostic.pitch_max);
  diagnostic.pitch_feedback = readPosition(kPitchId, diagnostic.pitch_raw);

  if (!diagnostic.yaw_feedback || !diagnostic.pitch_feedback) {
    diagnostic.id_scan_performed = true;
    for (uint8_t id = 0; id <= 8; ++id) {
      int position = -1;
      if (id == kYawId && diagnostic.yaw_feedback) {
        position = diagnostic.yaw_raw;
      } else if (id == kPitchId && diagnostic.pitch_feedback) {
        position = diagnostic.pitch_raw;
      } else {
        readPosition(id, position);
      }
      if (position >= 0) {
        diagnostic.detected_id_mask |= static_cast<uint16_t>(1u << id);
        diagnostic.detected_positions[id] = position;
      }
    }
  } else {
    diagnostic.detected_id_mask =
        static_cast<uint16_t>((1u << kYawId) | (1u << kPitchId));
    diagnostic.detected_positions[kYawId] = diagnostic.yaw_raw;
    diagnostic.detected_positions[kPitchId] = diagnostic.pitch_raw;
  }
  diagnostic.power_released = setServoPower(false);

  diagnostic.success =
      yaw_limits && pitch_limits && diagnostic.yaw_feedback &&
      diagnostic.pitch_feedback && diagnostic.yaw_max > diagnostic.yaw_min &&
      diagnostic.pitch_max > diagnostic.pitch_min && diagnostic.power_released;
  verified_ = diagnostic.success;
  if (!diagnostic.power_released) {
    diagnostic.detail = "feedback read; servo power release failed";
  } else if (!yaw_limits || !diagnostic.yaw_feedback) {
    diagnostic.detail = "yaw servo feedback unavailable";
  } else if (!pitch_limits || !diagnostic.pitch_feedback) {
    diagnostic.detail = "pitch servo feedback unavailable";
  } else if (diagnostic.yaw_max <= diagnostic.yaw_min) {
    diagnostic.detail = "yaw servo is not in position mode";
  } else if (diagnostic.pitch_max <= diagnostic.pitch_min) {
    diagnostic.detail = "pitch servo is not in position mode";
  } else {
    diagnostic.detail = "read-only feedback verified; power released";
  }
  last_error_ = diagnostic.detail;
  Serial.printf(
      "motion: diagnostic ok=%d power=%d/%d yaw=%d[%d,%d] pitch=%d[%d,%d] detail=%s\n",
      diagnostic.success, diagnostic.power_enabled, diagnostic.power_released,
      diagnostic.yaw_raw, diagnostic.yaw_min, diagnostic.yaw_max,
      diagnostic.pitch_raw, diagnostic.pitch_min, diagnostic.pitch_max,
      diagnostic.detail);
  return diagnostic.success;
}

bool MotionController::setTorque(uint8_t id, bool enabled) {
  const uint8_t value = enabled ? 1 : 0;
  return writeRegister(id, kTorqueEnableRegister, &value, 1);
}

bool MotionController::armAfterWarmup() {
  if (!initialized_) {
    last_error_ = "servo bus is not initialized";
    return false;
  }
  if (!positionMode(kYawId)) {
    last_error_ = "yaw servo position mode unavailable";
    setServoPower(false);
    return false;
  }
  if (!positionMode(kPitchId)) {
    last_error_ = "pitch servo position mode unavailable";
    setServoPower(false);
    return false;
  }
  if (!readPosition(kYawId, yaw_raw_)) {
    last_error_ = "yaw position feedback unavailable";
    setServoPower(false);
    return false;
  }
  if (!readPosition(kPitchId, pitch_raw_)) {
    last_error_ = "pitch position feedback unavailable";
    setServoPower(false);
    return false;
  }
  // Set each goal to its measured position before enabling torque, preventing a jump.
  if (!writeGoal(kYawId, yaw_raw_, 0) || !writeGoal(kPitchId, pitch_raw_, 0) ||
      !setTorque(kYawId, true) || !setTorque(kPitchId, true)) {
    last_error_ = "servo safe-arm sequence failed";
    setTorque(kYawId, false);
    setTorque(kPitchId, false);
    setServoPower(false);
    return false;
  }
  ready_ = true;
  verified_ = true;
  last_error_ = "ready";
  return true;
}

void MotionController::failPending(const char* detail) {
  setTorque(kYawId, false);
  setTorque(kPitchId, false);
  setServoPower(false);
  ready_ = false;
  pending_ = false;
  phase_ = Phase::idle;
  last_error_ = detail;
  completion_ = {false,
                 yaw_raw_,
                 pitch_raw_,
                 target_yaw_raw_,
                 target_pitch_raw_,
                 -1,
                 -1,
                 detail};
  completion_ready_ = true;
  verified_ = false;
}

bool MotionController::move(bool has_yaw, float yaw_deg, bool has_pitch,
                            float pitch_deg, uint16_t duration_ms) {
  if (pending_) {
    last_error_ = "motion already in progress";
    return false;
  }
  if (!has_yaw && !has_pitch) {
    last_error_ = "motion target is empty";
    return false;
  }
  if (!initialized_ || !verified_ || !setServoPower(true)) {
    last_error_ = "could not begin verified servo warmup";
    return false;
  }
  pending_yaw_ = has_yaw;
  pending_pitch_ = has_pitch;
  requested_yaw_deg_ = std::clamp(yaw_deg, -35.0f, 35.0f);
  requested_pitch_deg_ = std::clamp(pitch_deg, 5.0f, 85.0f);
  requested_duration_ms_ = std::clamp<uint16_t>(duration_ms, 200, 1500);
  pending_ = true;
  completion_ready_ = false;
  phase_ = Phase::warming;
  // VM rise is the only long servo delay. Keep it asynchronous so audio and
  // WebSocket servicing continue while the controller becomes ready.
  completion_due_ms_ = millis() + 650;
  last_error_ = "motion dispatched; warming asynchronously";
  return true;
}

void MotionController::update(uint32_t now_ms) {
  if (!pending_ || static_cast<int32_t>(now_ms - completion_due_ms_) < 0) return;

  if (phase_ == Phase::warming) {
    if (!armAfterWarmup()) {
      failPending(last_error_);
      return;
    }
    int target_yaw = yaw_raw_;
    int target_pitch = pitch_raw_;
    if (pending_yaw_) {
      target_yaw = yaw_zero_ + lroundf(requested_yaw_deg_ * 16.0f / 5.0f);
    }
    if (pending_pitch_) {
      target_pitch =
          pitch_zero_ + lroundf(requested_pitch_deg_ * 16.0f / 5.0f);
    }
    if ((pending_yaw_ &&
         !writeGoal(kYawId, target_yaw, requested_duration_ms_)) ||
        (pending_pitch_ &&
         !writeGoal(kPitchId, target_pitch, requested_duration_ms_))) {
      failPending("servo rejected goal after warmup");
      return;
    }
    yaw_raw_ = target_yaw;
    pitch_raw_ = target_pitch;
    target_yaw_raw_ = target_yaw;
    target_pitch_raw_ = target_pitch;
    phase_ = Phase::moving;
    completion_due_ms_ = now_ms + requested_duration_ms_ + 180;
    return;
  }

  pending_ = false;
  phase_ = Phase::idle;
  int measured_yaw = -1;
  int measured_pitch = -1;
  const bool feedback = readPosition(kYawId, measured_yaw) &&
                        readPosition(kPitchId, measured_pitch);
  const int yaw_error = feedback ? abs(measured_yaw - target_yaw_raw_) : -1;
  const int pitch_error = feedback ? abs(measured_pitch - target_pitch_raw_) : -1;
  constexpr int kPositionToleranceRaw = 24;
  const bool reached =
      feedback && (!pending_yaw_ || yaw_error <= kPositionToleranceRaw) &&
      (!pending_pitch_ || pitch_error <= kPositionToleranceRaw);
  const bool yaw_torque_released = setTorque(kYawId, false);
  const bool pitch_torque_released = setTorque(kPitchId, false);
  const bool torque_released = yaw_torque_released && pitch_torque_released;
  const bool power_released = setServoPower(false);
  ready_ = false;
  completion_ = {
      reached && torque_released && power_released,
      measured_yaw,
      measured_pitch,
      target_yaw_raw_,
      target_pitch_raw_,
      yaw_error,
      pitch_error,
      !feedback ? "feedback failed; shutdown attempted"
                : (!reached ? "target tolerance missed; torque released"
                            : ((!torque_released || !power_released)
                                   ? "target reached; safe shutdown failed"
                                   : "target verified; torque and power released"))};
  pending_yaw_ = false;
  pending_pitch_ = false;
  verified_ = completion_.success;
  completion_ready_ = true;
}

bool MotionController::takeCompletion(MotionCompletion& completion) {
  if (!completion_ready_) return false;
  completion = completion_;
  completion_ready_ = false;
  return true;
}

}  // namespace stackchan
