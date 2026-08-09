#include "CodexBleController.hpp"

#include <ArduinoJson.h>
#include <M5Unified.h>
#include <NimBLEDevice.h>
#include <NimBLEHIDDevice.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace stackchan {
namespace {

constexpr char kDeviceName[] = "Stack-chan Codex";
constexpr char kManufacturer[] = "Stack-chan";
constexpr char kModel[] = "Stack-chan Codex";
constexpr char kFirmwareVersion[] = "v1.0";
constexpr uint16_t kVendorId = 0x303A;
constexpr uint16_t kProductId = 0x8360;
constexpr uint16_t kProductVersion = 0x0001;
constexpr uint8_t kChannelJsonRpc = 2;
constexpr size_t kRpcBufferLength = 2048;
// ChatGPT opens an Agent Key conversation after two presses within 350 ms
// unless its optional single-tap preference is enabled. One physical tap on
// Stack-chan should be sufficient, so emit the compatible pair promptly.
constexpr uint16_t kAgentFocusTapGapMs = 55;

// HID descriptors are wire-format declarations. This descriptor exposes the
// standard keyboard, consumer, pointer, and vendor report collections expected
// by the Codex Micro protocol. Protocol behavior was independently implemented
// from the public analysis and MIT-licensed VibeWatch reference noted in
// THIRD_PARTY_NOTICES.md.
uint8_t kReportMap[] = {
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, 0x01, 0x05, 0x07, 0x19, 0xE0,
    0x29, 0xE7, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01, 0x95, 0x06, 0x75, 0x08, 0x15, 0x00,
    0x25, 0xA4, 0x05, 0x07, 0x19, 0x00, 0x29, 0xA4, 0x81, 0x00, 0xC0,
    0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x02, 0x75, 0x10, 0x95, 0x01,
    0x15, 0x00, 0x26, 0xFF, 0x07, 0x19, 0x00, 0x2A, 0xFF, 0x07, 0x81, 0x00,
    0xC0,
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x85, 0x03, 0x09, 0x01, 0xA1, 0x00,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x05, 0x15, 0x00, 0x25, 0x01, 0x95, 0x05,
    0x75, 0x01, 0x81, 0x02, 0x95, 0x01, 0x75, 0x03, 0x81, 0x01, 0x05, 0x01,
    0x09, 0x30, 0x09, 0x31, 0x15, 0x81, 0x25, 0x7F, 0x95, 0x02, 0x75, 0x08,
    0x81, 0x06, 0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x95, 0x01, 0x75, 0x08,
    0x81, 0x06, 0x05, 0x0C, 0x0A, 0x38, 0x02, 0x15, 0x81, 0x25, 0x7F, 0x95,
    0x01, 0x75, 0x08, 0x81, 0x06, 0xC0, 0xC0,
    0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01, 0x85, 0x06, 0x09, 0x02, 0x15,
    0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x3F, 0x81, 0x02, 0x09, 0x03,
    0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x3F, 0x91, 0x02, 0x09,
    0x04, 0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08, 0x95, 0x3F, 0xB1, 0x02,
    0xC0,
};

CodexBleController* g_controller = nullptr;
NimBLEServer* g_server = nullptr;
NimBLEHIDDevice* g_hid = nullptr;
NimBLECharacteristic* g_vendor_input = nullptr;

class OutputCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* characteristic, NimBLEConnInfo&) override {
    if (g_controller == nullptr) return;
    const NimBLEAttValue value = characteristic->getValue();
    g_controller->onVendorWrite(value.data(), value.size());
  }
};

class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* server, NimBLEConnInfo& connection) override {
    if (g_controller == nullptr) return;
    server->updateConnParams(connection.getConnHandle(), 12, 24, 0, 180);
    g_controller->onConnected(connection.getConnHandle());
  }

  void onDisconnect(NimBLEServer*, NimBLEConnInfo&, int reason) override {
    if (g_controller != nullptr) g_controller->onDisconnected(reason);
    NimBLEDevice::startAdvertising();
  }

  void onAuthenticationComplete(NimBLEConnInfo& connection) override {
    if (g_controller != nullptr) {
      g_controller->onAuthenticated(connection.isEncrypted());
    }
    if (!connection.isEncrypted() && g_server != nullptr) {
      g_server->disconnect(connection.getConnHandle());
    }
  }
};

OutputCallbacks g_output_callbacks;
ServerCallbacks g_server_callbacks;

void addDeviceInfoCharacteristic(uint16_t uuid, const char* value) {
  auto* characteristic =
      g_hid->getDeviceInfoService()->createCharacteristic(uuid, NIMBLE_PROPERTY::READ);
  characteristic->setValue(value);
}

}  // namespace

CodexAgentState CodexAgentStatus::state() const {
  if (color == 0 || brightness <= 0.001f) return CodexAgentState::off;
  if (color == 0x304FFE) return CodexAgentState::working;
  if (color == 0x00FF4C) return CodexAgentState::complete;
  if (color == 0xFF6D00) return CodexAgentState::needs_input;
  if (color == 0xFF0033) return CodexAgentState::error;
  return CodexAgentState::idle;
}

const char* codexAgentStateName(CodexAgentState state) {
  switch (state) {
    case CodexAgentState::off: return "Unassigned";
    case CodexAgentState::idle: return "Ready";
    case CodexAgentState::working: return "Working";
    case CodexAgentState::complete: return "Complete";
    case CodexAgentState::needs_input: return "Needs input";
    case CodexAgentState::error: return "Error";
  }
  return "Unknown";
}

bool CodexBleController::begin() {
  if (g_controller != nullptr) return false;
  g_controller = this;
  rpc_queue_ = xQueueCreate(8, sizeof(char*));
  if (rpc_queue_ == nullptr) return false;

  NimBLEDevice::init(kDeviceName);
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);
  NimBLEDevice::setSecurityAuth(true, false, true);
  g_server = NimBLEDevice::createServer();
  g_server->setCallbacks(&g_server_callbacks);
  g_hid = new NimBLEHIDDevice(g_server);
  g_hid->setManufacturer(kManufacturer);
  g_hid->setPnp(0x01, kVendorId, kProductId, kProductVersion);
  g_hid->setHidInfo(0x00, 0x01);
  g_hid->setReportMap(kReportMap, sizeof(kReportMap));

  char serial[17];
  snprintf(serial, sizeof(serial), "%016llX", ESP.getEfuseMac());
  addDeviceInfoCharacteristic(0x2A24, kModel);
  addDeviceInfoCharacteristic(0x2A25, serial);
  addDeviceInfoCharacteristic(0x2A26, kFirmwareVersion);

  const uint8_t keyboard_idle[8] = {};
  const uint8_t consumer_idle[2] = {};
  const uint8_t pointer_idle[5] = {};
  const uint8_t vendor_idle[kReportLength] = {};
  g_hid->getInputReport(1)->setValue(keyboard_idle, sizeof(keyboard_idle));
  g_hid->getInputReport(2)->setValue(consumer_idle, sizeof(consumer_idle));
  g_hid->getInputReport(3)->setValue(pointer_idle, sizeof(pointer_idle));
  g_vendor_input = g_hid->getInputReport(kVendorReportId);
  g_vendor_input->setValue(vendor_idle, sizeof(vendor_idle));
  g_hid->getOutputReport(kVendorReportId)->setCallbacks(&g_output_callbacks);
  g_hid->getFeatureReport(kVendorReportId);

  if (!g_server->start()) return false;
  auto* advertising = NimBLEDevice::getAdvertising();
  advertising->setName(kDeviceName);
  advertising->setAppearance(HID_KEYBOARD);
  advertising->addServiceUUID(g_hid->getHidService()->getUUID());
  advertising->enableScanResponse(true);
  const bool started = advertising->start();
  Serial.printf("codex-ble: advertising=%d name=%s\n", started, kDeviceName);
  return started;
}

void CodexBleController::update() {
  if (rpc_queue_ == nullptr) return;
  char* message = nullptr;
  while (xQueueReceive(static_cast<QueueHandle_t>(rpc_queue_), &message, 0) == pdTRUE) {
    if (message != nullptr) {
      processRpc(message);
      free(message);
    }
  }
}

bool CodexBleController::consumeUiDirty() {
  const bool dirty = ui_dirty_;
  ui_dirty_ = false;
  return dirty;
}

const CodexAgentStatus& CodexBleController::agent(uint8_t index) const {
  return agents_[index < kAgentCount ? index : 0];
}

void CodexBleController::selectAgent(uint8_t index) {
  if (index >= kAgentCount) return;
  selected_agent_ = index;
  ui_dirty_ = true;
  char key[5];
  snprintf(key, sizeof(key), "AG%02u", index);
  const bool first = sendTap(key);
  delay(kAgentFocusTapGapMs);
  const bool second = sendTap(key);
  Serial.printf("codex-ble: agent-focus index=%u first=%d second=%d\n", index,
                first, second);
}

bool CodexBleController::sendAction(uint8_t index) {
  char key[6];
  snprintf(key, sizeof(key), "ACT%02u", index);
  return sendTap(key);
}

bool CodexBleController::sendTap(const char* key) {
  const bool down = sendKey(key, true);
  delay(10);
  return sendKey(key, false) && down;
}

bool CodexBleController::setMicPressed(bool pressed) {
  const bool sent_10 = sendKey("ACT10", pressed);
  delay(12);
  const bool sent_11 = sendKey("ACT11", pressed);
  return sent_10 && sent_11;
}

bool CodexBleController::sendKey(const char* key, bool pressed) {
  if (!connected_ || g_vendor_input == nullptr) return false;
  uint8_t report[kReportLength] = {};
  report[0] = kChannelJsonRpc;
  const int written = snprintf(
      reinterpret_cast<char*>(&report[2]), kChunkLength,
      "{\"m\":\"v.oai.hid\",\"p\":{\"k\":\"%s\",\"act\":%u}}\r\n",
      key, pressed ? 1U : 0U);
  if (written < 0 || written >= static_cast<int>(kChunkLength)) return false;
  report[1] = static_cast<uint8_t>(written);
  g_vendor_input->setValue(report, sizeof(report));
  return g_vendor_input->notify();
}

bool CodexBleController::sendFramedJson(const String& json, bool append_crlf) {
  if (!connected_ || g_vendor_input == nullptr) return false;
  String payload = json;
  if (append_crlf) payload += "\r\n";
  for (size_t offset = 0; offset < payload.length(); offset += kChunkLength) {
    const size_t count = std::min(kChunkLength, payload.length() - offset);
    uint8_t report[kReportLength] = {};
    report[0] = kChannelJsonRpc;
    report[1] = static_cast<uint8_t>(count);
    memcpy(&report[2], payload.c_str() + offset, count);
    g_vendor_input->setValue(report, sizeof(report));
    if (!g_vendor_input->notify()) return false;
    delay(8);
  }
  return true;
}

void CodexBleController::onConnected(uint16_t connection_handle) {
  connected_ = true;
  ui_dirty_ = true;
  Serial.printf("codex-ble: connected handle=%u\n", connection_handle);
}

void CodexBleController::onDisconnected(int reason) {
  connected_ = false;
  ui_dirty_ = true;
  Serial.printf("codex-ble: disconnected reason=%d\n", reason);
}

void CodexBleController::onAuthenticated(bool encrypted) {
  Serial.printf("codex-ble: authenticated encrypted=%d\n", encrypted);
}

void CodexBleController::onVendorWrite(const uint8_t* data, size_t length) {
  if (data == nullptr || length < 2 || data[0] != kChannelJsonRpc) return;
  const size_t chunk_length = data[1];
  if (chunk_length > kChunkLength || chunk_length > length - 2 ||
      rx_buffer_.length() + chunk_length > kRpcBufferLength) {
    rx_buffer_ = "";
    return;
  }
  for (size_t index = 0; index < chunk_length; ++index) {
    rx_buffer_ += static_cast<char>(data[index + 2]);
  }
  JsonDocument probe;
  const DeserializationError error = deserializeJson(probe, rx_buffer_);
  if (error == DeserializationError::IncompleteInput) return;
  if (error) {
    rx_buffer_ = "";
    return;
  }
  char* message = static_cast<char*>(malloc(rx_buffer_.length() + 1));
  if (message != nullptr) {
    memcpy(message, rx_buffer_.c_str(), rx_buffer_.length() + 1);
    if (xQueueSend(static_cast<QueueHandle_t>(rpc_queue_), &message, 0) != pdTRUE) {
      free(message);
    }
  }
  rx_buffer_ = "";
}

void CodexBleController::processRpc(const char* json) {
  JsonDocument request;
  if (deserializeJson(request, json)) return;
  const char* method = request["method"] | request["m"] | "";
  const int id = request["id"] | request["i"] | -1;
  JsonVariantConst params = request["params"];
  if (params.isNull()) params = request["p"];
  Serial.printf("codex-ble: rpc method=%s id=%d\n", method, id);
  if (strcmp(method, "v.oai.thstatus") == 0) applyAgentStatus(params);
  if (id >= 0 && method[0] != '\0') sendRpcResponse(method, id);
}

void CodexBleController::applyAgentStatus(const JsonVariantConst& params) {
  if (!params.is<JsonArrayConst>()) return;
  for (JsonObjectConst item : params.as<JsonArrayConst>()) {
    const int id = item["id"] | -1;
    if (id < 0 || id >= kAgentCount) continue;
    auto& status = agents_[id];
    status.color = item["c"] | 0U;
    status.brightness = item["b"] | 0.0f;
    status.effect = item["e"] | 0;
    status.speed = item["s"] | 0.0f;
  }
  ui_dirty_ = true;
  Serial.printf("codex-ble: agent-status selected=%u state=%s\n", selected_agent_,
                codexAgentStateName(agents_[selected_agent_].state()));
}

void CodexBleController::sendRpcResponse(const char* method, int id) {
  JsonDocument response;
  response["id"] = id;
  response["method"] = method;
  if (strcmp(method, "device.status") == 0) {
    JsonObject result = response["result"].to<JsonObject>();
    result["version"] = kFirmwareVersion;
    result["profile_index"] = 0;
    result["layer_index"] = 1;
    result["battery"] = M5.Power.getBatteryLevel();
    // M5Unified returns a charging-state enum here. Assign an actual bool:
    // the Work Louder SDK rejects a numeric 0/1 and aborts device setup.
    result["is_charging"] =
        M5.Power.isCharging() == m5::Power_Class::is_charging;
  } else if (strcmp(method, "sys.version") == 0) {
    response["result"]["version"] = kFirmwareVersion;
  } else {
    response["result"]["ok"] = 1;
  }
  String json;
  serializeJson(response, json);
  sendFramedJson(json, true);
}

}  // namespace stackchan
