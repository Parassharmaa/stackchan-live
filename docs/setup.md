# End-to-end setup

This guide installs the laptop services, builds the original face assets, pairs
the custom firmware with the server, and flashes an M5Stack StackChan K151 while
preserving the Wi-Fi credentials already stored in its factory NVS partition.

## 1. Requirements

- Apple Silicon Mac running macOS
- M5Stack StackChan K151 with CoreS3 and a data-capable USB-C cable
- Stack-chan and the Mac on the same trusted Wi-Fi network
- [Pixi](https://pixi.sh/latest/installation/)
- [Homebrew](https://brew.sh/) and `whisper-cpp`
- Codex CLI authenticated with the account that will run Eve

Install the two host prerequisites that are not distributed by this project:

```sh
curl -fsSL https://pixi.sh/install.sh | sh
brew install whisper-cpp
codex login
```

The active pipeline does not use Docker or Ollama.

## 2. Install the project and local speech models

From the repository root:

```sh
pixi install
pixi run intelligence-install
pixi run download-models
```

`download-models` installs the three pinned whisper.cpp files into the ignored
`artifacts/models/` directory and verifies their SHA-256 checksums. Supertonic,
FFmpeg, Python, Node.js, PlatformIO, and the test tools come from the Pixi lock.

Confirm that Homebrew exposed both whisper.cpp programs:

```sh
command -v whisper-cli
command -v whisper-server
```

If Homebrew uses a nonstandard prefix, set `STACKCHAN_WHISPER_CLI` and
`STACKCHAN_WHISPER_SERVER` in `server/.env` to their absolute paths.

## 3. Create private local configuration

```sh
cp server/.env.example server/.env
cp firmware/include/LocalConfig.example.hpp firmware/include/LocalConfig.hpp
pixi run provision-device
```

`provision-device` creates one random pairing token and writes it only to:

- `secrets/device-token.txt`
- `firmware/include/DeviceSecret.hpp`
- `server/.env`

All three destinations are ignored by Git. The command never prints the token.

Find the Mac's Bonjour name:

```sh
scutil --get LocalHostName
```

Edit `firmware/include/LocalConfig.hpp` so `STACKCHAN_SERVER_HOST` is that value
with `.local` appended. Leave port `8765` and path `/v1/device` unchanged unless
the server configuration is changed too. Allow incoming connections for the
Python process if the macOS firewall prompts.

## 4. Build and flash without erasing saved Wi-Fi

Connect Stack-chan over USB, then run:

```sh
pixi run firmware-build
pixi run firmware-upload
```

The firmware reads `ssid` and `password` from the existing read-only `wifi` NVS
namespace. Its own boot metadata uses a separate `stackchan-meta` namespace.
The normal PlatformIO upload updates program images and leaves the NVS data
partition intact.

Do not run an erase-flash command, change the partition layout, or manually
write over the NVS partition if the saved Wi-Fi must be preserved. A full-flash
backup, if you choose to keep one, belongs outside this repository because it
can contain network credentials.

## 5. Start Stack-chan

```sh
pixi run start
```

This one command starts Eve, the resident Whisper and Supertonic services,
SQLite memory, and the authenticated robot WebSocket server. It prints a short
readiness summary and waits for Stack-chan to reconnect. Press `Ctrl-C` to stop
every service it started; detailed logs are under `artifacts/logs/`.

Raw microphone audio, speech models, TTS, and SQLite memory stay on the Mac.
Final transcripts, selected memory/context, and tool schemas are sent to the
configured Eve model.

## 6. Validate before normal use

Run the deterministic suite first:

```sh
pixi run check
```

With the physical robot connected to the running services, the focused current
acceptance checks are:

```sh
pixi run hil-recent-regressions
pixi run hil-no-false-barge
pixi run hil-sensor
pixi run hil-routine-music
```

The HIL tasks can move the servos, illuminate body LEDs, and play audio. Keep the
robot on a clear surface and do not force a powered servo by hand. Vertical
motion is constrained to the hardware-safe 5-85 degree range.

Repeatable far-field voice interruption is still experimental and has been
deferred. Do not use `hil-voice-benchmark` as an unattended pass/fail gate yet.

## Troubleshooting

- **Face says Reconnecting:** start both laptop services, verify the Bonjour
  host in `LocalConfig.hpp`, and confirm both devices are on the same network.
- **Connected but authentication fails:** rerun `pixi run provision-device`,
  rebuild/upload the firmware, and restart the server so both sides use the same
  locally stored token.
- **Health reports Whisper missing:** install `whisper-cpp`, verify the two
  binaries above, then run `pixi run download-models` again.
- **Idle but no speech:** inspect `/health`, then the ignored logs under
  `artifacts/logs/`; verify Supertonic and all three Whisper lanes are ready.
- **Wi-Fi no longer connects after an erase:** restore credentials outside this
  project. The custom firmware intentionally has no API that reveals them.

See [architecture.md](architecture.md), [protocol.md](protocol.md), and
[completion-audit.md](completion-audit.md) for design and evidence details.
