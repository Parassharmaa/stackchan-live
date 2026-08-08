# Voice provider evaluation

## Production decision

The production default is the bilingual cascade with laptop-local speech:

- base whisper.cpp for fast English and language detection;
- small whisper.cpp for the normal Japanese result, with a `-0.18` average-log-
  probability gate;
- large-v3-turbo whisper.cpp only for low-confidence Japanese, retaining the
  higher-confidence transcript;
- GPT-5.6 Luna through Eve's Codex-subscription Responses transport with
  reasoning disabled for the latency baseline;
- five-step Supertonic with the F5 voice for the current higher-intelligibility
  bilingual profile.

It is the baseline lane for English, Japanese, device tools, and durable memory
on this 36 GB M3 Pro Mac. It is not offline: finalized transcripts and selected
memory/context leave the Mac for model inference, while raw audio, STT, TTS,
and SQLite stay local. The boot-34
bilingual speaker-only physical regression passed substantive Eve answers with
zero false barges, drops, or starvation and 49/77 mouth transitions plus 3/5
blinks.
Historical firmware has physically confirmed cue-triggered queue flushes. The
current correlated 26 dB duck plus physical-flush path also passed one complete English
cue-plus-replacement run, but the distant fixture missed the initial cue on an
English repeat and Japanese run. Interruption is therefore not yet a repeatable
bilingual far-field quality pass.

## Eve intelligence and historical local models

Eve 0.31.1 is the default intelligence layer and remains separate from the
realtime Python audio server. It adds connection-scoped multi-turn sessions, context
compaction, Markdown instructions and skills, typed memory tools, cancellation,
and an allowlist-oriented MCP connection point. Shell, filesystem, arbitrary
web, question parking, todo, and subagent defaults are disabled.

The current GPT-5.6 Luna sidecar passes bilingual content, authored-memory-tool,
cancellation, and physically verified head-tool contracts. Its latest selective-
tool run measured 1,213 ms first-token p50, but a 4,229 ms model/network outlier
failed the strict 1.5-second maximum gate. A fixed policy selects one explicitly requested,
advertised authored tool at the AI SDK boundary; it cannot select unknown or
future MCP tools, fails closed after a tool result, and never treats
model-printed pseudo syntax, conditional wording, or metalinguistic examples as
execution.
Explicit voice memory also remains reliable through the deterministic Python
lane. Future MCP promotion stays gated on a connection allowlist and a
voice-capable approval path.
Disconnect explicitly
retires the Eve session; the separate SQLite memory store is the only
conversation data expected to persist across device reconnects.

The active Luna run delivered 100% content quality and passed the exact tool
surface, multi-turn continuity, early cancellation recovery, memory write/delete,
sensitive-memory denial, device status, and physical head movement. Direct raw
Codex-subscription probes started in 925-1,035 ms; Eve's latest p50 was 1,213 ms,
so most remaining latency is model/network time rather than local STT or TTS.

The historical synthetic comparison exposed a clear local-model tradeoff. The warm Qwen3 4B
lane usually begins an Eve follow-up in about 0.5-0.8 seconds. Its latest run
passed 4/4 content scenarios and the full authored-tool contract at 609 ms p50
and 715 ms maximum first-token latency. Earlier unforced runs missed a memory
tool call and one common-sense question. Qwen 3.5 9B used
the memory tool and answered the question correctly, but took 36.5 seconds for
the tool turn and 13.0 seconds for a normal reply. Qwen3 8B also used the tool
but took 55.0 seconds, duplicated the idempotent call, and took 14.9 seconds for
the normal reply. Qwen 3.5 4B passed all content probes, but its 8.05-second
first-token median, incorrect cancellation recovery, and failed memory-tool turn
also miss the realtime contract. These rejected candidates were removed after
measurement and can be downloaded again by model name.

The direct Qwen3 4B adapter now uses intent-adaptive response depth and passed
24/24 bilingual joke, memory, motion-grounding, and explanation runs. Its suite
median was 522 ms; detailed generation medians were 2.09 seconds in English and
2.70 seconds in Japanese. Qwen3 1.7B was faster but failed every Japanese
motion-grounding repetition, so it is not routed into production turns.

The physical pipeline still stores an explicit remember request deterministically
before Eve runs and passes the terminal memory result into the model context.
Recognized credential, payment, and common English/Japanese health patterns are
rejected in the shared Python store before either the voice fast path or an Eve
tool can persist them.
Completed turns can also capture only direct stable preferences/names and one
bounded non-sensitive conversation episode. The same capture hook runs for
Eve cascade and native speech-to-speech; episodes expire after 30 days
and are capped at 50. The live fresh-session Eve benchmark passes profile and
episode recall in both languages without perspective inversion.
This preserves the proven local memory behavior while better tool-capable model
lanes are evaluated. `pixi run benchmark-intelligence` is the repeatable quality
and first-token gate for this boundary; it also verifies the live tool allowlist,
multi-turn continuity, cancellation recovery, and the memory mutation/denial
contract.

## Native hosted speech-to-speech

The optional `speech_to_speech` provider uses the GA OpenAI Realtime WebSocket
with `gpt-realtime-2.1`. This matches OpenAI's current guidance: live audio is
the recommended path for conversational agents needing low first-audio
latency, barge-in, natural turn-taking, and realtime tools, and WebSocket is the
appropriate transport when a server already owns raw media.

The adapter has deterministic coverage for GA session shape, 16-to-24 kHz
audio resampling, bilingual transcripts, streaming audio, tools, explicit
memory, response continuation, cancellation, and truncation to physically
played audio. It fails closed when no key is present. A live latency/quality
claim still requires `OPENAI_API_KEY` and `pixi run benchmark-realtime`.

Primary documentation:

- https://developers.openai.com/api/docs/guides/voice-agents#build-a-speech-to-speech-voice-agent
- https://developers.openai.com/api/docs/guides/realtime

## Local end-to-end speech candidates

### Moshi-MLX: reject for the bilingual production selector

Kyutai's Moshi is a genuine full-duplex spoken-dialogue model. Its official
MLX implementation supports on-device inference on Apple Silicon and was
tested on an M3 MacBook Pro. The q4 Moshika checkpoint is approximately 4.8 GB
and the model architecture targets roughly 160 ms theoretical / 200 ms
practical latency under the published reference conditions.

It is not selected because the released MLX dialogue checkpoint is English,
not English/Japanese. Its bare command-line client also provides no echo
cancellation or lag compensation, both of which are essential for Stack-chan's
open speaker/microphone geometry. Adding it would create an English-only lane
without satisfying the product contract.

Primary sources:

- https://github.com/kyutai-labs/moshi
- https://huggingface.co/kyutai/moshika-mlx-q4

### J-Moshi: reject for this Mac and product contract

J-Moshi is a Japanese full-duplex research model based on Moshi. Its own
documentation says it is prototype-stage, is dominated by casual-dialogue
training, does not reliably follow user instructions, requires a Linux GPU
with at least 24 GB VRAM, and does not support macOS. It is also released for
research under CC BY-NC 4.0. It therefore cannot supply the Japanese half of a
local Mac production provider, cannot reliably drive tools, and cannot be
combined with the English MLX checkpoint as one behaviorally equivalent lane.

Primary source:

- https://github.com/nu-dialogue/j-moshi

## Revisit criteria

Reconsider a local end-to-end model only when one checkpoint/runtime provides:

1. supported Apple Silicon inference within the 36 GB memory budget;
2. both English and Japanese conversational quality;
3. stable full-duplex streaming with an external PCM transport;
4. interruption and echo behavior compatible with the physical robot;
5. structured tool calling or a safe tool bridge;
6. durable-memory injection and auditable transcripts;
7. a license suitable for the intended deployment.

Any candidate must pass the same physical bilingual HIL and five-minute soak as
the cascade before it can become selectable in production.
