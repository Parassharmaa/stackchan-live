# Stack-chan Local

An original, local-first software stack for a bilingual animated Stack-chan. It does not use the factory application or vendor UI.

> Hardware voice interruption is experimental and intentionally deferred; see
> `docs/completion-audit.md` for the exact evidence boundary.

## Architecture

- `server/`: laptop-hosted realtime audio, memory, device tools, and benchmarks.
- `intelligence/`: optional Vercel Eve conversation agent, skills, typed memory/device tools, and an allowlisted MCP seam.
- `firmware/`: custom ESP32-S3 firmware for audio transport, face animation, motion, lights, and device safety.
- `docs/`: hardware notes, protocol, model decisions, and benchmark targets.

Start with the Pixi-first [end-to-end setup guide](docs/setup.md). The proposed
public file boundary and exclusions are recorded in
[the publication audit](docs/publication-audit.md).

The hot path is provider-neutral. The default production target is laptop-local
STT and TTS around an Eve intelligence sidecar backed by GPT-5.6 Luna through
the Mac's Codex login. Eve adds connection-
scoped conversation sessions, instructions, skills, context compaction, typed
memory/device tools, and a narrow future MCP boundary without entering the ESP audio loop. A native
OpenAI Realtime speech-to-speech provider uses the same device session, memory,
tool, interruption, and telemetry interfaces.

## Server quick start

```sh
pixi install
pixi run server
```

Run `pixi run intelligence` in a second terminal before `pixi run server`. The
server fails its health check when Eve is unavailable; it never silently
falls through to a different model. See `docs/eve-intelligence.md` for the
quality gate and tool, skill, MCP, and security boundary.

Eve uses `gpt-5.6-luna` with reasoning disabled for the strict-latency baseline.
It authenticates with the local Codex login rather than an Ollama daemon or a
new API key. STT, TTS, raw audio, and SQLite memory storage stay on the laptop;
the finalized transcript, conversation context, and retrieved memory sent to
the model leave the Mac. Run `codex login` if the Eve sidecar reports missing
credentials.

To compare the native speech-to-speech path, provide the key only to the laptop
process and select the explicit provider. The ESP never receives or stores it:

```sh
OPENAI_API_KEY=... STACKCHAN_PROVIDER=speech_to_speech pixi run server
OPENAI_API_KEY=... pixi run benchmark-realtime
```

Hosted mode uses the GA Realtime WebSocket with `gpt-realtime-2.1`, 24 kHz
PCM16 output, bilingual input transcription, low reasoning effort, native tool
calling, and client-managed conversation truncation on barge-in. It fails at
startup if the key is missing; it never falls through to mock audio.

The default server profile is the real `cascade`: three resident laptop-local
whisper.cpp lanes (fast base English/language detection, small Japanese, and a
confidence-gated large-v3-turbo Japanese fallback), Eve/GPT-5.6 Luna, and
Supertonic services configured through environment variables; see
`server/.env.example`. Mock mode is test-only and must be
selected explicitly, so an ordinary restart cannot silently produce placeholder
audio.

Stack-chan streams the first complete, speakable phrase to laptop-local TTS as
soon as it arrives, then prefetches the remaining phrases while the first audio
plays. There is no canned per-turn acknowledgement; every audible reply comes
from the current turn or from an explicit sensor/routine interaction.

Useful project tasks are `pixi run firmware-build`, `pixi run firmware-upload`,
`pixi run benchmark`, `pixi run benchmark-stt-routing-broad`, `pixi run
benchmark-realtime`, `pixi run e2e-probe`, `pixi run sensor-probe`, and `pixi
run check`. The broad STT task replays every unique captured Japanese physical
turn and reports confidence thresholds, latency, intent preservation, and
regressions. With the physical robot connected and the cascade server
running, `pixi run hil-voice-benchmark` plays bilingual acoustic prompts from
the Mac and writes an interruption/latency/recognition report. The acoustic
tasks temporarily unmute the Mac output and restore its exact prior mute state,
including on failure. `pixi run hil-no-false-barge` separately proves that long
English and Japanese speaker output does not interrupt itself. `pixi run
hil-soak` runs the five-minute alternating bilingual physical soak and includes
ESP boot stability plus cumulative playback-frame-drop counters. For local
acoustic diagnosis, start the server with `STACKCHAN_TRACE_AUDIO=true`; this
stores ignored cleaned-turn WAV files beside the JSONL traces. It is off by
default so ordinary conversations are not recorded.

The established physical five-minute result is 9/9 bilingual interruption
cases, zero false confirmed barges, zero dropped playback frames, and zero
observed WebSocket disconnects. The newer boot-18 grounded soak ran for 97.96
seconds and passed 3/3 alternating English/Japanese cases with every intended
barge confirmed, no unintended barges, no audio drops, and no starvation.
Confirmed physical queue flushes measured below 1 ms in that run; see
`docs/benchmark-results.md` for the full latency and tuning history.

Those interruption results are historical baselines. Boot 31 keeps 6 dB of
codec input headroom while Stack-chan speaks and gives a validated Stop/Wait cue
one bounded, firmware-acknowledged 26 dB playback-duck window so the replacement request is easier to
hear. Cross-language cue confirmation requires a stable repeated cue, and a
cue-anchored raw continuation must contain an actionable request before playback
is flushed; cue-only confirmation must also survive the unsuppressed raw lane.
A physical bilingual long-answer regression passed with zero false barges,
zero drops/starvation, 64/65 mouth transitions, and four blinks per reply. The continuous
laptop fixture still does not repeatably preserve the complete cue plus
replacement request, so the completion audit keeps that end-to-end gate open.
Boot 30 also provides a separate, conservative physical fallback: holding a palm
flat across at least two top-sensor zones for 700 ms stops local playback before
the server round trip, then returns the session to listening. `pixi run
hil-sensor-interrupt` produces the physical acceptance artifact; it requires a
person to perform the hold while the generated reply is audible. Speaking-face
telemetry now proves concurrent mouth motion and blinking.

`pixi run hil-routine-music` speaks a dance request through the physical
microphone path and requires evidence for the generated jingle, verified head
completion, real speaker start/drain, and zero new playback drops.

`pixi run hil-memory` speaks temporary English and Japanese remember/recall
pairs, verifies the stored SQLite row and exact memories passed to the local
LLM, checks the spoken answer, then removes only its benchmark facts.
`pixi run benchmark-long-memory` adds 20 distractor turns, bilingual exact
recall, deduplication, sensitive-write rejection, and cleanup validation.
`pixi run benchmark-adaptive-memory` creates an isolated temporary store and
uses a fresh live Eve session for every turn to prove automatic English and
Japanese profile recall, bounded episode recall, correct user/robot perspective,
sensitive-data rejection, and cleanup without touching the user's memories.
`pixi run hil-profile-memory` extends that proof through the physical acoustic
path: natural English and Japanese preference statements are learned without a
`remember` command, then recalled on a later turn. It deliberately refuses to
run unless both the server and benchmark point at the same isolated, initially
empty `STACKCHAN_MEMORY_PATH`; never point this HIL task at the live user-memory
database.

`pixi run hil-sensor` waits for a real touch, hold, or swipe on the capacitive
top sensor and requires the physical reaction audio to start and drain. The
event records its channel and strength so sensor behavior can be diagnosed
without exposing it as an agent-controlled fake input. Firmware initializes the
three physical pads on Si12T channels 2-4 directly from the official schematic
and verifies the written registers before reporting `head_sensor_ready`.

The provider and local end-to-end speech-model decision is recorded in
`docs/provider-evaluation.md`; the requirement-by-requirement evidence matrix
is `docs/completion-audit.md`.

Supertonic currently uses five diffusion steps with the F5 voice. `pixi run
benchmark-tts-steps` compares 1/2/3/5 steps with bilingual Whisper round-trip
intelligibility; the current profile favors the more audible five-step result,
while one and two are intentionally rejected as too error-prone.

Historical local-model comparisons remain in the benchmark artifacts, but no
Ollama task or process is part of the active runtime.
`pixi run benchmark-intelligence` measures the active Eve sidecar for bilingual
depth, common-sense grounding, retrieved memory, spoken-output shape, and
first-token latency. It also exercises the live tool allowlist, multi-turn
continuity, cancellation recovery, memory create/recall/delete, and
sensitive-memory denial. Explicit requests for an advertised authored tool use
a bounded middleware; mutating/unknown tools are absent from ordinary turns,
and all tools are disabled after a result to prevent duplicate actions.

## Hardware-in-the-loop controls

While the server is running, local-only endpoints expose connected devices and
allow safe control probes:

```text
GET  /v1/devices
GET  /v1/devices/{device_id}
POST /v1/devices/{device_id}/control
GET  /v1/devices/{device_id}/results
```

The control endpoint only accepts the protocol allowlist. Head movement remains
disabled until the firmware successfully reads live servo state and completes
its explicit feedback verification. On the connected unit, yaw ID 1 and pitch
ID 2 are now verified automatically at boot. Five presets coordinate the cute
face, safe head targets, body lights, and optional original music: `greet`,
`celebrate`, `curious`, `comfort`, and `dance`. `capture.commit` and the
read-only `motion.diagnose` probe are loopback-only test controls and are not
available to the conversation agent.

Device pairing uses a fresh server nonce on every WebSocket connection. The
firmware returns `HMAC-SHA256(nonce:device_id)` and does not enable microphone,
telemetry, or playback traffic until the server acknowledges authentication.
The shared secret and saved Wi-Fi credentials are never exposed through the
HTTP API or copied into benchmark artifacts. This authenticates the device and
prevents replay of an old handshake; ordinary LAN audio/control traffic is not
encrypted, so use a trusted network.

The hardware implementation is grounded in the [official StackChan hardware
page](https://docs.m5stack.com/en/StackChan), [power-board
schematic](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/1205/SCH_Power.pdf),
the [touch-board schematic](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/1205/SCH_Touch.pdf),
and the [Si12T datasheet](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/1205/Si12T_Datasheet_EN.pdf). The custom
firmware does not reuse the vendor application or UI.

## Safety

The firmware constrains vertical motion to 5-85 degrees, rate-limits servo
movement, and starts with motor power disabled. It seeds each commanded servo
with its live position before enabling torque and turns motor power back off
after the move. Servo power warm-up is an asynchronous state, so it cannot block
the audio/WebSocket loop. Do not force a powered servo by hand.

## License

The original project code and artwork are released under the [MIT License](LICENSE).
The official M5Stack mechanical assets under `third_party/M5_Hardware/` retain
their upstream MIT copyright and notice.
