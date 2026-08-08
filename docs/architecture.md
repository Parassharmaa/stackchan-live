# Architecture

## Product boundary

The ESP32-S3 is a realtime face, motion, light, sensor, and audio endpoint. The Mac owns acoustic processing, speech inference, agent reasoning, memory, tools, observability, and model routing.

This keeps secrets and large models off the robot, makes model upgrades independent of firmware, and leaves the device responsive when the agent is busy.

## Audio path

1. Stack-chan captures 16 kHz mono PCM in 20 ms frames.
2. Frames are sent over a persistent LAN WebSocket with sequence number and capture timestamp.
3. The Mac runs WebRTC acoustic echo cancellation and noise suppression in
   10 ms frames, using a 16 kHz playback reference resampled from outgoing TTS,
   then applies VAD and streaming STT or a speech-to-speech provider. The local
   cascade first decodes with base: English returns immediately, while Japanese
   is decoded by resident small. Results at or above `-0.18` average log
   probability return immediately; lower-confidence turns run resident
   large-v3-turbo and retain whichever transcript has the higher confidence.
   Sequential routing avoids Metal GPU contention and keeps English fast. All
   whisper.cpp servers disable the redundant language-probability sweep while
   preserving per-segment confidence. A short natural Japanese prior-context phrase
   disambiguates recurring double-talk vocabulary without the hallucinations
   observed from a long command list.
4. Agent output is synthesized as streaming 24 kHz mono PCM.
5. The Mac sends PCM with provider-aware prefill and then paces it at playback
   rate, while the device absorbs network jitter in a bounded buffer. The local
   cascade starts at 40 frames (800 ms) so local Whisper work cannot starve the
   speaker; native streaming speech-to-speech starts at 16 frames (320 ms).
6. An 80 ms near-end candidate preserves 200 ms of onset audio. The verifier
   decodes WebRTC-AEC audio first and falls back to a bounded render-projected
   stream, recording the source and comparing forced English/Japanese results.
   Only utterance-initial Stop/Wait grammar can open one bounded 26 dB playback-
   duck listening window. Firmware acknowledges the applied gain with the
   correlated request ID, while the server pauses new render frames so replacement
   requests can anchor to that validated cue. Cross-language cue-only confirmation
   requires a stable repeated cue,
   and any cue-only flush also requires non-render corroboration from the raw
   microphone lane. Incomplete fragments such as a bare `I need` cannot count
   as a replacement request.
   Render-text matches, negated narration, quiet cross-language hallucinations,
   and unstable language switches resume playback instead of flushing it. A
   confirmed interruption cancels generation, flushes the ESP queue before
   producer cleanup, commits the exact semantic probe window that proved the
   request plus only its later suffix, and uses a 900 ms silence endpoint.
   During commanded servo motion, motor-aware VAD requires a stronger clean
   near-field signal. Serialized WebSocket writes prevent cancellation from
   tearing down the transport. The latest hardware evidence proves cue-triggered
   flushes, but complete replacement recognition remains an open physical gate.

After final STT, the first complete speakable phrase starts laptop-local TTS
while Eve continues generating and the server prefetches later phrases. The
device receives one continuous observable PCM stream without a canned
acknowledgement. If synthesis arrives after a gap, the server rebases its pacing
clock; it never bursts overdue frames to catch up and overflow the ESP jitter
queue.

## Agent path

The default cascade sends each finalized
turn to a connection-scoped Eve session. Eve supplies companion instructions,
conversation history, context compaction, load-on-demand skills, and typed
memory tools, then streams GPT-5.6 Luna through the Codex subscription Responses
transport back into the
existing TTS path. Each physical device connection receives its own session and
interruption propagates to Eve's turn-cancellation endpoint. Disconnect retires
that session; only explicit SQLite memory persists across device reconnects.
The durable store supports typed remember, recall, list, and forget operations,
deduplicates equivalent facts, and rejects recognized credential, payment, and
health content before persistence. The long-session gate inserts 20 distractor
turns and then verifies exact English and Japanese recall plus cleanup. A
deterministic post-turn capture boundary may also retain direct stable profile
facts and bounded non-sensitive episodes; cancelled turns are never captured.

Latency-sensitive device tools remain in Python. They emit allowlisted protocol
commands instead of directly touching transport state. Each invocation receives
a unique request ID. The server waits for the matching terminal firmware result
before grounding Eve's context; a dispatch acknowledgement can never be
presented as successful physical execution. This hybrid boundary prevents a
language model or a future MCP server from bypassing motion limits or claiming
unobserved hardware success.

The five coordinated routines (`greet`, `celebrate`, `curious`, `comfort`, and
`dance`) combine semantic face state, bounded RGB animation, guarded head
targets, and an optional short signature jingle. Jingles use the same paced PCM
lane and AEC playback reference as speech instead of an unobservable firmware
sound path.

The presets are intentionally distinct: warm pink greeting, rainbow
celebration, blue curious tilt, amber comfort bow, and magenta rainbow dance.
Each has a different safe target and an original synthesized musical stinger;
music is generated on the Mac and follows the same observable 24 kHz playback
path as speech.

## Verified hardware contract

The CoreS3 talks to the power board's SCSCL feedback servos over GPIO 6/7 at 1
Mbps. Servo words are big-endian; IDs 1 and 2 are yaw and pitch. Motor power is
enabled through the board expander only for a verified move. Its 650 ms power
stabilization is an asynchronous firmware phase, allowing full-duplex audio and
WebSocket work to continue. Torque plus motor power are released after live
position feedback confirms the target. Firmware boot performs read-only
feedback and limit checks before advertising `motion_verified`.

The three-zone head PCB uses the Si12T at address `0x68`. Our driver is derived
from the chip register map and body schematic: pads are wired to sensing
channels 2-4, so firmware explicitly holds unused channels, enables those three,
leaves sleep mode, and reads all three configuration registers back before
advertising `head_sensor_ready`. Touch, hold, and directional swipe recognition
then run locally at 40 Hz; only semantic gesture events cross the WebSocket.

The factory Wi-Fi namespace remains read-only. A monotonically increasing boot
counter uses the separate `stackchan-meta` NVS namespace, making reset detection
survive power and software resets without modifying the saved credentials.
On each WebSocket connection, a fresh nonce authenticates the device with
HMAC-SHA256 bound to its ID before the firmware enables audio or telemetry. This
removes the static secret from LAN transport, although the WebSocket itself is
still unencrypted and must remain on a trusted network.

## Face behavior

The face is a local state machine, not a video stream. The Mac sends semantic
state such as `listening`, `thinking`, `speaking`, emotion, and speech energy.
The ESP selects aligned 320x240 raster expressions from the original
cream-and-lavender character sheet, adds natural idle blinks, and switches two
speaking mouth shapes with energy hysteresis and a 90 ms minimum hold. The two
frequently changing speech frames are decoded into PSRAM once at boot, avoiding
PNG decompression stalls in the audio/WebSocket loop. Semantic expression
changes are direct because every frame shares the same shell geometry and eye
baseline; there is no forced blink transition between emotions.

## Memory

SQLite stores explicit facts plus bounded automatic profiles and conversation
episodes. Explicit bilingual requests such as “remember that ...” and
“...と覚えて” are written immediately and exact duplicates are suppressed.
Direct, stable first-person preferences and preferred names can update one
semantic profile slot; vague, temporary, negated, and inferred statements do
not. Substantive episodes expire after 30 days and are capped at 50, are hidden
from the ordinary recent-memory list, and are retrieved only by a relevant
query or an explicit earlier-conversation question. Both automatic features
have environment switches. Recognized credential, payment, and common
English/Japanese health patterns are rejected deterministically before any
lookup or write.
Retrieval combines recency, importance, and lexical relevance. English uses
FTS5; unsegmented Japanese also has bounded character-bigram ranking so a
natural question can retrieve the shared subject even when its predicate or a
single speech-recognition character differs. Embeddings remain an optional
adapter, not a schema requirement.

The same store now has loopback-only remember, recall/list, and forget endpoints.
Eve exposes those as typed tools while the Python turn pipeline retains the
deterministic explicit-remember fast path. Eve's connection-scoped history is
conversation context, not a substitute for long-term memory: only the SQLite
store is expected to survive session retirement and support user-visible
deletion.

The same capture boundary runs after a completed response in both cascade/Eve
and native speech-to-speech lanes. Cancelled turns are never captured. Profile
records describe the human in third person internally; provider instructions
require second-person wording so Stack-chan never claims the user's preference
as its own.

## Provider lanes

- `cascade`: laptop-local streaming STT + Eve/GPT-5.6 Luna + laptop-local
  streaming TTS. Default. Eve failure never automatically falls through to a
  different model.
- `speech_to_speech`: persistent GA OpenAI Realtime WebSocket using
  `gpt-realtime-2.1`. The laptop resamples cleaned 16 kHz capture to 24 kHz,
  streams native audio replies to the same device frames, executes allowlisted
  face/light/motion/routine tools plus explicit durable-memory tools, and
  truncates unplayed model audio using measured device playback time. It is
  experimental until live bilingual quality and latency meet the local gates.
- `hosted_realtime`: optional comparison/fallback, never required for local operation.
