#pragma once

#include <Arduino.h>

namespace stackchan {

struct CameraCapture {
  uint8_t* jpeg = nullptr;
  size_t length = 0;
  uint16_t width = 0;
  uint16_t height = 0;
  bool control_bus_restored = false;
  String error;

  void release();
};

class CameraEndpoint {
 public:
  CameraCapture capture(uint8_t quality);
};

}  // namespace stackchan
