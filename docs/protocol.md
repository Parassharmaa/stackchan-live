# Device protocol v1

Control messages are JSON text frames. Audio and camera images use distinct
binary frames.

## Binary audio header

Little-endian layout, followed by signed 16-bit PCM:

| Field | Type | Meaning |
| --- | --- | --- |
| magic | 4 bytes | `STKA` |
| version | u8 | `1` |
| stream | u8 | `1` microphone, `2` speaker |
| flags | u16 | bit 0 start, bit 1 end, bit 2 cancelled |
| sequence | u32 | monotonically increasing per stream |
| timestamp_ms | u32 | sender monotonic timestamp |

## Binary camera header

Little-endian 42-byte layout, followed by the encoded image:

| Field | Type | Meaning |
| --- | --- | --- |
| magic | 4 bytes | `STKI` |
| version | u8 | `1` |
| format | u8 | `1` JPEG |
| width | u16 | image width |
| height | u16 | image height |
| request_id | 32 ASCII bytes | lowercase hexadecimal correlation ID |

Images are limited to 2 MB. The current firmware sends an explicit 320x240
JPEG still; it does not stream ambient video.

## Core control messages

- `auth.challenge` / `hello` / `hello.ack`
- `session.state`
- `transcript.partial` / `transcript.final`
- `face.set`
- `motion.set`
- `lights.set`
- `routine.play`
- `camera.capture`
- `tool.result`
- `playback.duck` / `playback.duck.state`
- `playback.flush` / `playback.flush.state`
- `playback.state`
- `capture.commit` (loopback HIL only)
- `barge_in`
- `telemetry`
- `error`

Unknown JSON fields must be ignored. Unknown message types return a non-fatal protocol error.

## Pairing handshake

Immediately after accepting the WebSocket, the server sends `auth.challenge`
with a fresh 32-byte random nonce and `algorithm: hmac-sha256`. The firmware
answers with `hello`, its `device_id`, and lowercase hex
`auth_response = HMAC-SHA256(shared_secret, nonce + ":" + device_id)`. The
server uses constant-time comparison and sends `hello.ack` only after the
response matches. A challenge is valid only for that connection. The firmware
does not send audio or telemetry and does not accept the session as connected
before `hello.ack`; the static shared secret is never transmitted.

The device `hello` includes persistent `boot_count`, `head_sensor_present`,
`head_sensor_ready`, `camera_present`, and `camera_mode`. Sensor presence means
an I2C response was detected; readiness is
stricter and means the custom channel/wake configuration was written and read
back successfully.

### `playback.state`

Device-to-server edge notification with boolean `active`. Unlike one-second
audio telemetry, this is emitted at the physical speaker's actual start and
drain/flush boundaries. The server uses it for echo-tail gating, barge-in
confirmation, and hardware benchmark assertions.

### `playback.duck.state`

Correlated device acknowledgement emitted after firmware applies a
`playback.duck` command. It echoes the boolean `enabled` state, bounded physical
speaker `gain`, and `request_id`. The server uses this edge to prove that the
speaker has entered its one-frame gain ramp before an interruption's replacement
request is evaluated.

### `playback.flush.state`

Correlated device acknowledgement emitted only after firmware has stopped and
cleared the physical I2S playback path. It returns `success`, the measured
post-operation `active` state, firmware-side `duration_us`, and the original
`request_id`. If the fast I2S restart fails, firmware rebuilds the duplex endpoint
and reports failure unless that recovery succeeds. The server records a confirmed
barge only after a matching acknowledgement says `success: true` and
`active: false`.

### `motion.diagnose`

Loopback-only read probe. The firmware powers the servo rail, discovers the
expected feedback IDs and safe limits, reads current positions, then releases
torque and power without commanding a move. The response includes verified
IDs, positions, limits, and final power state.

### `routine.play`

Server-to-device request for one of the coordinated presets: `greet`,
`celebrate`, `curious`, `comfort`, `dance`, `wake_up`, `focus`, or `good_night`.
A routine combines face state, bounded lights, and head motion only after motion
feedback has been verified. The optional `music` flag requests an original
mini-song but does not bypass the paced audio transport or motion safety gates.

### `camera.capture`

Server-to-device request for one correlated still. Capture is allowed only for
an explicit user photo request. Firmware centers the head, shows a curious face
and white capture light, temporarily owns the shared internal camera-control
bus, sends one `STKI` JPEG, restores the light/sensor bus, and returns a matching
terminal `tool.result`. The laptop stores captures only under ignored local
artifacts and exposes the latest image/metadata only on loopback routes.

### `tool.result`

Device-to-server acknowledgement containing the original tool type, `request_id`, status
(`dispatched`, `completed`, `rejected`, or `failed`), and optional detail. A
`dispatched` command is not proof that physical movement completed; agent text
must only claim completion after a matching terminal result with the same
`request_id`. Motion and routines report completion only after servo feedback;
the routine result also incorporates the actual LED-frame write result.

### `capture.commit`

Loopback-only hardware-test request that makes the device emit `turn.commit` for
its current microphone buffer. It exists so automated HIL tests can exercise
the physical microphone without touching the display and is not exposed to the
agent tool set.

### `sensor.head`

Device-to-server event from the custom Si12T driver. Payload contains `gesture`
(`touch`, `hold`, `swipe_forward`, `swipe_backward`, or `release`), the raw
1-based capacitive `zone`, and `strength` from 0 to 3. Gesture meanings remain
configurable until the three physical zones are calibrated on the target unit.
