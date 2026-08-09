#pragma once

#include <cstdint>

namespace stackchan {

enum class CodexVoiceState : uint8_t {
  idle,
  recording,
  processing,
  completed,
};

enum class CodexAction : uint8_t {
  fast = 6,
  approve = 7,
  decline = 8,
  continue_in_new_chat = 9,
  submit = 12,
};

struct CodexVoiceSignal {
  bool recognized;
  CodexVoiceState state;
};

constexpr uint32_t kCodexSubmitFallbackMs = 6000;
constexpr uint8_t kCodexArchiveModifiers = 0x0A;  // left Shift + left GUI
constexpr uint8_t kCodexArchiveKey = 0x04;        // HID usage A
constexpr uint8_t kCodexNewChatModifiers = 0x08;  // left GUI
constexpr uint8_t kCodexNewChatKey = 0x11;        // HID usage N
constexpr uint8_t codexActionIndex(CodexAction action) {
  return static_cast<uint8_t>(action);
}

constexpr CodexVoiceSignal decodeCodexVoiceLighting(uint8_t effect,
                                                    uint32_t color) {
  return effect == 2 && color == 0x2E8B57
             ? CodexVoiceSignal{true, CodexVoiceState::recording}
         : effect == 2 && color == 0xFFFFFF
             ? CodexVoiceSignal{true, CodexVoiceState::processing}
         : effect == 1 && color == 0xFFFFFF
             ? CodexVoiceSignal{true, CodexVoiceState::completed}
             : CodexVoiceSignal{false, CodexVoiceState::idle};
}

constexpr bool shouldSubmitCodexDictation(CodexVoiceState host_state,
                                          uint32_t elapsed_ms) {
  return (host_state == CodexVoiceState::completed && elapsed_ms >= 100) ||
         elapsed_ms >= kCodexSubmitFallbackMs;
}

constexpr bool shouldMoveCodexHead(bool codex_mode, uint8_t selected_state,
                                   uint8_t last_motion_state,
                                   bool hardware_busy) {
  return codex_mode && selected_state != last_motion_state && !hardware_busy;
}

}  // namespace stackchan
