# stackchan-live

An original, MIT-licensed firmware and Mac runtime for real-time English and
Japanese conversation with Stack-chan. It does not use the factory application
or vendor UI.

The project provides:

- custom ESP32-S3 firmware with an animated face, head motion, lights, audio,
  top-touch input, and privacy-visible camera stills for photo and direct
  “look at me” requests;
- laptop-local Whisper STT, Supertonic TTS, audio scheduling, SQLite memory,
  and opt-in proactive schedules;
- Vercel Eve intelligence with bilingual conversation, skills, and typed tools;
- pluggable cascade and OpenAI Realtime speech-to-speech providers;
- eight coordinated expression routines, six original music styles, and
  reproducible software and hardware-in-the-loop benchmarks;
- bilingual scheduled check-ins and explicitly authorized one-still
  surroundings checks with quiet hours and pause/delete controls.

> Physical voice interruption remains experimental. Current evidence and known
> limits are tracked in [the completion audit](docs/completion-audit.md).

## Agent-assisted setup

Paste this into Codex, Claude, or another coding agent:

> Set up this stackchan-live repository end to end by following `docs/setup.md`.
> Use Pixi, preserve the Stack-chan Wi-Fi/NVS, never print secrets, ask before
> flashing connected hardware, then run `pixi run start` and verify service
> health and the device connection.

## Architecture

```mermaid
flowchart LR
    U(["You<br/>EN / 日本語"]) <-->|"voice"| S["Stack-chan<br/>custom ESP32-S3 firmware"]
    S <-->|"PCM audio + control"| M["Mac · local<br/>Whisper · Supertonic · SQLite"]
    M <-->|"selected context + reply"| A["Eve + GPT-5.6 Luna<br/>conversation · skills · tools"]
```

## Start

Complete the [first-time setup](docs/setup.md), then start the whole laptop
runtime with one command:

```sh
pixi run start
```

This starts Eve, Whisper, Supertonic, memory, and the realtime device server.
Press `Ctrl-C` to stop everything it started. Runtime logs are written under
`artifacts/logs/`.

## Firmware

```sh
pixi run firmware-build
pixi run firmware-upload
```

Normal uploads preserve Wi-Fi credentials stored in the device's NVS. Do not
erase the flash if those credentials must be retained.

## Verify

```sh
pixi run check
```

See the [setup guide](docs/setup.md), [benchmark results](docs/benchmark-results.md),
[Eve integration](docs/eve-intelligence.md), and [protocol](docs/protocol.md) for
the detailed operator and development references.

## Safety

Head motion is bounded and rate-limited. Do not force a powered servo by hand.

## License

Project code and original artwork are released under the [MIT License](LICENSE).
The official M5Stack mechanical assets in `third_party/M5_Hardware/` retain
their upstream license and notice.
