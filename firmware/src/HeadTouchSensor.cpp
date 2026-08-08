#include "HeadTouchSensor.hpp"

namespace stackchan {

bool HeadTouchSensor::begin() {
  present_ = M5.In_I2C.scanID(kAddress, kBusFrequency);
  ready_ = present_ && configure();
  Serial.printf("head-touch: Si12T=%s address=0x%02X\n",
                ready_ ? "ready" : (present_ ? "configuration-failed" : "missing"),
                kAddress);
  return ready_;
}

bool HeadTouchSensor::configure() {
  // The production body exposes its three pads on Si12T CH1-CH3. Configure
  // all channels for calibration, then start with a sensitive threshold while
  // raw telemetry is collected for tuning on the assembled body. 0x00 selects
  // the lowest thresholds (0.4/0.5/0.6 percent) for hand-hover detection.
  for (uint8_t reg = kReferenceReset1Register;
       reg <= kCalibrationHold2Register; ++reg) {
    if (!M5.In_I2C.writeRegister8(kAddress, reg, 0x00, kBusFrequency)) return false;
  }
  if (!M5.In_I2C.writeRegister8(kAddress, kControl2Register, 0x0F,
                                kBusFrequency) ||
      !M5.In_I2C.writeRegister8(kAddress, kControl2Register, 0x03,
                                kBusFrequency) ||
      !M5.In_I2C.writeRegister8(kAddress, kControl1Register, 0x22,
                                kBusFrequency)) {
    return false;
  }
  for (uint8_t reg = kSensitivityFirstRegister;
       reg <= kSensitivityLastRegister; ++reg) {
    if (!M5.In_I2C.writeRegister8(kAddress, reg, 0x00, kBusFrequency)) return false;
  }
  delay(20);

  uint8_t control1 = 0;
  uint8_t control2 = 0;
  uint8_t sensitivity = 0;
  uint8_t channel_hold = 0xFF;
  return M5.In_I2C.readRegister(kAddress, kControl1Register, &control1, 1,
                                kBusFrequency) &&
         M5.In_I2C.readRegister(kAddress, kControl2Register, &control2, 1,
                                kBusFrequency) &&
         M5.In_I2C.readRegister(kAddress, kSensitivityFirstRegister, &sensitivity, 1,
                                kBusFrequency) &&
         M5.In_I2C.readRegister(kAddress, 0x0C, &channel_hold, 1, kBusFrequency) &&
         control1 == 0x22 && control2 == 0x03 && sensitivity == 0x00 &&
         channel_hold == 0x00;
}

bool HeadTouchSensor::readOutput(uint8_t& output) {
  return M5.In_I2C.readRegister(kAddress, kOutputRegister, &output, 1, kBusFrequency);
}

bool HeadTouchSensor::strongMultiZoneContact(uint8_t raw_output) {
  uint8_t active_channels = 0;
  uint8_t maximum_strength = 0;
  for (uint8_t channel = 0; channel < 3; ++channel) {
    const uint8_t strength = (raw_output >> (channel * 2)) & 0x03;
    if (strength > 0) ++active_channels;
    maximum_strength = max(maximum_strength, strength);
  }
  return active_channels >= 2 && maximum_strength >= 2;
}

void HeadTouchSensor::resetGesture() {
  zone_ = 0;
  strength_ = 0;
  start_zone_ = 0;
  furthest_zone_ = 0;
  gesture_zone_ = 0;
  gesture_strength_ = 0;
  max_strength_ = 0;
  pending_zone_ = 0;
  pending_strength_ = 0;
  activation_samples_ = 0;
  release_samples_ = 0;
  touch_started_ms_ = 0;
  hold_reported_ = false;
}

HeadGesture HeadTouchSensor::update(uint32_t now_ms) {
  if (!ready_ || now_ms - last_poll_ms_ < 25) return HeadGesture::none;
  last_poll_ms_ = now_ms;

  ++poll_count_;
  uint8_t output = 0;
  last_read_ok_ = readOutput(output);
  if (!last_read_ok_) {
    ++read_failures_;
    return HeadGesture::none;
  }
  raw_output_ = output;

  uint8_t next_zone = 0;
  uint8_t next_strength = 0;
  for (uint8_t channel = 0; channel < 3; ++channel) {
    const uint8_t value = (output >> (channel * 2)) & 0x03;
    if (value > next_strength) {
      next_strength = value;
      // Physical front-to-back order is the reverse of the Si12T bit order.
      next_zone = 3 - channel;
    }
  }

  // Require three consistent samples (75 ms) at both edges. A single noisy
  // capacitive sample must never launch an LLM turn or servo routine.
  if (zone_ == 0) {
    if (next_zone == 0) {
      pending_zone_ = 0;
      pending_strength_ = 0;
      activation_samples_ = 0;
      return HeadGesture::none;
    }
    if (pending_zone_ != next_zone) {
      pending_zone_ = next_zone;
      pending_strength_ = next_strength;
      activation_samples_ = 1;
    } else {
      pending_strength_ = max(pending_strength_, next_strength);
      activation_samples_ = min<uint8_t>(activation_samples_ + 1, 3);
    }
    if (activation_samples_ < 3) return HeadGesture::none;
    zone_ = pending_zone_;
    strength_ = pending_strength_;
    pending_zone_ = 0;
    pending_strength_ = 0;
    activation_samples_ = 0;
  } else if (next_zone == 0) {
    release_samples_ = min<uint8_t>(release_samples_ + 1, 3);
    if (release_samples_ < 3) return HeadGesture::none;
    release_samples_ = 0;
  } else {
    release_samples_ = 0;
    zone_ = next_zone;
    strength_ = next_strength;
  }

  const uint8_t previous_zone = zone_;
  if (next_zone == 0) {
    zone_ = 0;
    strength_ = 0;
  }

  if (touch_started_ms_ == 0 && zone_ != 0) {
    start_zone_ = zone_;
    furthest_zone_ = zone_;
    max_strength_ = strength_;
    touch_started_ms_ = now_ms;
    hold_reported_ = false;
    Serial.printf("head-touch: start zone=%u strength=%u raw=%02X\n", zone_, strength_,
                  raw_output_);
    return HeadGesture::none;
  }

  if (zone_ != 0) {
    max_strength_ = max(max_strength_, strength_);
    if (abs(static_cast<int>(zone_) - static_cast<int>(start_zone_)) >
        abs(static_cast<int>(furthest_zone_) - static_cast<int>(start_zone_))) {
      furthest_zone_ = zone_;
    }
    if (!hold_reported_ && now_ms - touch_started_ms_ >= 700) {
      hold_reported_ = true;
      gesture_zone_ = zone_;
      gesture_strength_ = max_strength_;
      return HeadGesture::hold;
    }
    return HeadGesture::none;
  }

  if (previous_zone != 0) {
    const int travel = static_cast<int>(furthest_zone_) - static_cast<int>(start_zone_);
    gesture_zone_ = start_zone_;
    gesture_strength_ = max_strength_;
    HeadGesture result = hold_reported_ ? HeadGesture::release : HeadGesture::touch;
    if (abs(travel) >= 2 && now_ms - touch_started_ms_ <= 1100) {
      result = travel > 0 ? HeadGesture::swipe_forward : HeadGesture::swipe_backward;
    }
    touch_started_ms_ = 0;
    start_zone_ = 0;
    furthest_zone_ = 0;
    max_strength_ = 0;
    hold_reported_ = false;
    return result;
  }
  return HeadGesture::none;
}

const char* headGestureName(HeadGesture gesture) {
  switch (gesture) {
    case HeadGesture::touch:
      return "touch";
    case HeadGesture::hold:
      return "hold";
    case HeadGesture::swipe_forward:
      return "swipe_forward";
    case HeadGesture::swipe_backward:
      return "swipe_backward";
    case HeadGesture::release:
      return "release";
    default:
      return "none";
  }
}

}  // namespace stackchan
