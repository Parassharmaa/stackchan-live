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

using stackchan::ScreenTapRoute;

int main() {
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
