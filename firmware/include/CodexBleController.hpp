#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

#include <array>

namespace stackchan {

enum class CodexAgentState : uint8_t {
  off,
  idle,
  working,
  complete,
  needs_input,
  error,
};

struct CodexAgentStatus {
  uint32_t color = 0;
  float brightness = 0.0f;
  uint8_t effect = 0;
  float speed = 0.0f;

  CodexAgentState state() const;
};

class CodexBleController {
 public:
  static constexpr uint8_t kAgentCount = 6;
  static constexpr uint8_t kVendorReportId = 6;
  static constexpr size_t kReportLength = 63;
  static constexpr size_t kChunkLength = 61;

  bool begin();
  void update();

  bool connected() const { return connected_; }
  bool consumeUiDirty();
  const CodexAgentStatus& agent(uint8_t index) const;
  uint8_t selectedAgent() const { return selected_agent_; }
  void selectAgent(uint8_t index);

  bool sendAction(uint8_t index);
  bool setMicPressed(bool pressed);

  // Called from NimBLE callbacks. JSON parsing is deliberately deferred to
  // update(), which runs on the Arduino loop task.
  void onConnected(uint16_t connection_handle);
  void onDisconnected(int reason);
  void onAuthenticated(bool encrypted);
  void onVendorWrite(const uint8_t* data, size_t length);

 private:
  bool sendTap(const char* key);
  bool sendKey(const char* key, bool pressed);
  bool sendFramedJson(const String& json, bool append_crlf);
  void processRpc(const char* json);
  void sendRpcResponse(const char* method, int id);
  void applyAgentStatus(const JsonVariantConst& params);

  std::array<CodexAgentStatus, kAgentCount> agents_{};
  uint8_t selected_agent_ = 0;
  volatile bool connected_ = false;
  volatile bool ui_dirty_ = true;
  String rx_buffer_;
  void* rpc_queue_ = nullptr;
};

const char* codexAgentStateName(CodexAgentState state);

}  // namespace stackchan
