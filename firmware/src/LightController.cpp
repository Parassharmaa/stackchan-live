#include "LightController.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace stackchan {
namespace {

uint8_t scale(uint8_t value, float brightness) {
  return static_cast<uint8_t>(std::clamp(value * brightness, 0.0f, 255.0f));
}

}  // namespace

bool LightController::begin() {
  if (M5.In_I2C.scanID(kAddressLow, kBusFrequency)) {
    address_ = kAddressLow;
  } else if (M5.In_I2C.scanID(kAddressHigh, kBusFrequency)) {
    address_ = kAddressHigh;
  } else {
    Serial.println("lights: M5IOE1 missing");
    return false;
  }
  present_ = true;
  const uint8_t awake = 0;
  M5.In_I2C.writeRegister(address_, kI2cConfigRegister, &awake, 1, kBusFrequency);
  off();
  update(millis());
  Serial.printf("lights: ready address=0x%02X count=%u\n", address_, kLedCount);
  return true;
}

bool LightController::set(uint8_t red, uint8_t green, uint8_t blue, float brightness,
                          LightAnimation animation) {
  red_ = red;
  green_ = green;
  blue_ = blue;
  brightness_ = std::clamp(brightness, 0.0f, 0.35f);
  animation_ = animation;
  animation_started_ms_ = millis();
  dirty_ = true;
  update(animation_started_ms_);
  return last_write_successful_;
}

bool LightController::off() {
  return set(0, 0, 0, 0.0f, LightAnimation::solid);
}

uint16_t LightController::rgb565(uint8_t red, uint8_t green, uint8_t blue) {
  return static_cast<uint16_t>(((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3));
}

uint16_t LightController::wheel(uint8_t position, float brightness) {
  position = 255 - position;
  uint8_t red;
  uint8_t green;
  uint8_t blue;
  if (position < 85) {
    red = 255 - position * 3;
    green = 0;
    blue = position * 3;
  } else if (position < 170) {
    position -= 85;
    red = 0;
    green = position * 3;
    blue = 255 - position * 3;
  } else {
    position -= 170;
    red = position * 3;
    green = 255 - position * 3;
    blue = 0;
  }
  return rgb565(scale(red, brightness), scale(green, brightness), scale(blue, brightness));
}

bool LightController::writeFrame(const uint16_t (&colors)[kLedCount]) {
  uint8_t payload[kLedCount * 2];
  for (uint8_t index = 0; index < kLedCount; ++index) {
    payload[index * 2] = colors[index] & 0xFF;
    payload[index * 2 + 1] = colors[index] >> 8;
  }
  if (!M5.In_I2C.writeRegister(address_, kLedRamRegister, payload, sizeof(payload),
                               kBusFrequency)) {
    return false;
  }
  const uint8_t refresh = 0x40 | kLedCount;
  return M5.In_I2C.writeRegister(address_, kLedConfigRegister, &refresh, 1,
                                 kBusFrequency);
}

void LightController::update(uint32_t now_ms) {
  if (!present_) {
    last_write_successful_ = false;
    return;
  }
  if (!dirty_ && now_ms - last_frame_ms_ < kFrameIntervalMs) return;
  last_frame_ms_ = now_ms;
  const uint32_t elapsed = now_ms - animation_started_ms_;
  const float phase = static_cast<float>(elapsed % 1400) / 1400.0f;
  uint16_t colors[kLedCount]{};

  if (animation_ == LightAnimation::rainbow) {
    const uint8_t offset = (elapsed / 8) & 0xFF;
    for (uint8_t index = 0; index < kLedCount; ++index) {
      colors[index] = wheel(offset + index * 256 / kLedCount, brightness_);
    }
  } else if (animation_ == LightAnimation::chase) {
    const uint8_t head = (elapsed / 90) % kLedCount;
    for (uint8_t index = 0; index < kLedCount; ++index) {
      const uint8_t distance = (index + kLedCount - head) % kLedCount;
      const float trail = distance == 0 ? 1.0f : (distance == 1 ? 0.3f : 0.04f);
      colors[index] = rgb565(scale(red_, brightness_ * trail),
                             scale(green_, brightness_ * trail),
                             scale(blue_, brightness_ * trail));
    }
  } else if (animation_ == LightAnimation::twinkle) {
    for (uint8_t index = 0; index < kLedCount; ++index) {
      random_state_ = random_state_ * 1664525u + 1013904223u;
      const float sparkle = ((random_state_ >> 28) == 0) ? 1.0f : 0.08f;
      colors[index] = rgb565(scale(red_, brightness_ * sparkle),
                             scale(green_, brightness_ * sparkle),
                             scale(blue_, brightness_ * sparkle));
    }
  } else {
    const float pulse = animation_ == LightAnimation::pulse
                            ? 0.18f + 0.82f * (0.5f - 0.5f * cosf(phase * 2.0f * PI))
                            : 1.0f;
    const uint16_t color = rgb565(scale(red_, brightness_ * pulse),
                                  scale(green_, brightness_ * pulse),
                                  scale(blue_, brightness_ * pulse));
    std::fill(std::begin(colors), std::end(colors), color);
  }
  last_write_successful_ = writeFrame(colors);
  dirty_ = !last_write_successful_;
  if (!last_write_successful_) Serial.println("lights: frame write failed");
}

LightAnimation lightAnimationFromString(const char* value) {
  if (strcmp(value, "pulse") == 0) return LightAnimation::pulse;
  if (strcmp(value, "rainbow") == 0) return LightAnimation::rainbow;
  if (strcmp(value, "chase") == 0) return LightAnimation::chase;
  if (strcmp(value, "twinkle") == 0) return LightAnimation::twinkle;
  return LightAnimation::solid;
}

}  // namespace stackchan
