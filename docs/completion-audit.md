# Completion audit

This matrix maps the requested end state to current authoritative evidence.
Passing a narrow unit test is not treated as proof of physical behavior.

| Requirement | Current evidence | Status |
| --- | --- | --- |
| Original ESP32-S3 firmware, not vendor UI | Custom `firmware/src` endpoint, original raster assets, successful PlatformIO build and physical boot 35 | Proven |
| Preserve saved Wi-Fi | Firmware reads the existing `wifi` NVS namespace; custom metadata writes use separate `stackchan-meta`; flash upload omitted the NVS partition and the device reconnected without credential entry | Proven |
| Replay-resistant device pairing | A fresh 256-bit nonce challenge authenticates `HMAC-SHA256(nonce:device_id)` before telemetry/audio is enabled; the static secret is never sent over the LAN | Proven |
| Cute animated face with speech/emotions | Thirteen aligned 320x240 original expressions, PSRAM speech-frame cache, and the latest boot-34 bilingual physical playback telemetry with 108/137 mouth transitions plus 5/6 blinks in the complete replies | Implemented; final appearance remains manual visual QA because the HIL suite has no camera/framebuffer oracle |
| Safe head motion | Live SCSCL feedback, big-endian protocol, five measured preset targets, torque/power release | Proven |
| Body lights and music coordinated with behavior | Five face/head/light presets; boot-18 acoustic `dance with music` HIL passed with 44 PCM frames, verified servo completion, and an actually written LED frame | Proven |
| Top-head sensor reactions | Physical Si12T found at 0x68; channels 2-4 and wake state are configured and read back from the chip. The latest boot-35 physical touch produced Luna-generated dialogue, a four-step `curious` head/light routine with verified servo feedback, overlapping physical speech, and a clean playback drain. A 2.5-second coalescing gate preserved that first reaction while the same contact generated later touch/swipe edges | Proven for positive physical gesture, LLM dialogue, motion, lights, and speech |
| Local English/Japanese conversation | The established 301.8-second acoustic soak passed 9/9 semantic cases; the newer boot-18 grounded regression passed 3/3 English/Japanese cases over 97.96 seconds | Proven |
| Voice interruption while speaking | Boot 34 has historical positive correlated duck/flush runs, including one bilingual sequence with 7.892/7.014 ms physical acknowledgements. The current `hil-latest.json` failed repeatability: primary prompts and cue-only transcripts were captured, but neither replacement request survived, so no barge, duck, or flush was authorized. Physical playback still drained with zero drops/starvation, and the separate speaker-only safety regression passes both languages with zero false barges | Historical positive path only; current repeatable far-field gate fails and further hardware work is deferred |
| Laptop-local STT/TTS with cloud intelligence | Resident whisper.cpp and Supertonic stay on the Mac; Eve uses GPT-5.6 Luna through the local Codex login. Raw audio and SQLite remain local, while finalized transcript/context leaves the Mac. A bounded 96-frame server PCM queue now prefetches later TTS phrases while the first plays. The latest bilingual long-speech HIL passed with zero ESP drops, starvation, unintended ducks, or self-interruptions. Per-request text/timing/frame counts and queue high-water marks are traced | Speech locality and continuous physical playout proven in the latest bilingual regression |
| Typed face/head/light/routine tools | Allowlisted protocol, bounded bilingual routing, per-request correlation, and LLM-visible results only after matching terminal firmware success/failure/timeout. Exact recent failures `Head towards left` and `blink your lights very fast` now pass on boot 35: servo target feedback was verified and torque released; the LED frame completed over I2C. Both acknowledgements used zero retrieved episodic memories | Proven in deterministic and fresh physical regressions |
| Durable bilingual memory | SQLite explicit memory plus replaceable stable profiles and 30-day/50-item bounded episodes; list/forget operations; deterministic sensitive-data rejection; language-preferred retrieval; physical explicit English/Japanese recall; 20-distractor explicit benchmark; an 11-check fresh-session Eve benchmark; and physical natural-statement profile learning/recall for exact `dragon fruit` and `ほうじ茶` values with correct user perspective, zero drops/starvation, isolated storage, and cleanup | Proven for explicit, profile, and bounded episodic memory |
| Expandable provider architecture | Shared device session supports default Eve/GPT-5.6 Luna cascade and optional `speech_to_speech`; Eve adds skills, compaction, cancellation, reset-on-disconnect, nine authored memory/device tools, and an allowlisted MCP seam. Mutations remain fail-closed; direct natural status/recall/exact-delete requests expose one bounded authored tool, ordinary turns otherwise expose only `load_skill`, and post-result turns expose none. Eve `input.requested` approvals are session scoped and require an unspoken random display challenge plus an exact per-tool material-field schema. Local side effects are isolated; stale IDs, self-echo phrases, incomplete summaries, timeout/voice races, and failed-denial turn loss are regression covered. Production has no promoted MCP approval schema. The latest Luna run passed 4/4 content quality and every prior contract, grounded a natural sensor-status question, created/deleted memory, and physically completed the bound head-tool request. Its 1,514 ms maximum missed the strict 1.5-second gate by 14 ms | Authored tools and generic fail-closed approval transport proven in deterministic tests; tail latency and promotion of one real allowlisted MCP connection remain open |
| Hosted native speech-to-speech behavior | GA Realtime session/audio/tool/memory/cancellation contract is deterministic-test covered | Proven without live quality claim |
| Hosted Realtime latency and bilingual quality | Reproducible benchmark exists and fails closed without credentials | Missing external credential |
| Local end-to-end speech model on Mac | Moshi-MLX evaluated; English-only. J-Moshi evaluated; no macOS support and unsuitable instruction following | Rejected, cascade retained |
| Reproducible latency/quality benchmarks | Pixi tasks cover local stages, TTS/LLM comparisons, bilingual HIL, memory, routines, sensor, and soak | Proven |
| Cold startup/reconnect | First unprimed bilingual physical turn after full server restart passed; Eve is now warmed during server startup; repeated bootloader/partition/application flashes left NVS untouched and the HMAC-authenticated device reconnected with its saved Wi-Fi at persistent boot count 34 | Proven |
| Full build health | Final `pixi run check` passed after the publication cleanup: Ruff, 190 Python tests, five Eve policy tests, TypeScript typecheck/build, regenerated face assets, and the ESP32-S3 PlatformIO build (45.5% RAM, 33.5% flash) | Proven |

## Evidence artifacts

- `artifacts/benchmarks/hil-soak-bilingual-90s-grounded-boot18.json`
- `artifacts/benchmarks/hil-soak-final-flush-first-5m.json`
- `artifacts/benchmarks/hil-barge-ja-prerendered-boot18.json`
- `artifacts/benchmarks/hil-cold-reconnect-first-turn-bilingual.json`
- `artifacts/benchmarks/hil-routine-music-latest.json`
- `artifacts/benchmarks/hil-voice-motion-latest.json`
- `artifacts/benchmarks/hil-confidence-router-bilingual-windowed120.json`
- `artifacts/benchmarks/stt-routing-broad-ja.json`
- `artifacts/benchmarks/hil-memory-latest.json`
- `artifacts/benchmarks/hil-sensor-physical-initialized.json`
- `artifacts/benchmarks/long-memory-latest.json`
- `artifacts/benchmarks/adaptive-memory-latest.json`
- `artifacts/benchmarks/hil-profile-memory-latest.json`
- `artifacts/benchmarks/hil-no-false-barge-bilingual-boot27-dual-source-final.json`
- `artifacts/benchmarks/hil-interruption-en-boot27-dual-source-live.json`
- `artifacts/benchmarks/hil-interruption-en-boot27-dual-source-preroll200.json`
- `artifacts/benchmarks/hil-interruption-en-boot29-dynamic-codec-gain.json`
- `artifacts/benchmarks/hil-conversation-depth-en-boot30-sensor-soak.json`
- `artifacts/benchmarks/hil-sensor-interrupt-en-boot30.json`
- `artifacts/benchmarks/hil-no-false-barge-bilingual-boot31-raw-cue-final.json`
- `artifacts/benchmarks/hil-no-false-barge-bilingual-boot31-final.json`
- `artifacts/benchmarks/hil-interruption-en-boot31-fast-preferred-safe-request.json`
- `artifacts/benchmarks/hil-interruption-bilingual-boot31-final.json`
- `artifacts/benchmarks/hil-interruption-en-boot33-naturalcue-paused-ackduck26-run1.json`
- `artifacts/benchmarks/hil-no-false-barge-bilingual-boot33-eve-final.json`
- `artifacts/benchmarks/hil-interruption-en-boot34-correlated-physical-ack-run2.json`
- `artifacts/benchmarks/hil-no-false-barge-bilingual-boot34-reviewed-final.json`
- `artifacts/benchmarks/hil-interruption-ja-boot34-ja700-specialized-run1.json`
- `artifacts/benchmarks/hil-interruption-en-boot34-current-regression.json`
- `artifacts/benchmarks/hil-no-false-barge-bilingual-boot34-dual-model-current.json`
- `artifacts/benchmarks/hil-latest.json`
- `artifacts/benchmarks/hil-interruption-bilingual-unity-repeat-boot34.json`
- `artifacts/benchmarks/hil-interruption-bilingual-unity-retry-boot34.json`
- `artifacts/benchmarks/hil-no-false-barge-latest.json`
- `artifacts/benchmarks/hil-recent-regressions-latest.json`
- `artifacts/benchmarks/hil-sensor-latest.json`
- `artifacts/benchmarks/eve-intelligence-latest.json`
- `artifacts/benchmarks/eve-intelligence-deepseek-v4-flash-cloud.json`
- `artifacts/benchmarks/hil-conversation-depth-eve-boot29.json`
- `docs/benchmark-results.md`
- `docs/provider-evaluation.md`

## Open evidence

Open evidence and current gaps:

1. A live OpenAI Realtime latency/quality result requires a user-supplied API
   key. The local production system does not depend on it.
2. Historical normalized fixtures include a complete bilingual pass with
   correlated physical duck/flush acknowledgements and zero drops/starvation.
   The current artifact failed both languages after two cue attempts because
   only the control cue, not the actionable replacement, survived capture. The
   verifier correctly refused to flush. Repeatable bilingual far-field capture
   remains open and further hardware interruption work is currently deferred.
3. GPT-5.6 Luna now passes bilingual answer quality, the authored Eve memory-
   tool contract, and the physical head-tool path under the bounded named-tool
   policy. Its 1,213 ms p50 is usable, but the 4,229 ms maximum keeps the strict
   realtime gate open. Arbitrary MCP promotion also remains open: the generic
   bilingual voice approval transport now passes deterministic session,
   timeout, disconnect, and stale-ID contracts, but each real connection still
   needs a deliberate read/write allowlist and bounded qualified-name policy.
