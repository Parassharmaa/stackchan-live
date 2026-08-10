#pragma once

// Copy this file to LocalConfig.hpp and use the Mac's LocalHostName from:
//   scutil --get LocalHostName
// The copied file is ignored by Git.
#define STACKCHAN_SERVER_HOST "your-mac.local"
#define STACKCHAN_SERVER_PORT 8765
#define STACKCHAN_SERVER_PATH "/v1/device"
// POSIX timezone string used by the avatar clock. This example is Japan time.
#define STACKCHAN_TIMEZONE "JST-9"
// Keep the Codex task halo static by default. Set to 1 to let the host's
// status effect/speed animate working and needs-input indicators.
#define STACKCHAN_CODEX_INDICATOR_ANIMATION 0
#define STACKCHAN_CODEX_INDICATOR_PULSE_MS 720
