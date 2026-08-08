#include "CameraEndpoint.hpp"

#include <M5Unified.h>
#include <esp_camera.h>
#include <img_converters.h>

namespace stackchan {
namespace {

camera_config_t cameraConfig() {
  camera_config_t config{};
  config.pin_pwdn = -1;
  config.pin_reset = -1;
  config.pin_xclk = -1;
  config.pin_sccb_sda = 12;
  config.pin_sccb_scl = 11;
  config.pin_d7 = 47;
  config.pin_d6 = 48;
  config.pin_d5 = 16;
  config.pin_d4 = 15;
  config.pin_d3 = 42;
  config.pin_d2 = 41;
  config.pin_d1 = 40;
  config.pin_d0 = 39;
  config.pin_vsync = 46;
  config.pin_href = 38;
  config.pin_pclk = 45;
  config.xclk_freq_hz = 20000000;
  config.ledc_timer = LEDC_TIMER_0;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.pixel_format = PIXFORMAT_RGB565;
  config.frame_size = FRAMESIZE_QVGA;
  config.jpeg_quality = 0;
  config.fb_count = 2;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.sccb_i2c_port = -1;
  return config;
}

}  // namespace

void CameraCapture::release() {
  if (jpeg != nullptr) free(jpeg);
  jpeg = nullptr;
  length = 0;
}

CameraCapture CameraEndpoint::capture(uint8_t quality) {
  CameraCapture result;
  // The GC0308 shares GPIO 11/12 with the CoreS3 internal control bus. Camera
  // ownership is therefore deliberately bounded to this still capture.
  M5.In_I2C.release();
  camera_config_t config = cameraConfig();
  const esp_err_t init_error = esp_camera_init(&config);
  if (init_error != ESP_OK) {
    result.error = String("camera init failed: ") + esp_err_to_name(init_error);
    result.control_bus_restored = M5.In_I2C.begin();
    return result;
  }

  // Give auto-exposure several frames to settle; the first GC0308 frame is
  // commonly dark after its power/control bus has just been acquired.
  camera_fb_t* frame = nullptr;
  for (uint8_t warmup = 0; warmup < 2; ++warmup) {
    frame = esp_camera_fb_get();
    if (frame != nullptr) esp_camera_fb_return(frame);
    delay(60);
  }
  frame = esp_camera_fb_get();
  if (frame == nullptr) {
    result.error = "camera frame unavailable";
  } else {
    result.width = frame->width;
    result.height = frame->height;
    if (!frame2jpg(frame, constrain(quality, 40, 85), &result.jpeg, &result.length)) {
      result.error = "JPEG conversion failed";
    }
    esp_camera_fb_return(frame);
  }
  esp_camera_deinit();
  result.control_bus_restored = M5.In_I2C.begin();
  if (!result.control_bus_restored) {
    result.release();
    result.error = "internal control bus did not recover after capture";
  }
  return result;
}

}  // namespace stackchan
