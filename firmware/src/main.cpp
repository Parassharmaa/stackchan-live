#include <Arduino.h>
#include <ArduinoJson.h>
#include <M5Unified.h>
#include <Preferences.h>
#include <WebSocketsClient.h>
#include <WiFi.h>
#include <esp_system.h>
#include <esp_heap_caps.h>
#include <mbedtls/md.h>

#include <cstring>
#include <iterator>

#include "AudioEndpoint.hpp"
#include "CameraEndpoint.hpp"
#include "CodexBleController.hpp"
#include "DeviceProtocol.hpp"
#include "FaceRenderer.hpp"
#include "HeadTouchSensor.hpp"
#include "LightController.hpp"
#include "MotionController.hpp"

#if __has_include("LocalConfig.hpp")
#include "LocalConfig.hpp"
#endif

#if __has_include("DeviceSecret.hpp")
#include "DeviceSecret.hpp"
#else
#define STACKCHAN_DEVICE_TOKEN ""
#endif

#ifndef STACKCHAN_SERVER_HOST
#define STACKCHAN_SERVER_HOST "stackchan-server.local"
#endif
#ifndef STACKCHAN_SERVER_PORT
#define STACKCHAN_SERVER_PORT 8765
#endif
#ifndef STACKCHAN_SERVER_PATH
#define STACKCHAN_SERVER_PATH "/v1/device"
#endif

namespace {

WebSocketsClient socket_client;
stackchan::FaceRenderer face(M5.Display);
stackchan::AudioEndpoint audio(socket_client);
stackchan::CameraEndpoint camera;
stackchan::HeadTouchSensor head_touch;
stackchan::LightController lights;
stackchan::MotionController motion;
stackchan::CodexBleController codex;
bool server_connected = false;
String pending_server_nonce;
String pending_device_nonce;
bool reported_playback_active = false;
uint32_t last_energy_ms = 0;
uint32_t last_audio_telemetry_ms = 0;
uint32_t last_head_sensor_telemetry_ms = 0;
uint32_t last_playback_ended_ms = 0;
uint32_t last_motion_ended_ms = 0;
uint32_t boot_count = 0;
bool head_sensor_rearm_pending = true;
uint8_t head_sensor_release_samples = 0;
uint32_t head_interrupt_contact_started_ms = 0;
bool head_interrupt_latched = false;
uint32_t approval_waiting_until_ms = 0;
bool held_face_active = false;
bool codex_mic_pressed = false;
stackchan::CodexAgentState last_codex_motion_state =
    stackchan::CodexAgentState::off;
String held_face_state = "idle";
String held_face_emotion = "neutral";
float held_face_intensity = 0.5f;

constexpr uint32_t kHeadSensorPlaybackGuardMs = 500;
constexpr uint32_t kHeadSensorMotionGuardMs = 1000;
constexpr uint8_t kHeadSensorRearmSamples = 8;
constexpr uint32_t kHeadInterruptHoldMs = 700;

void clearHeldFace() { held_face_active = false; }

void applyHeldFace() {
  if (!held_face_active) return;
  face.setState(stackchan::faceStateFromString(held_face_state));
  face.setEmotion(held_face_emotion, held_face_intensity);
  face.setStatus(held_face_emotion);
  if (server_connected) {
    JsonDocument document;
    document["type"] = "telemetry";
    document["payload"]["component"] = "face_hold";
    document["payload"]["state"] = held_face_state;
    document["payload"]["emotion"] = held_face_emotion;
    document["payload"]["intensity"] = held_face_intensity;
    String output;
    serializeJson(document, output);
    socket_client.sendTXT(output);
  }
}

bool flushAudioWithSensorGuard() {
  const bool was_active = audio.playbackActive();
  const bool success = audio.flush();
  if (was_active) last_playback_ended_ms = millis();
  return success;
}

struct RoutineMotionStep {
  float yaw_deg;
  float pitch_deg;
  uint16_t duration_ms;
};

constexpr RoutineMotionStep kGreetMotion[] = {
    {16.0f, 42.0f, 420}, {-12.0f, 38.0f, 420}, {0.0f, 45.0f, 450}};
constexpr RoutineMotionStep kCelebrateMotion[] = {
    {-24.0f, 32.0f, 400}, {24.0f, 32.0f, 400}, {0.0f, 45.0f, 480}};
constexpr RoutineMotionStep kCuriousMotion[] = {
    {14.0f, 25.0f, 460}, {22.0f, 32.0f, 380}, {8.0f, 28.0f, 380},
    {0.0f, 45.0f, 480}};
constexpr RoutineMotionStep kComfortMotion[] = {
    {0.0f, 55.0f, 520}, {-6.0f, 50.0f, 420}, {6.0f, 50.0f, 420},
    {0.0f, 45.0f, 520}};
constexpr RoutineMotionStep kDanceMotion[] = {
    {-24.0f, 32.0f, 380}, {24.0f, 52.0f, 380}, {-20.0f, 48.0f, 380},
    {20.0f, 30.0f, 380}, {0.0f, 45.0f, 480}};
constexpr RoutineMotionStep kWakeUpMotion[] = {
    {0.0f, 65.0f, 450}, {-8.0f, 52.0f, 380}, {8.0f, 40.0f, 380},
    {0.0f, 38.0f, 480}};
constexpr RoutineMotionStep kFocusMotion[] = {
    {0.0f, 45.0f, 450}, {-5.0f, 40.0f, 360}, {0.0f, 40.0f, 450}};
constexpr RoutineMotionStep kGoodNightMotion[] = {
    {8.0f, 50.0f, 450}, {-8.0f, 56.0f, 450}, {0.0f, 60.0f, 520}};

const RoutineMotionStep* active_routine_steps = nullptr;
size_t active_routine_step_count = 0;
size_t active_routine_step_index = 0;
String active_routine_name;
String active_routine_request_id;
String active_motion_request_id;
bool active_routine_light_written = true;

bool validRequestId(const String& request_id) {
  if (request_id.length() != stackchan::kImageRequestIdSize) return false;
  for (size_t index = 0; index < request_id.length(); ++index) {
    const char value = request_id[index];
    if (!((value >= '0' && value <= '9') || (value >= 'a' && value <= 'f'))) {
      return false;
    }
  }
  return true;
}

uint32_t incrementPersistentBootCount() {
  Preferences preferences;
  if (!preferences.begin("stackchan-meta", false)) return 0;
  const uint32_t previous = preferences.getUInt("boot_count", 0);
  const uint32_t current = previous == UINT32_MAX ? 1 : previous + 1;
  preferences.putUInt("boot_count", current);
  preferences.end();
  return current;
}

const char* resetReasonName(esp_reset_reason_t reason) {
  switch (reason) {
    case ESP_RST_POWERON: return "power_on";
    case ESP_RST_EXT: return "external";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "interrupt_watchdog";
    case ESP_RST_TASK_WDT: return "task_watchdog";
    case ESP_RST_WDT: return "watchdog";
    case ESP_RST_DEEPSLEEP: return "deep_sleep";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SDIO: return "sdio";
    default: return "unknown";
  }
}

void sendControl(const char* type) {
  JsonDocument document;
  document["type"] = type;
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

void sendBargeIn(const char* reason) {
  JsonDocument document;
  document["type"] = "barge_in";
  document["payload"]["reason"] = reason;
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

void sendPlaybackDuckState(bool enabled, float gain,
                           const String& request_id = String()) {
  if (!server_connected) return;
  JsonDocument document;
  document["type"] = "playback.duck.state";
  if (!request_id.isEmpty()) document["request_id"] = request_id;
  document["payload"]["enabled"] = enabled;
  document["payload"]["gain"] = gain;
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

void sendPlaybackFlushState(bool success, uint32_t duration_us,
                            const String& request_id = String()) {
  if (!server_connected) return;
  JsonDocument document;
  document["type"] = "playback.flush.state";
  if (!request_id.isEmpty()) document["request_id"] = request_id;
  document["payload"]["success"] = success;
  document["payload"]["active"] = audio.playbackActive();
  document["payload"]["duration_us"] = duration_us;
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

String hmacHex(const String& message) {
  const mbedtls_md_info_t* info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (info == nullptr || message.isEmpty() || strlen(STACKCHAN_DEVICE_TOKEN) == 0) {
    return "";
  }
  unsigned char digest[32];
  if (mbedtls_md_hmac(
          info, reinterpret_cast<const unsigned char*>(STACKCHAN_DEVICE_TOKEN),
          strlen(STACKCHAN_DEVICE_TOKEN),
          reinterpret_cast<const unsigned char*>(message.c_str()), message.length(),
          digest) != 0) {
    return "";
  }
  static constexpr char kHex[] = "0123456789abcdef";
  char encoded[65];
  for (size_t index = 0; index < sizeof(digest); ++index) {
    encoded[index * 2] = kHex[digest[index] >> 4];
    encoded[index * 2 + 1] = kHex[digest[index] & 0x0F];
  }
  encoded[64] = '\0';
  return String(encoded);
}

String randomNonce() {
  unsigned char bytes[32];
  esp_fill_random(bytes, sizeof(bytes));
  static constexpr char kHex[] = "0123456789abcdef";
  char encoded[65];
  for (size_t index = 0; index < sizeof(bytes); ++index) {
    encoded[index * 2] = kHex[bytes[index] >> 4];
    encoded[index * 2 + 1] = kHex[bytes[index] & 0x0F];
  }
  encoded[64] = '\0';
  return String(encoded);
}

String pairingProof(const char* role, const String& server_nonce,
                    const String& device_nonce, const String& device_id) {
  if (server_nonce.isEmpty() || device_nonce.isEmpty() || device_id.isEmpty()) return "";
  return hmacHex(String("stackchan-v1:") + role + ":" + server_nonce + ":" +
                 device_nonce + ":" + device_id);
}

bool constantTimeEqual(const String& left, const String& right) {
  if (left.length() != right.length() || left.isEmpty()) return false;
  uint8_t difference = 0;
  for (size_t index = 0; index < left.length(); ++index) {
    difference |= static_cast<uint8_t>(left[index] ^ right[index]);
  }
  return difference == 0;
}

void sendAuthenticatedHello(const String& server_nonce) {
  const String device_id = WiFi.macAddress();
  pending_server_nonce = server_nonce;
  pending_device_nonce = randomNonce();
  const String auth_response = pairingProof(
      "device", pending_server_nonce, pending_device_nonce, device_id);
  if (auth_response.isEmpty()) {
    face.setState(stackchan::FaceState::error);
    face.setStatus("Pairing error");
    return;
  }
  JsonDocument hello;
  hello["type"] = "hello";
  hello["payload"]["protocol_version"] = 1;
  hello["payload"]["device_id"] = device_id;
  hello["payload"]["device_nonce"] = pending_device_nonce;
  hello["payload"]["auth_response"] = auth_response;
  hello["payload"]["model"] = "StackChan-CoreS3";
  hello["payload"]["audio_mode"] =
      audio.duplexReady() ? "full_duplex" : "half_duplex_fallback";
  hello["payload"]["physical_render_reference"] = audio.duplexReady();
  hello["payload"]["input_sample_rate"] = 16000;
  hello["payload"]["output_sample_rate"] = 24000;
  hello["payload"]["turn_detection"] = "auto";
  hello["payload"]["boot_count"] = boot_count;
  hello["payload"]["reset_reason"] = resetReasonName(esp_reset_reason());
  hello["payload"]["free_heap_bytes"] = ESP.getFreeHeap();
  hello["payload"]["free_psram_bytes"] = ESP.getFreePsram();
  hello["payload"]["face_speaking_cache"] = face.speakingFramesCached();
  hello["payload"]["motion_verified"] = motion.verified();
  hello["payload"]["head_sensor_present"] = head_touch.present();
  hello["payload"]["head_sensor_ready"] = head_touch.ready();
  hello["payload"]["camera_present"] = true;
  hello["payload"]["camera_mode"] = "explicit_still";
  String output;
  serializeJson(hello, output);
  socket_client.sendTXT(output);
}

void reportPlaybackState(bool active) {
  if (!server_connected || reported_playback_active == active) return;
  reported_playback_active = active;
  JsonDocument document;
  document["type"] = "playback.state";
  document["payload"]["active"] = active;
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

void sendHeadGesture(stackchan::HeadGesture gesture) {
  if (!server_connected) return;
  JsonDocument document;
  document["type"] = "sensor.head";
  document["payload"]["gesture"] = stackchan::headGestureName(gesture);
  document["payload"]["zone"] = head_touch.gestureZone();
  document["payload"]["strength"] = head_touch.gestureStrength();
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

void sendHeadInterrupt(uint8_t raw_output) {
  if (!server_connected) return;
  uint8_t maximum_strength = 0;
  for (uint8_t channel = 0; channel < 3; ++channel) {
    const uint8_t strength = (raw_output >> (channel * 2)) & 0x03;
    maximum_strength = max(maximum_strength, strength);
  }
  JsonDocument document;
  document["type"] = "sensor.head";
  document["payload"]["gesture"] = "interrupt_hold";
  document["payload"]["zone"] = 0;
  document["payload"]["strength"] = maximum_strength;
  document["payload"]["raw_output"] = raw_output & 0x3F;
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

void sendMotionResult(const char* stage, bool success, const char* detail,
                      int yaw_raw = -1, int pitch_raw = -1,
                      int yaw_target_raw = -1, int pitch_target_raw = -1,
                      int yaw_error_raw = -1, int pitch_error_raw = -1,
                      bool has_yaw_deg = false, float yaw_deg = 0.0f,
                      bool has_pitch_deg = false, float pitch_deg = 45.0f,
                      const String& request_id = String()) {
  if (!server_connected) return;
  JsonDocument document;
  document["type"] = "tool.result";
  if (!request_id.isEmpty()) document["request_id"] = request_id;
  document["payload"]["tool"] = "move_head";
  document["payload"]["stage"] = stage;
  document["payload"]["success"] = success;
  document["payload"]["detail"] = detail;
  if (yaw_raw >= 0) document["payload"]["yaw_raw"] = yaw_raw;
  if (pitch_raw >= 0) document["payload"]["pitch_raw"] = pitch_raw;
  if (yaw_target_raw >= 0) document["payload"]["yaw_target_raw"] = yaw_target_raw;
  if (pitch_target_raw >= 0)
    document["payload"]["pitch_target_raw"] = pitch_target_raw;
  if (yaw_error_raw >= 0) document["payload"]["yaw_error_raw"] = yaw_error_raw;
  if (pitch_error_raw >= 0)
    document["payload"]["pitch_error_raw"] = pitch_error_raw;
  if (has_yaw_deg) document["payload"]["yaw_deg"] = yaw_deg;
  if (has_pitch_deg) document["payload"]["pitch_deg"] = pitch_deg;
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

void sendFaceResult(const String& state, const String& emotion, float intensity,
                    const String& request_id = String()) {
  if (!server_connected) return;
  JsonDocument document;
  document["type"] = "tool.result";
  if (!request_id.isEmpty()) document["request_id"] = request_id;
  document["payload"]["tool"] = "set_face";
  document["payload"]["stage"] = "completed";
  document["payload"]["success"] = true;
  document["payload"]["state"] = state;
  document["payload"]["emotion"] = emotion;
  document["payload"]["intensity"] = intensity;
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

void sendLightResult(int red, int green, int blue, float brightness,
                     const String& animation, bool success,
                     const String& request_id = String()) {
  if (!server_connected) return;
  JsonDocument document;
  document["type"] = "tool.result";
  if (!request_id.isEmpty()) document["request_id"] = request_id;
  document["payload"]["tool"] = "set_lights";
  document["payload"]["stage"] = "completed";
  document["payload"]["success"] = success;
  document["payload"]["detail"] =
      success ? "LED frame written over I2C" : "LED frame write failed";
  document["payload"]["red"] = red;
  document["payload"]["green"] = green;
  document["payload"]["blue"] = blue;
  document["payload"]["brightness"] = brightness;
  document["payload"]["animation"] = animation;
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

void sendCameraResult(bool success, const String& detail, const String& request_id,
                      uint16_t width = 0, uint16_t height = 0,
                      size_t bytes = 0, bool control_bus_restored = false) {
  if (!server_connected) return;
  JsonDocument document;
  document["type"] = "tool.result";
  if (!request_id.isEmpty()) document["request_id"] = request_id;
  document["payload"]["tool"] = "capture_photo";
  document["payload"]["stage"] = success ? "completed" : "failed";
  document["payload"]["success"] = success;
  document["payload"]["detail"] = detail;
  document["payload"]["format"] = "jpeg";
  document["payload"]["width"] = width;
  document["payload"]["height"] = height;
  document["payload"]["bytes"] = bytes;
  document["payload"]["control_bus_restored"] = control_bus_restored;
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

void captureAndSendPhoto(uint8_t quality, const String& request_id) {
  lights.set(255, 255, 255, 0.28f, stackchan::LightAnimation::twinkle);
  face.setState(stackchan::FaceState::thinking);
  face.setEmotion("curious", 0.9f);
  face.setStatus("Camera");

  stackchan::CameraCapture capture = camera.capture(quality);
  bool sent = false;
  if (capture.jpeg != nullptr && capture.length > 0 &&
      capture.control_bus_restored) {
    const size_t packet_length = sizeof(stackchan::ImageHeader) + capture.length;
    auto* packet = static_cast<uint8_t*>(
        heap_caps_malloc(packet_length, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (packet == nullptr) packet = static_cast<uint8_t*>(malloc(packet_length));
    if (packet != nullptr) {
      stackchan::ImageHeader header{{'S', 'T', 'K', 'I'},
                                    stackchan::kProtocolVersion,
                                    static_cast<uint8_t>(stackchan::ImageFormat::jpeg),
                                    capture.width,
                                    capture.height,
                                    {}};
      memcpy(header.request_id, request_id.c_str(), stackchan::kImageRequestIdSize);
      memcpy(packet, &header, sizeof(header));
      memcpy(packet + sizeof(header), capture.jpeg, capture.length);
      sent = socket_client.sendBIN(packet, packet_length);
      free(packet);
    } else {
      capture.error = "camera packet allocation failed";
    }
  }
  const uint16_t width = capture.width;
  const uint16_t height = capture.height;
  const size_t bytes = capture.length;
  const bool restored = capture.control_bus_restored;
  String detail = capture.error;
  capture.release();
  // Releasing and reacquiring the shared I2C bus can transiently read all
  // capacitive channels as active. Require physical release samples before
  // this state is ever eligible to interrupt the following spoken reply.
  head_touch.resetGesture();
  head_sensor_rearm_pending = true;
  head_sensor_release_samples = 0;
  head_interrupt_contact_started_ms = 0;
  head_interrupt_latched = false;
  if (sent) {
    detail = "onboard camera still captured and transferred";
  } else if (detail.isEmpty()) {
    detail = "camera transfer failed";
  }
  if (restored) {
    lights.set(255, 105, 145, 0.08f, stackchan::LightAnimation::solid);
  }
  face.setState(sent ? stackchan::FaceState::happy : stackchan::FaceState::error);
  face.setEmotion(sent ? "joy" : "worried", sent ? 0.8f : 0.7f);
  face.setStatus(sent ? "Photo captured" : "Camera failed");
  sendCameraResult(sent, detail, request_id, width, height, bytes, restored);
}

void sendRoutineMotionResult(const char* stage, bool success, const char* detail,
                             int step = -1,
                             const stackchan::MotionCompletion* completion = nullptr) {
  if (!server_connected) return;
  JsonDocument document;
  document["type"] = "tool.result";
  if (!active_routine_request_id.isEmpty())
    document["request_id"] = active_routine_request_id;
  document["payload"]["tool"] = "play_routine";
  document["payload"]["routine"] = active_routine_name;
  document["payload"]["stage"] = stage;
  document["payload"]["success"] = success;
  document["payload"]["detail"] = detail;
  if (step >= 0) document["payload"]["step"] = step;
  document["payload"]["step_count"] = active_routine_step_count;
  if (completion != nullptr) {
    document["payload"]["yaw_raw"] = completion->yaw_raw;
    document["payload"]["pitch_raw"] = completion->pitch_raw;
    document["payload"]["yaw_target_raw"] = completion->yaw_target_raw;
    document["payload"]["pitch_target_raw"] = completion->pitch_target_raw;
    document["payload"]["yaw_error_raw"] = completion->yaw_error_raw;
    document["payload"]["pitch_error_raw"] = completion->pitch_error_raw;
  }
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

bool dispatchRoutineMotionStep() {
  if (active_routine_steps == nullptr ||
      active_routine_step_index >= active_routine_step_count) {
    return false;
  }
  const auto& step = active_routine_steps[active_routine_step_index];
  return motion.move(true, step.yaw_deg, true, step.pitch_deg, step.duration_ms);
}

bool startRoutineMotion(const String& routine, const String& request_id) {
  if (motion.active() || active_routine_steps != nullptr) {
    const String previous_routine = active_routine_name;
    const String previous_request_id = active_routine_request_id;
    active_routine_name = routine;
    active_routine_request_id = request_id;
    sendRoutineMotionResult("rejected", false, "another motion is already active");
    active_routine_name = previous_routine;
    active_routine_request_id = previous_request_id;
    return false;
  }
  active_routine_name = routine;
  active_routine_request_id = request_id;
  active_routine_light_written = true;
  if (routine == "celebrate") {
    active_routine_steps = kCelebrateMotion;
    active_routine_step_count = std::size(kCelebrateMotion);
  } else if (routine == "curious") {
    active_routine_steps = kCuriousMotion;
    active_routine_step_count = std::size(kCuriousMotion);
  } else if (routine == "comfort") {
    active_routine_steps = kComfortMotion;
    active_routine_step_count = std::size(kComfortMotion);
  } else if (routine == "dance") {
    active_routine_steps = kDanceMotion;
    active_routine_step_count = std::size(kDanceMotion);
  } else if (routine == "wake_up") {
    active_routine_steps = kWakeUpMotion;
    active_routine_step_count = std::size(kWakeUpMotion);
  } else if (routine == "focus") {
    active_routine_steps = kFocusMotion;
    active_routine_step_count = std::size(kFocusMotion);
  } else if (routine == "good_night") {
    active_routine_steps = kGoodNightMotion;
    active_routine_step_count = std::size(kGoodNightMotion);
  } else {
    active_routine_steps = kGreetMotion;
    active_routine_step_count = std::size(kGreetMotion);
  }
  active_routine_step_index = 0;
  if (!dispatchRoutineMotionStep()) {
    sendRoutineMotionResult("rejected", false, motion.lastError());
    active_routine_steps = nullptr;
    active_routine_step_count = 0;
    active_routine_request_id = "";
    return false;
  }
  sendRoutineMotionResult("dispatched", true, "multi-step routine accepted", 0);
  return true;
}

void sendMotionDiagnostic(const stackchan::MotionDiagnostic& diagnostic,
                          const String& request_id = String()) {
  if (!server_connected) return;
  JsonDocument document;
  document["type"] = "tool.result";
  if (!request_id.isEmpty()) document["request_id"] = request_id;
  document["payload"]["tool"] = "motion_diagnostic";
  document["payload"]["stage"] = "read_only";
  document["payload"]["success"] = diagnostic.success;
  document["payload"]["detail"] = diagnostic.detail;
  document["payload"]["io_address"] = diagnostic.io_address;
  document["payload"]["power_enabled"] = diagnostic.power_enabled;
  document["payload"]["power_released"] = diagnostic.power_released;
  document["payload"]["yaw_feedback"] = diagnostic.yaw_feedback;
  document["payload"]["pitch_feedback"] = diagnostic.pitch_feedback;
  document["payload"]["yaw_raw"] = diagnostic.yaw_raw;
  document["payload"]["pitch_raw"] = diagnostic.pitch_raw;
  document["payload"]["yaw_min"] = diagnostic.yaw_min;
  document["payload"]["yaw_max"] = diagnostic.yaw_max;
  document["payload"]["pitch_min"] = diagnostic.pitch_min;
  document["payload"]["pitch_max"] = diagnostic.pitch_max;
  document["payload"]["id_scan_performed"] = diagnostic.id_scan_performed;
  document["payload"]["detected_id_mask"] = diagnostic.detected_id_mask;
  JsonArray detected = document["payload"]["detected_servos"].to<JsonArray>();
  for (uint8_t id = 0; id <= 8; ++id) {
    if ((diagnostic.detected_id_mask & (1u << id)) == 0) continue;
    JsonObject servo = detected.add<JsonObject>();
    servo["id"] = id;
    servo["position"] = diagnostic.detected_positions[id];
  }
  String output;
  serializeJson(document, output);
  socket_client.sendTXT(output);
}

void handleControl(const uint8_t* payload, size_t length) {
  JsonDocument document;
  const auto error = deserializeJson(document, payload, length);
  if (error) return;
  const String type = document["type"] | "";
  const String request_id = document["request_id"] | "";
  const JsonVariantConst body = document["payload"];

  if (type == "auth.challenge") {
    if (!server_connected) sendAuthenticatedHello(body["nonce"] | "");
  } else if (type == "hello.ack") {
    if (server_connected || pending_server_nonce.isEmpty() ||
        pending_device_nonce.isEmpty()) {
      return;
    }
    const String device_id = WiFi.macAddress();
    const String expected = pairingProof(
        "server", pending_server_nonce, pending_device_nonce, device_id);
    const String supplied = body["server_response"] | "";
    if (!constantTimeEqual(expected, supplied)) {
      face.setState(stackchan::FaceState::error);
      face.setStatus("Server auth failed");
      pending_server_nonce = "";
      pending_device_nonce = "";
      socket_client.disconnect();
      return;
    }
    pending_server_nonce = "";
    pending_device_nonce = "";
    server_connected = true;
    audio.setConnected(true);
    face.setState(stackchan::FaceState::idle);
    face.setStatus("Ready");
  } else if (!server_connected) {
    // No physical control or audio state is accepted before the server proves
    // knowledge of the pairing secret for this connection's two fresh nonces.
    return;
  } else if (type == "session.state") {
    const String state = body["state"] | "idle";
    if (state == "thinking" || state == "listening") clearHeldFace();
    if (state == "idle" || state == "thinking") {
      approval_waiting_until_ms = 0;
    }
    face.setState(stackchan::faceStateFromString(state));
    face.setStatus(state);
    if (state == "idle" && audio.playbackActive()) {
      face.setState(stackchan::FaceState::speaking);
      face.setStatus("Speaking");
    }
    if (state == "listening") {
      lights.set(30, 210, 255, 0.22f, stackchan::LightAnimation::pulse);
    } else if (state == "thinking") {
      lights.set(145, 60, 255, 0.24f, stackchan::LightAnimation::chase);
    } else if (state == "speaking") {
      lights.set(50, 110, 255, 0.18f, stackchan::LightAnimation::pulse);
    } else if (state == "awaiting_approval") {
      face.setState(stackchan::FaceState::listening);
      face.setStatus("Approve or deny");
      lights.set(255, 150, 20, 0.24f, stackchan::LightAnimation::pulse);
    } else if (state == "idle") {
      lights.set(255, 105, 145, 0.08f, stackchan::LightAnimation::solid);
      if (!audio.playbackActive()) applyHeldFace();
    }
  } else if (type == "approval.requested") {
    const float timeout_seconds = constrain(
        body["timeout_seconds"] | 30.0f, 5.0f, 300.0f);
    approval_waiting_until_ms =
        millis() + static_cast<uint32_t>(timeout_seconds * 1000.0f);
    const String challenge = body["challenge"] | "--";
    face.setState(stackchan::FaceState::listening);
    face.setStatus(String("Approve ") + challenge);
    lights.set(255, 150, 20, 0.24f, stackchan::LightAnimation::pulse);
  } else if (type == "face.set") {
    const String state = body["state"] | "idle";
    const String emotion = body["emotion"] | "neutral";
    const float intensity = body["intensity"] | 0.5f;
    face.setState(stackchan::faceStateFromString(state));
    face.setEmotion(emotion, intensity);
    held_face_active = true;
    held_face_state = state;
    held_face_emotion = emotion;
    held_face_intensity = intensity;
    sendFaceResult(state, emotion, intensity, request_id);
  } else if (type == "lights.set") {
    const int red = body["red"] | 0;
    const int green = body["green"] | 0;
    const int blue = body["blue"] | 0;
    const float brightness = body["brightness"] | 0.2f;
    const String animation = body["animation"] | "solid";
    const bool written = lights.set(
        red, green, blue, brightness,
        stackchan::lightAnimationFromString(animation.c_str()));
    sendLightResult(red, green, blue, brightness, animation, written, request_id);
  } else if (type == "motion.set") {
    const JsonVariantConst yaw = body["yaw_deg"];
    const JsonVariantConst pitch = body["pitch_deg"];
    const bool accepted = motion.move(!yaw.isNull(), yaw | 0.0f, !pitch.isNull(),
                                      pitch | 45.0f, body["duration_ms"] | 450);
    if (accepted) active_motion_request_id = request_id;
    sendMotionResult(accepted ? "dispatched" : "rejected", accepted,
                     accepted ? "safe motion accepted" : motion.lastError(), -1, -1,
                     -1, -1, -1, -1, !yaw.isNull(), yaw | 0.0f, !pitch.isNull(),
                     pitch | 45.0f, request_id);
  } else if (type == "motion.diagnose") {
    stackchan::MotionDiagnostic diagnostic;
    motion.diagnose(diagnostic);
    sendMotionDiagnostic(diagnostic, request_id);
  } else if (type == "routine.play") {
    const String routine = body["name"] | "greet";
    // Reject contention before touching visual state. Then prove the first LED
    // frame can be written before motion starts; a dispatch failure rolls the
    // LEDs back off and never changes the face.
    if (motion.active() || active_routine_steps != nullptr) {
      startRoutineMotion(routine, request_id);
      return;
    }
    bool light_written = false;
    if (routine == "celebrate") {
      light_written =
          lights.set(255, 80, 150, 0.3f, stackchan::LightAnimation::rainbow);
    } else if (routine == "curious") {
      light_written =
          lights.set(80, 160, 255, 0.22f, stackchan::LightAnimation::chase);
    } else if (routine == "comfort") {
      light_written =
          lights.set(255, 125, 85, 0.16f, stackchan::LightAnimation::pulse);
    } else if (routine == "dance") {
      light_written =
          lights.set(255, 40, 180, 0.32f, stackchan::LightAnimation::rainbow);
    } else if (routine == "wake_up") {
      light_written =
          lights.set(255, 155, 45, 0.26f, stackchan::LightAnimation::pulse);
    } else if (routine == "focus") {
      light_written =
          lights.set(30, 190, 220, 0.14f, stackchan::LightAnimation::solid);
    } else if (routine == "good_night") {
      light_written =
          lights.set(105, 55, 180, 0.1f, stackchan::LightAnimation::pulse);
    } else {
      light_written =
          lights.set(255, 105, 145, 0.24f, stackchan::LightAnimation::pulse);
    }
    if (!light_written) {
      active_routine_name = routine;
      active_routine_request_id = request_id;
      active_routine_step_count = 0;
      sendRoutineMotionResult("rejected", false,
                              "LED preflight failed; motion was not started");
      active_routine_request_id = "";
      lights.off();
      return;
    }
    if (startRoutineMotion(routine, request_id)) {
      active_routine_light_written = light_written;
      if (routine == "celebrate") {
        face.setState(stackchan::FaceState::happy);
        face.setEmotion("joy", 1.0f);
      } else if (routine == "curious") {
        face.setState(stackchan::FaceState::thinking);
        face.setEmotion("curious", 0.8f);
      } else if (routine == "comfort") {
        face.setState(stackchan::FaceState::happy);
        face.setEmotion("gentle", 0.65f);
      } else if (routine == "dance") {
        face.setState(stackchan::FaceState::happy);
        face.setEmotion("playful", 1.0f);
      } else if (routine == "wake_up") {
        face.setState(stackchan::FaceState::happy);
        face.setEmotion("excited", 0.9f);
      } else if (routine == "focus") {
        face.setState(stackchan::FaceState::thinking);
        face.setEmotion("curious", 0.72f);
      } else if (routine == "good_night") {
        face.setState(stackchan::FaceState::idle);
        face.setEmotion("sleepy", 0.9f);
      } else {
        face.setState(stackchan::FaceState::happy);
        face.setEmotion("joy", 0.85f);
      }
    } else {
      lights.off();
    }
  } else if (type == "camera.capture") {
    if (!validRequestId(request_id)) {
      sendCameraResult(false, "camera request id must be 32 lowercase hex characters",
                       request_id);
    } else if (audio.playbackActive() || motion.active() ||
               active_routine_steps != nullptr) {
      sendCameraResult(false, "camera is busy while playback or motion is active",
                       request_id);
    } else {
      captureAndSendPhoto(body["quality"] | 70, request_id);
    }
  } else if (type == "audio.energy") {
    face.setSpeechEnergy(body["value"] | 0.0f);
    last_energy_ms = millis();
  } else if (type == "playback.configure") {
    audio.setPlaybackStartFrames(body["start_frames"] | 16);
  } else if (type == "playback.duck") {
    const bool enabled = body["enabled"] | false;
    const float gain = body["gain"] | 0.05f;
    audio.setDucked(enabled, gain);
    sendPlaybackDuckState(enabled, audio.playbackDuckGain(), request_id);
  } else if (type == "playback.flush") {
    const uint32_t started_us = micros();
    const bool success = flushAudioWithSensorGuard();
    const uint32_t duration_us = micros() - started_us;
    if (!audio.playbackActive()) reportPlaybackState(false);
    sendPlaybackFlushState(success, duration_us, request_id);
    face.setState(success ? stackchan::FaceState::listening
                          : stackchan::FaceState::error);
    face.setStatus(success ? "Listening" : "Audio flush failed");
  } else if (type == "capture.commit") {
    sendControl("turn.commit");
    face.setState(stackchan::FaceState::thinking);
    face.setStatus("Thinking");
  } else if (type == "error") {
    face.setState(stackchan::FaceState::error);
    face.setStatus("Protocol error");
  }
}

void websocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED: {
      // The TCP/WebSocket transport is open, but device traffic must remain
      // disabled until the nonce challenge is accepted and hello.ack arrives.
      server_connected = false;
      clearHeldFace();
      approval_waiting_until_ms = 0;
      pending_server_nonce = "";
      pending_device_nonce = "";
      reported_playback_active = false;
      audio.setConnected(false);
      face.setState(stackchan::FaceState::idle);
      face.setStatus("Authenticating");
      break;
    }
    case WStype_DISCONNECTED:
      Serial.printf("websocket: disconnected wifi=%d heap=%u\n",
                    static_cast<int>(WiFi.status()), ESP.getFreeHeap());
      server_connected = false;
      approval_waiting_until_ms = 0;
      pending_server_nonce = "";
      pending_device_nonce = "";
      reported_playback_active = false;
      audio.setConnected(false);
      flushAudioWithSensorGuard();
      face.setState(stackchan::FaceState::disconnected);
      face.setStatus("Reconnecting");
      break;
    case WStype_TEXT:
      handleControl(payload, length);
      break;
    case WStype_BIN:
      {
      if (!server_connected) break;
      const bool was_active = audio.playbackActive();
      audio.playFrame(payload, length);
      if (!was_active && audio.playbackActive()) reportPlaybackState(true);
      face.setState(stackchan::FaceState::speaking);
      face.setStatus("Speaking");
      break;
      }
    default:
      break;
  }
}

bool connectWifiFromFactoryNvs() {
  Preferences preferences;
  if (!preferences.begin("wifi", true)) return false;
  const String ssid = preferences.getString("ssid", "");
  const String password = preferences.getString("password", "");
  preferences.end();
  if (ssid.isEmpty()) return false;

  WiFi.mode(WIFI_STA);
  // ESP32-S3 radio coexistence requires modem sleep while BLE is enabled.
  // Disabling it causes an immediate abort in the Wi-Fi driver. The audio
  // stream remains continuous because the WebSocket queue absorbs wake gaps.
  WiFi.setSleep(true);
  WiFi.begin(ssid.c_str(), password.c_str());
  face.setStatus("Connecting Wi-Fi");
  const uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 15000) {
    face.update(millis());
    delay(20);
  }
  return WiFi.status() == WL_CONNECTED;
}

void syncCodexUi(bool force = false);
void updateCodexPose();

void enterCodexMode() {
  face.setCodexMode(true);
  syncCodexUi(true);
  // Entering the mode is visual only. Do not turn a navigation gesture into
  // unsolicited servo motion.
  last_codex_motion_state = codex.agent(codex.selectedAgent()).state();
}

void exitCodexMode() {
  if (codex_mic_pressed) codex.setMicPressed(false);
  codex_mic_pressed = false;
  face.setCodexMode(false);
  lights.set(255, 105, 145, 0.08f, stackchan::LightAnimation::solid);
}

void handleTouch(uint32_t now_ms) {
  (void)now_ms;
  const auto& detail = M5.Touch.getDetail(0);
  if (face.codexMode()) {
    const auto state = codex.agent(codex.selectedAgent()).state();
    const bool mic_region = detail.x >= 240 && detail.y >= 176 &&
                            state != stackchan::CodexAgentState::needs_input;
    if (!codex_mic_pressed && detail.wasPressed() && mic_region) {
      codex_mic_pressed = codex.setMicPressed(true);
      return;
    }
    if (codex_mic_pressed && detail.wasReleased()) {
      codex.setMicPressed(false);
      codex_mic_pressed = false;
      audio.playUiSound(stackchan::UiSoundEffect::mic_release);
      return;
    }
  }
  const bool horizontal_gesture_finished =
      detail.wasFlicked() || detail.wasDragged() || detail.wasReleased();
  if (horizontal_gesture_finished && abs(detail.distanceX()) >= 40 &&
      abs(detail.distanceX()) * 2 > abs(detail.distanceY()) * 3) {
    Serial.printf("codex-ui: swipe dx=%d dy=%d mode=%d\n", detail.distanceX(),
                  detail.distanceY(), face.codexMode());
    if (!face.codexMode() && detail.distanceX() < 0) {
      enterCodexMode();
      return;
    }
    if (face.codexMode() && detail.distanceX() > 0) {
      exitCodexMode();
      return;
    }
  }
  if (!detail.wasClicked()) return;
  if (audio.playbackActive()) {
    // A single tap during playback is intentionally inert. The second tap in
    // M5Unified's bounded click window is the explicit physical interruption.
    if (detail.getClickCount() < 2) return;
    flushAudioWithSensorGuard();
    reportPlaybackState(false);
    sendBargeIn("screen_double_tap");
    face.setState(stackchan::FaceState::listening);
    face.setStatus("Listening");
    return;
  }

  const int x = detail.x;
  const int y = detail.y;
  if (face.codexMode()) {
    if (x < 52 && y < 48) {
      exitCodexMode();
      return;
    }
    if (y >= 42 && y <= 94) {
      const int agent = constrain(x / 50, 0, 5);
      // Paint selection immediately; host activation emits its compatible
      // double press afterward and must not delay physical feedback.
      face.setCodexSelectedAgent(static_cast<uint8_t>(agent));
      audio.playUiSound(codex.connected()
                            ? stackchan::UiSoundEffect::agent_select
                            : stackchan::UiSoundEffect::error,
                        static_cast<uint8_t>(agent));
      codex.selectAgent(static_cast<uint8_t>(agent));
      // Agent navigation updates the screen/lights but never moves the head.
      last_codex_motion_state = codex.agent(agent).state();
      return;
    }
    if (y >= 176) {
      const auto state = codex.agent(codex.selectedAgent()).state();
      if (state == stackchan::CodexAgentState::needs_input) {
        const bool decline = x < 160;
        const bool sent = codex.sendAction(decline ? 2 : 1);  // NG / OK
        audio.playUiSound(sent ? (decline ? stackchan::UiSoundEffect::decline
                                         : stackchan::UiSoundEffect::approve)
                               : stackchan::UiSoundEffect::error);
      } else {
        const int command = constrain(x / 80, 0, 3);
        bool sent = false;
        auto effect = stackchan::UiSoundEffect::error;
        if (command == 0) {
          sent = codex.sendAction(0);  // Fast
          effect = stackchan::UiSoundEffect::fast;
        }
        if (command == 1) {
          sent = codex.sendAction(3);  // Plan
          effect = stackchan::UiSoundEffect::plan;
        }
        if (command == 2) {
          sent = codex.sendAction(4);  // AI / new task
          effect = stackchan::UiSoundEffect::assistant;
        }
        audio.playUiSound(sent ? effect : stackchan::UiSoundEffect::error);
        // The microphone is handled as a true press/release above.
      }
      return;
    }
    return;
  }

  if (server_connected) {
    sendControl("turn.commit");
    face.setState(stackchan::FaceState::thinking);
    face.setStatus("Thinking");
  }
}

void syncCodexUi(bool force) {
  const bool dirty = codex.consumeUiDirty();
  if (!force && !dirty) return;
  face.setCodexConnected(codex.connected());
  face.setCodexSelectedAgent(codex.selectedAgent());
  for (uint8_t index = 0; index < stackchan::CodexBleController::kAgentCount;
       ++index) {
    const auto& agent = codex.agent(index);
    face.setCodexAgentState(index, agent.state(), agent.color);
  }
  if (!face.codexMode()) return;
  const auto selected_state = codex.agent(codex.selectedAgent()).state();
  switch (selected_state) {
    case stackchan::CodexAgentState::working:
      lights.set(48, 79, 254, 0.24f, stackchan::LightAnimation::chase);
      break;
    case stackchan::CodexAgentState::complete:
      lights.set(0, 255, 76, 0.20f, stackchan::LightAnimation::pulse);
      break;
    case stackchan::CodexAgentState::needs_input:
      lights.set(255, 109, 0, 0.25f, stackchan::LightAnimation::pulse);
      break;
    case stackchan::CodexAgentState::error:
      lights.set(255, 0, 51, 0.25f, stackchan::LightAnimation::pulse);
      break;
    case stackchan::CodexAgentState::idle:
      lights.set(255, 255, 255, 0.08f, stackchan::LightAnimation::solid);
      break;
    case stackchan::CodexAgentState::off:
      lights.off();
      break;
  }
}

void updateCodexPose() {
  if (!face.codexMode()) return;
  const auto selected_state = codex.agent(codex.selectedAgent()).state();
  if (selected_state == last_codex_motion_state) return;
  if (audio.playbackActive() || motion.active() || active_routine_steps != nullptr) {
    return;
  }
  // Host state transitions get one restrained pose; touch navigation never
  // enters this path.
  float yaw = 0.0f;
  float pitch = 45.0f;
  if (selected_state == stackchan::CodexAgentState::working) pitch = 39.0f;
  if (selected_state == stackchan::CodexAgentState::complete) pitch = 31.0f;
  if (selected_state == stackchan::CodexAgentState::needs_input) {
    yaw = 12.0f;
    pitch = 36.0f;
  }
  if (selected_state == stackchan::CodexAgentState::error) {
    yaw = -10.0f;
    pitch = 54.0f;
  }
  if (motion.move(true, yaw, true, pitch, 460)) {
    last_codex_motion_state = selected_state;
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(100);
  auto config = M5.config();
  config.output_power = true;
  M5.begin(config);
  M5.Touch.setFlickThresh(24);
  boot_count = incrementPersistentBootCount();
  Serial.printf("boot: persistent_count=%u reset=%s heap=%u\n", boot_count,
                resetReasonName(esp_reset_reason()), ESP.getFreeHeap());
  M5.Display.setBrightness(128);
  face.begin();
  face.setState(stackchan::FaceState::booting);
  face.setStatus("Custom Stack-chan");
  codex.begin();

  if (!connectWifiFromFactoryNvs()) {
    face.setState(stackchan::FaceState::error);
    face.setStatus("Wi-Fi setup needed");
    return;
  }

  audio.begin();
  head_touch.begin();
  lights.begin();
  motion.begin();
  stackchan::MotionDiagnostic boot_motion_diagnostic;
  motion.diagnose(boot_motion_diagnostic);
  socket_client.begin(STACKCHAN_SERVER_HOST, STACKCHAN_SERVER_PORT, STACKCHAN_SERVER_PATH);
  socket_client.onEvent(websocketEvent);
  socket_client.setReconnectInterval(1000);
  socket_client.enableHeartbeat(15000, 3000, 2);
  face.setState(stackchan::FaceState::disconnected);
  face.setStatus("Finding local server");
}

void loop() {
  const uint32_t now_ms = millis();
  M5.update();
  codex.update();
  syncCodexUi();
  updateCodexPose();
  socket_client.loop();
  if (approval_waiting_until_ms != 0 &&
      static_cast<int32_t>(now_ms - approval_waiting_until_ms) >= 0) {
    approval_waiting_until_ms = 0;
    face.setState(stackchan::FaceState::idle);
    face.setStatus("Approval expired");
    lights.set(255, 105, 145, 0.08f, stackchan::LightAnimation::solid);
  }
  handleTouch(now_ms);
  auto head_gesture = head_touch.update(now_ms);
  if (server_connected && now_ms - last_head_sensor_telemetry_ms >= 500) {
    last_head_sensor_telemetry_ms = now_ms;
    JsonDocument telemetry;
    telemetry["type"] = "telemetry";
    telemetry["payload"]["component"] = "head_sensor";
    telemetry["payload"]["present"] = head_touch.present();
    telemetry["payload"]["ready"] = head_touch.ready();
    telemetry["payload"]["read_ok"] = head_touch.lastReadOk();
    telemetry["payload"]["zone"] = head_touch.zone();
    telemetry["payload"]["strength"] = head_touch.strength();
    telemetry["payload"]["raw_output"] = head_touch.rawOutput();
    telemetry["payload"]["poll_count"] = head_touch.pollCount();
    telemetry["payload"]["read_failures"] = head_touch.readFailures();
    String output;
    serializeJson(telemetry, output);
    socket_client.sendTXT(output);
  }
  // The speaker and motors can capacitively disturb the head sensor. Never let
  // either feedback path submit another routine while playback/motion is active
  // or during the short electrical settling tail after playback ends.
  const bool playback_sensor_guard =
      audio.playbackActive() ||
      (last_playback_ended_ms != 0 &&
       now_ms - last_playback_ended_ms < kHeadSensorPlaybackGuardMs);
  const bool motion_sensor_guard =
      motion.active() ||
      (last_motion_ended_ms != 0 &&
       now_ms - last_motion_ended_ms < kHeadSensorMotionGuardMs);
  const uint8_t head_raw_output = head_touch.rawOutput() & 0x3F;
  const bool strong_head_contact =
      stackchan::HeadTouchSensor::strongMultiZoneContact(head_raw_output);
  if (audio.playbackActive() && !motion_sensor_guard &&
      !head_sensor_rearm_pending && strong_head_contact && !head_interrupt_latched) {
    if (head_interrupt_contact_started_ms == 0) {
      head_interrupt_contact_started_ms = now_ms;
    } else if (now_ms - head_interrupt_contact_started_ms >=
               kHeadInterruptHoldMs) {
      head_interrupt_latched = true;
      sendHeadInterrupt(head_raw_output);
      flushAudioWithSensorGuard();
      reportPlaybackState(false);
      face.setState(stackchan::FaceState::listening);
      face.setEmotion("curious", 0.8f);
      face.setStatus("Listening");
      lights.set(70, 170, 255, 0.24f, stackchan::LightAnimation::pulse);
    }
  } else if (!strong_head_contact) {
    head_interrupt_contact_started_ms = 0;
    if (!audio.playbackActive()) head_interrupt_latched = false;
  }
  if (playback_sensor_guard || motion_sensor_guard) {
    // Motor and speaker switching can look like a real capacitive gesture.
    // Discard the complete detector history during those noisy windows so it
    // cannot turn into a delayed touch as soon as the hardware becomes idle.
    head_touch.resetGesture();
    head_gesture = stackchan::HeadGesture::none;
    head_sensor_rearm_pending = true;
    head_sensor_release_samples = 0;
  } else if (head_sensor_rearm_pending) {
    // Do not re-arm on a timer alone. Motor/speaker charge can outlast the
    // nominal guard; require 200 ms of actual all-channel release (00h).
    const bool released =
        head_touch.lastReadOk() && (head_touch.rawOutput() & 0x3F) == 0;
    head_sensor_release_samples = released
                                      ? min<uint8_t>(head_sensor_release_samples + 1,
                                                     kHeadSensorRearmSamples)
                                      : 0;
    head_touch.resetGesture();
    head_gesture = stackchan::HeadGesture::none;
    if (head_sensor_release_samples >= kHeadSensorRearmSamples) {
      head_sensor_rearm_pending = false;
    }
  }
  if (head_gesture != stackchan::HeadGesture::none && !motion.active() &&
      !playback_sensor_guard && !motion_sensor_guard) {
    sendHeadGesture(head_gesture);
    if (head_gesture == stackchan::HeadGesture::touch) {
      face.setState(stackchan::FaceState::thinking);
      face.setEmotion("surprised", 1.0f);
      face.setGaze(0.0f, 0.75f);
      face.setStatus("Bonk?");
      lights.set(255, 80, 145, 0.32f, stackchan::LightAnimation::pulse);
    } else if (head_gesture == stackchan::HeadGesture::hold) {
      face.setState(stackchan::FaceState::happy);
      face.setEmotion("petted", 1.0f);
      face.setStatus("Mmm...");
      lights.set(255, 85, 150, 0.28f, stackchan::LightAnimation::twinkle);
    } else if (head_gesture == stackchan::HeadGesture::swipe_forward ||
               head_gesture == stackchan::HeadGesture::swipe_backward) {
      face.setState(stackchan::FaceState::happy);
      face.setEmotion("playful", 1.0f);
      face.setGaze(head_gesture == stackchan::HeadGesture::swipe_forward ? 0.8f : -0.8f,
                   -0.2f);
      face.setStatus("Wheee!");
      lights.set(70, 170, 255, 0.28f, stackchan::LightAnimation::rainbow);
    } else if (!audio.playbackActive()) {
      face.setState(stackchan::FaceState::idle);
      face.setEmotion("neutral", 0.5f);
      face.setGaze(0.0f, 0.0f);
      face.setStatus("Idle");
    }
  }
  const bool ui_sound_was_active = audio.uiSoundActive();
  if (audio.update() && !ui_sound_was_active) {
    last_playback_ended_ms = now_ms;
    reportPlaybackState(false);
    face.setState(stackchan::FaceState::idle);
    face.setStatus("Idle");
    applyHeldFace();
  }
  if (audio.playbackActive()) {
    face.setSpeechEnergy(audio.playbackEnergy());
    last_energy_ms = now_ms;
  } else if (now_ms - last_energy_ms > 120) {
    face.setSpeechEnergy(0.0f);
  }
  audio.captureFrame();
  if (server_connected && now_ms - last_audio_telemetry_ms >= 1000) {
    last_audio_telemetry_ms = now_ms;
    JsonDocument telemetry;
    telemetry["type"] = "telemetry";
    telemetry["payload"]["component"] = "audio";
    telemetry["payload"]["mode"] =
        audio.duplexReady() ? "full_duplex" : "half_duplex_fallback";
    telemetry["payload"]["microphone_rms"] = audio.microphoneRms();
    telemetry["payload"]["microphone_left_rms"] = audio.microphoneLeftRms();
    telemetry["payload"]["microphone_right_rms"] = audio.microphoneRightRms();
    telemetry["payload"]["microphone_peak"] = audio.microphonePeak();
    telemetry["payload"]["microphone_clipped_samples"] =
        audio.microphoneClippedSamples();
    telemetry["payload"]["microphone_gain_x100"] =
        audio.microphoneGainX100();
    telemetry["payload"]["microphone_codec_gain_db"] =
        audio.microphoneCodecGainDb();
    telemetry["payload"]["playback_active"] = audio.playbackActive();
    telemetry["payload"]["playback_dropped_frames"] =
        audio.droppedPlaybackFrames();
    telemetry["payload"]["playback_queued_frames"] = audio.queuedPlaybackFrames();
    telemetry["payload"]["playback_queue_high_water_frames"] =
        audio.playbackQueueHighWaterFrames();
    telemetry["payload"]["playback_response_high_water_frames"] =
        audio.playbackResponseHighWaterFrames();
    telemetry["payload"]["playback_start_frames"] =
        audio.playbackStartFrames();
    telemetry["payload"]["playback_queue_capacity_frames"] =
        audio.playbackQueueCapacityFrames();
    telemetry["payload"]["playback_starvation_events"] =
        audio.playbackStarvationEvents();
    telemetry["payload"]["face_speaking_mouth_transitions"] =
        face.speakingMouthTransitions();
    telemetry["payload"]["face_speaking_blinks"] = face.speakingBlinks();
    String output;
    serializeJson(telemetry, output);
    socket_client.sendTXT(output);
  }
  lights.update(now_ms);
  motion.update(now_ms);
  stackchan::MotionCompletion motion_completion;
  if (motion.takeCompletion(motion_completion)) {
    last_motion_ended_ms = now_ms;
    if (active_routine_steps != nullptr) {
      sendRoutineMotionResult("step_completed", motion_completion.success,
                              motion_completion.detail,
                              active_routine_step_index, &motion_completion);
      if (!motion_completion.success) {
        sendRoutineMotionResult("completed", false, "routine motion failed");
        active_routine_steps = nullptr;
        active_routine_step_count = 0;
        active_routine_request_id = "";
      } else if (++active_routine_step_index < active_routine_step_count) {
        if (!dispatchRoutineMotionStep()) {
          sendRoutineMotionResult("completed", false, motion.lastError());
          active_routine_steps = nullptr;
          active_routine_step_count = 0;
          active_routine_request_id = "";
        }
      } else {
        sendRoutineMotionResult(
            "completed", active_routine_light_written,
            active_routine_light_written
                ? "all motion steps and the LED frame were verified"
                : "motion completed but the LED frame write failed");
        active_routine_steps = nullptr;
        active_routine_step_count = 0;
        active_routine_request_id = "";
      }
    } else {
      sendMotionResult("completed", motion_completion.success, motion_completion.detail,
                       motion_completion.yaw_raw, motion_completion.pitch_raw,
                       motion_completion.yaw_target_raw,
                       motion_completion.pitch_target_raw,
                       motion_completion.yaw_error_raw,
                       motion_completion.pitch_error_raw, false, 0.0f, false,
                       45.0f, active_motion_request_id);
      active_motion_request_id = "";
    }
  }
  face.update(now_ms);
  delay(1);
}
