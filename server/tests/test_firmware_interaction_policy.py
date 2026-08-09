from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_firmware_interaction_policy_executes_on_host(tmp_path: Path) -> None:
    """Compile and execute the same pure policy functions used by the ESP build."""
    source = tmp_path / "interaction_policy_test.cpp"
    binary = tmp_path / "interaction_policy_test"
    source.write_text(
        r'''
#include <cassert>
#include "InteractionPolicy.hpp"
#include "CodexInteractionPolicy.hpp"

using stackchan::ScreenTapRoute;

int main() {
  // CoreS3 controls are recognized from a low-travel release instead of the
  // stricter M5Unified click classifier. Drags and swipes remain excluded.
  assert(stackchan::isCodexControlRelease(true, true, 0, 0));
  assert(stackchan::isCodexControlRelease(true, true, 24, -24));
  assert(!stackchan::isCodexControlRelease(true, false, 0, 0));
  assert(!stackchan::isCodexControlRelease(false, true, 0, 0));
  assert(!stackchan::isCodexControlRelease(true, true, 25, 0));
  assert(!stackchan::isCodexControlRelease(true, true, 0, -25));

  // A Codex button always remains a Codex button, even while speech is active.
  assert(stackchan::routeScreenTap(true, true, 1) ==
         ScreenTapRoute::codex_control);
  assert(stackchan::routeScreenTap(true, false, 1) ==
         ScreenTapRoute::codex_control);

  // The ordinary face keeps the intentional double-tap interruption contract.
  assert(stackchan::routeScreenTap(false, true, 1) == ScreenTapRoute::none);
  assert(stackchan::routeScreenTap(false, true, 2) ==
         ScreenTapRoute::interrupt_playback);
  assert(stackchan::routeScreenTap(false, false, 1) ==
         ScreenTapRoute::conversation);

  // Every non-empty UI-tone tail is padded to one complete 20 ms / 480-sample
  // DMA packet. Empty input must not manufacture a frame.
  assert(stackchan::uiSoundPcmFrameLength(480, 480) == 480);
  assert(stackchan::uiSoundPcmFrameLength(1, 480) == 480);
  assert(stackchan::uiSoundPcmFrameLength(119, 480) == 480);
  assert(stackchan::uiSoundPcmFrameLength(0, 480) == 0);

  // Generated audio is preserved and every unused tail sample becomes silence.
  int16_t frame[8] = {11, 22, 33, 44, 55, 66, 77, 88};
  stackchan::zeroPadUiSoundFrame(frame, 3, 8);
  assert(frame[0] == 11 && frame[1] == 22 && frame[2] == 33);
  for (int index = 3; index < 8; ++index) assert(frame[index] == 0);

  // Current ChatGPT defaults: Fast, approval pair, New Chat, and Send.
  using stackchan::CodexAction;
  assert(stackchan::codexActionIndex(CodexAction::fast) == 6);
  assert(stackchan::codexActionIndex(CodexAction::approve) == 7);
  assert(stackchan::codexActionIndex(CodexAction::decline) == 8);
  assert(stackchan::codexActionIndex(CodexAction::continue_in_new_chat) == 9);
  assert(stackchan::codexActionIndex(CodexAction::submit) == 12);
  assert(stackchan::kCodexArchiveModifiers == 0x0A);
  assert(stackchan::kCodexArchiveKey == 0x04);
  assert(stackchan::kCodexNewChatModifiers == 0x08);
  assert(stackchan::kCodexNewChatKey == 0x11);
  // Voice lighting is the completion handshake for reliable auto-submit.
  auto recording = stackchan::decodeCodexVoiceLighting(2, 0x2E8B57);
  auto processing = stackchan::decodeCodexVoiceLighting(2, 0xFFFFFF);
  auto completed = stackchan::decodeCodexVoiceLighting(1, 0xFFFFFF);
  auto unrelated = stackchan::decodeCodexVoiceLighting(2, 0x304FFE);
  assert(recording.recognized && recording.state == stackchan::CodexVoiceState::recording);
  assert(processing.recognized && processing.state == stackchan::CodexVoiceState::processing);
  assert(completed.recognized && completed.state == stackchan::CodexVoiceState::completed);
  assert(!unrelated.recognized);
  assert(!stackchan::shouldSubmitCodexDictation(
      stackchan::CodexVoiceState::processing, 5999));
  assert(stackchan::shouldSubmitCodexDictation(
      stackchan::CodexVoiceState::completed, 100));
  assert(stackchan::shouldSubmitCodexDictation(
      stackchan::CodexVoiceState::processing, 6000));

  // Selecting another slot copies that slot's state into last_motion_state;
  // equal states must never schedule a servo move.
  assert(!stackchan::shouldMoveCodexHead(true, 2, 2, false));
  assert(stackchan::shouldMoveCodexHead(true, 2, 1, false));
  assert(!stackchan::shouldMoveCodexHead(true, 2, 1, true));
  assert(!stackchan::shouldMoveCodexHead(false, 2, 1, false));
  return 0;
}
'''.strip()
        + "\n"
    )
    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(ROOT / "firmware/include"),
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
