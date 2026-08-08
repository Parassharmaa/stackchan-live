# Eve intelligence layer

## Why this is a sidecar

The Python server continues to own realtime audio, echo cancellation, VAD, STT,
TTS, device authentication, and physically verified controls. When selected,
Eve owns connection-scoped conversation sessions, prompt instructions, load-on-demand skills, typed
intelligence tools, context compaction, and the future MCP connection catalog.
This avoids putting a workflow runtime in the audio loop or replacing the custom
ESP firmware.

Eve is currently a preview, so the pinned `0.31.3` package is treated as a
replaceable adapter rather than a protocol dependency of the firmware:

- https://vercel.com/blog/introducing-eve
- https://github.com/vercel/eve

## Run locally through Pixi

Keep these two laptop processes running in separate terminals:

```sh
pixi run intelligence-install
pixi run intelligence
pixi run server
```

`GET /health` on port 8765 reports Eve as a required dependency. The Eve HTTP
surface remains on loopback port 2000. The sidecar defaults to
`gpt-5.6-luna` at reasoning `none`; override only for a measured comparison:

```sh
STACKCHAN_EVE_MODEL=gpt-5.6-terra \
pixi run intelligence
```

Eve authenticates through the Mac's existing `codex login`. It does not use
Ollama or require a separate API key. Raw audio, STT, TTS, and SQLite storage
remain local, but finalized turns, conversation history, and retrieved memory
sent to GPT-5.6 leave the Mac.

One authenticated device WebSocket receives one Eve session. On disconnect the
Python adapter explicitly resets that session rather than orphaning it; a later
device connection starts fresh conversation history. Only the SQLite memory
store survives reconnect and remains listable and deletable.

## Current capability and security boundary

Always-on behavior is in `intelligence/agent/instructions.md`. Ten skills cover
bilingual conversation, expressive embodiment, memory hygiene, music moods,
privacy-bounded ambient awareness, safe daily rhythms, workspace checks, focus
sessions, language practice, and celebrations. Fifteen typed
tools provide durable memory operations, connected-device status, durable
schedule management, and bounded
face, head, light, and coordinated-routine requests through loopback-only Python
endpoints. Latency-critical direct commands still take the deterministic Python
lane; Eve must not duplicate an action already reported in turn context.

The SQLite boundary deterministically rejects recognized credential, payment,
and health patterns before lookup or insertion, including common English and
Japanese diagnoses, medications, and pregnancy terms. This enforcement applies
equally to voice extraction, the local API, and Eve tools; the model instruction
remains a second layer for sensitive phrasing outside the deterministic list.

Eve's default shell, file read/write, glob, grep, arbitrary web fetch/search,
todo, question parking, and subagent delegation tools are explicitly disabled.
Only `load_skill` and the fifteen authored memory/device/schedule tools are visible.
Eve's device tools wait for the correlated terminal firmware result; dispatch
acceptance alone is never treated as completion. The live contract
used Eve to read sensor readiness and request a 12-degree head move; firmware
completed it at raw yaw/pitch error 4/2 and released torque and power.

Explicit requests for one of the fifteen authored tools pass through a bounded AI
SDK middleware. The selector recovers the latest user message after the
application-owned context delimiter, checks that the tool is actually
advertised, and uses a fixed safe-tool list. On each semantic model step it
exposes exactly the next authorized, not-yet-executed tool and lets the model
issue the schema-validated structured call. Eve can therefore complete several
distinct tools in one user turn and reply from their results; every tool is
bounded to one call per turn. Each model step is capped at 2,000 output tokens.
Ordinary turns retain only `load_skill`, while the final answer step after the
authorized sequence has no tools. Conditional wording, metalinguistic examples,
framework tools, unknown tools, and future MCP names never authorize mutation.

## Adding MCP safely

An MCP connection belongs in `intelligence/agent/connections/<name>.ts`. Do not
add a broad connection without a tool allowlist. Read-only tools may run without
approval; create, update, delete, purchase, message, or publish tools must use an
Eve approval policy. Credentials stay in environment or Vercel Connect and must
never enter instructions, conversation history, firmware, or the SQLite memory
tool.

Example shape:

```ts
import { defineMcpClientConnection } from "eve/connections";
import { always } from "eve/tools/approval";

export default defineMcpClientConnection({
  url: process.env.EXAMPLE_MCP_URL!,
  description: "One narrowly scoped connected service.",
  headers: { Authorization: `Bearer ${process.env.EXAMPLE_MCP_TOKEN!}` },
  tools: { allow: ["search", "get"] },
  approval: always(),
});
```

The Python Eve adapter now maps one `tool-approval` request at a time to the
physical voice channel. Each qualified tool needs an exact per-tool tuple of
material fields; every declared field is spoken, and an unknown, omitted, or
additional input field fails closed. Production currently has no promoted MCP
tool summary policy, so an arbitrary future write cannot reach approval merely
because one harmless-looking field was present. The raw tool input is never
spoken.

Approval shows an amber state plus a random two-digit challenge on the display.
The spoken prompt deliberately does not say that challenge; English or Japanese
approval must combine the word `approve`/`承認` with the displayed number. This
prevents Stack-chan's own captured playback from authorizing a write. Unrelated
speech, the old fixed approval sentence, bare `yes`/`はい`, and the wrong number
are inert. Denial remains deliberately easy and safe.
The response is correlated to the original Eve request and durable session;
stale or cross-session IDs are rejected. While approval is pending, transcripts
bypass every local memory-write and physical-action route. Silence expires
irrevocably to `deny` after 30 seconds, even if Eve is temporarily offline
(configurable with `STACKCHAN_EVE_APPROVAL_TIMEOUT_SECONDS`). The firmware also
clears its correlated amber waiting state at that deadline, and a device
disconnect attempts a denial before resetting the Eve session. Timeout and
voice decisions share one response lock, so the same request cannot race into
two decisions. If a fail-closed denial must be retried after Eve recovers, its
continuation is drained first and the user's current utterance is then submitted
as a new turn rather than discarded.

This transport does not make an MCP connection safe by itself. Do not activate
a connection until its file has a narrow `tools.allow` list, an explicit
read/write classification, and `always()` or an equivalent per-tool policy for
every mutation. The current model middleware still refuses unknown/future MCP
names, so promoting a real connection also requires adding its qualified names
to the bounded semantic policy and contract tests. No arbitrary MCP endpoint is
active in the current build.

## Model benchmark result

GPT-5.6 Luna is the active sidecar baseline. The latest warmed Eve run passed
all four bilingual/grounding quality scenarios. Fresh sessions measured a 1,574
ms first-token median and 1,691 ms maximum; after one retained warm-up turn, the
same scenarios measured a 1,559 ms median and 1,574 ms maximum. Its exact
fifteen-tool surface, multi-turn continuity, cancellation recovery,
sensitive-memory denial, authored memory and schedule create/list/delete,
hardware status, and physically verified head command all passed. A prior build
let the model speak text resembling a `remember` call;
the fixed middleware exposes only an explicitly authorized authored tool and
lets the model make the structured call. It never promotes printed syntax,
conditional wording, or an example into an action. Arbitrary MCP activation
remains gated on a connection-specific allowlist and qualified-name policy. The
generic voice approval transport is now implemented and deterministically
tested, but no remote MCP connection has been promoted. The authenticated device
handshake now creates and retains its Eve session before conversation begins,
and records success or failure as device telemetry. Direct Luna transport probes started in
925-1,035 ms, showing that the remaining tail is primarily hosted model/network
latency. Historical Qwen 3.5 9B correctly called the typed memory tool
and answered the failed common-sense case, but measured 36.5 seconds for the
tool turn and 13.0 seconds for a normal answer. Qwen3 8B also called the tool but
took 55.0 seconds and duplicated the idempotent call; its normal answer took
14.9 seconds. Qwen 3.5 4B passed 4/4 content scenarios, but needed an 8.05-second
first-token median, failed cancellation recovery, and returned no response for
the Eve memory-tool probe. None is acceptable for realtime speech. The Qwen3 4B
baseline previously varied between 2/4 and 4/4 live quality scenarios. Stronger
everyday-premise grounding, a natural memory-recall continuation, startup
warmup, and a smaller fixed memory-write schema improved the current baseline.
Those Ollama comparisons are historical and are not active fallbacks.

The production Eve adapter selects conversational depth by intent: one sentence
for commands, jokes, interruptions, and explicit brevity; up to two for grounded
action or memory results; three for ordinary conversation; and four for English
or Japanese explanation/comparison requests. Historical local-model runs are
retained only for comparison and are not active fallbacks.

`pixi run benchmark-long-memory` separately verifies the shared durable layer
through 20 distractor turns. The latest run recalled `amber comet` and `青い月`,
deduplicated writes, rejected sensitive content with HTTP 422, and deleted every
temporary row. Eve uses this same store when enabled, so replacing the session
runtime does not fork or hide the robot's durable memory.

## Local proactive schedules

The laptop owns a SQLite schedule store and a private event queue for each
authenticated device. One-shot and daily schedules require an explicit IANA
timezone and quiet-hours boundary. Due work is leased, waits for an idle device,
and is completed only after Eve-generated dialogue, laptop-local TTS, and the
embodied routine finish. Disconnects, interruptions, and failures release the
lease for a bounded retry. Every schedule can be listed, paused, resumed, or
deleted. Pause and delete prevent future claims; an occurrence already claimed
and physically underway is allowed to finish so a mid-motion stop cannot leave
the robot in an ambiguous state.

`capture_photo` is false unless the user separately authorizes one visible still
for every occurrence. On an authorized surroundings check, Stack-chan shows its
capture cue and takes one still. The image stays laptop-local; Eve receives only
the conservative local-Vision summary needed to ground the spoken reply.

`pixi run benchmark-adaptive-memory` verifies the automatic layer in an
isolated temporary SQLite store. Every request uses a fresh live Eve session,
so correct recall cannot come from connection history. English and Japanese
profile facts and earlier-conversation episodes are retrieved across session
retirement; perspective checks reject “my favorite” when the fact belongs to
the user. Sensitive automatic capture is denied, episodes expire after 30 days
and are capped at 50, and the temporary database is removed after the run.
Ordinary turns retrieve stable profile/fact memory only. Bounded episode text is
returned only for an explicit earlier-conversation question, preventing a bad
old assistant reply from repeatedly contaminating unrelated answers.

Run `pixi run benchmark-intelligence` against the active sidecar. A model must
pass bilingual quality, grounding, spoken-output shape, and a 1.5-second maximum
first-token gate. The same live run
also requires the exact tool allowlist, multi-turn continuity, cancellation
recovery, memory create/recall/delete, sensitive-memory denial, device-status
grounding, and a physically completed bounded head-tool request.

The measured DeepSeek V4 Flash hosted candidate answered all eight direct
English/Japanese quality cases and had a 791 ms median. Through Eve it correctly
executed memory create/delete and passed all content cases, but one explanation
and cancellation recovery each took more than six seconds. It therefore remains
an explicit evaluation option, not an automatic router or realtime default.
