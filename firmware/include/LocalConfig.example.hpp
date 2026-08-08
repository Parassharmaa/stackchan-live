#pragma once

// Copy this file to LocalConfig.hpp and use the Mac's LocalHostName from:
//   scutil --get LocalHostName
// The copied file is ignored by Git.
#define STACKCHAN_SERVER_HOST "your-mac.local"
#define STACKCHAN_SERVER_PORT 8765
#define STACKCHAN_SERVER_PATH "/v1/device"
