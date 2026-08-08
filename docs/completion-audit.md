# Completion audit

This matrix maps the requested end state to current authoritative evidence.
Passing a narrow unit test is not treated as proof of physical behavior.

| Requirement | Current evidence | Status |
| --- | --- | --- |
| Original ESP32-S3 firmware, not vendor UI | Custom `firmware/src` endpoint, original raster assets, successful PlatformIO build and physical boot 47 | Proven |
| Preserve saved Wi-Fi | Firmware reads the existing `wifi` NVS namespace; custom metadata writes use separate `stackchan-meta`; flash upload omitted the NVS partition and the device reconnected without credential entry | Proven |
| Replay-resistant device pairing | A fresh 256-bit nonce challenge authenticates `HMAC-SHA256(nonce:device_id)` before telemetry/audio is enabled; the static secret is never sent over the LAN | Proven |
| Cute animated face with speech/emotions | Fourteen aligned 320x240 raster frames now include a distinct crying face with tear streams. Boot-44 HIL passed sad, surprised, and crying requests in English/Japanese with correlated completion, post-speech face hold, zero drops, and zero starvation. PSRAM speaking-frame caching and natural blinks remain active | Implemented and physically routed; final appearance remains manual visual QA because the HIL suite has no framebuffer oracle |
| Safe head motion | Live SCSCL feedback, big-endian protocol, five measured preset targets, torque/power release | Proven |
| Body lights and music coordinated with behavior | Eight distinct face/head/light presets physically completed every motion step and LED frame. Six bilingual physical music cases now pass for fanfare, chiptune dance, sunrise, gentle waltz, lo-fi focus, and lullaby; all 4.8-7.04 second pieces use the paced speech queue and added zero playback drops/starvation | Proven |
| Explicit camera interaction | Boot-44 acoustic “Look at me and tell me how I'm looking today” centered the head, showed the capture cue, transferred a valid 320x240 JPEG, and restored lights/shared sensor bus with zero new sensor failures. One run detected a face and described only that evidence; the latest low-confidence frame produced an honest inability to judge. Both replies played and drained with zero drops/starvation | Proven for explicit photo and direct user-inspection stills with conservative laptop-local analysis; continuous video intentionally deferred |
| Top-head sensor reactions | Physical Si12T found at 0x68; channels 2-4 and wake state are configured and read back from the chip. The latest boot-35 physical touch produced Luna-generated dialogue, a four-step `curious` head/light routine with verified servo feedback, overlapping physical speech, and a clean playback drain. A 2.5-second coalescing gate preserved that first reaction while the same contact generated later touch/swipe edges | Proven for positive physical gesture, LLM dialogue, motion, lights, and speech |
| Local English/Japanese conversation | The established 301.8-second acoustic soak passed 9/9 semantic cases; the newer boot-18 grounded regression passed 3/3 English/Japanese cases over 97.96 seconds | Proven |
| Voice interruption while speaking | Boot 47 now transports the post-gain physical I2S render beside microphone audio, uses unity playback capture gain, and reports per-capsule RMS. One isolated English run decoded `Stop talking`, captured `I need a short joke instead`, and physically flushed in 8.289 ms with zero drops/starvation. The subsequent full bilingual repeat still failed to recover either far-field cue, so the latest acceptance artifact remains red. The final speaker-only safety regression passes both languages with zero false ducks/barges, zero clipped samples, and zero drops/starvation | Physical reference and one positive path proven; repeatable far-field English/Japanese interruption remains open |
| Laptop-local STT/TTS with cloud intelligence | Resident whisper.cpp and Supertonic stay on the Mac; Eve uses GPT-5.6 Luna through the local Codex login. Raw audio and SQLite remain local, while finalized transcript/context leaves the Mac. A bounded 96-frame server PCM queue now prefetches later TTS phrases while the first plays. The latest bilingual long-speech HIL passed with zero ESP drops, starvation, unintended ducks, or self-interruptions. Per-request text/timing/frame counts and queue high-water marks are traced | Speech locality and continuous physical playout proven in the latest bilingual regression |
| Typed face/head/light/routine tools | Allowlisted protocol, bounded bilingual routing, per-request correlation, and LLM-visible results only after matching terminal firmware success/failure/timeout. Exact recent failures `Head towards left` and `blink your lights very fast` now pass on boot 35: servo target feedback was verified and torque released; the LED frame completed over I2C. Both acknowledgements used zero retrieved episodic memories | Proven in deterministic and fresh physical regressions |
| Durable bilingual memory | SQLite explicit memory plus replaceable stable profiles and 30-day/50-item bounded episodes; list/forget operations; deterministic sensitive-data rejection; language-preferred retrieval; physical explicit English/Japanese recall; 20-distractor explicit benchmark; an 11-check fresh-session Eve benchmark; and physical natural-statement profile learning/recall for exact `dragon fruit` and `ほうじ茶` values with correct user perspective, zero drops/starvation, isolated storage, and cleanup | Proven for explicit, profile, and bounded episodic memory |
| Durable proactive schedules | Laptop-local SQLite supports one-shot/daily entries, IANA timezones, overnight quiet hours, leases, bounded retry, and list/pause/resume/delete. Physical schedule 2 proved generated English speech and focus motion/lights/face, then firmware playback idle before completion. Physical schedule 4 separately captured one explicitly authorized still, accepted conservative local Vision, generated an honest Japanese reply, played the curious routine, and completed 10 ms after playback idle with no retry | Proven for bilingual check-ins and explicit one-still surroundings checks; no continuous capture or external autonomous actions |
| Expandable provider architecture | Shared device session supports default Eve/GPT-5.6 Luna cascade and optional `speech_to_speech`; Eve adds ten companion skills, compaction, cancellation, reset-on-disconnect, fifteen authored memory/device/schedule tools, and an allowlisted MCP seam. Skills cover music moods, privacy-bounded ambient awareness, safe daily rhythms, scheduled workspace checks, focus sessions, bilingual practice, and celebrations. Mutations remain fail-closed; direct natural status/recall/exact-delete requests expose one bounded authored tool, ordinary turns otherwise expose only `load_skill`, and post-result turns expose none. Eve `input.requested` approvals are session scoped and require an unspoken random display challenge plus an exact per-tool material-field schema. Production has no promoted MCP approval schema. The latest Luna run passed 4/4 content quality and every prior contract; persistent first-token p50/max were 1,559/1,574 ms, so the strict 1.5-second gate remains open | Authored tools, durable opt-in schedules, and generic fail-closed approval transport proven; hosted tail latency and promotion of one real allowlisted MCP connection remain optional follow-up work |
| Hosted native speech-to-speech behavior | GA Realtime session/audio/tool/memory/cancellation contract is deterministic-test covered | Proven without live quality claim |
| Hosted Realtime latency and bilingual quality | Reproducible benchmark exists and fails closed without credentials | Missing external credential |
| Local end-to-end speech model on Mac | Moshi-MLX evaluated; English-only. J-Moshi evaluated; no macOS support and unsuitable instruction following | Rejected, cascade retained |
| Reproducible latency/quality benchmarks | Pixi tasks cover local stages, TTS/LLM comparisons, bilingual HIL, memory, routines, camera, sensor, and soak | Proven |
| Cold startup/reconnect | First unprimed bilingual physical turn after full server restart passed; the authenticated device handshake now retained a ready Eve session in 2,068 ms before conversation; repeated bootloader/partition/application flashes left NVS untouched and the HMAC-authenticated device reconnected with its saved Wi-Fi at persistent boot count 34 | Proven |
| Full build health | Fresh `pixi run check` passed Ruff, 235 Python tests, six Eve policy tests, TypeScript typecheck/build, 14 regenerated face frames, and the ESP32-S3 PlatformIO build (49.7% RAM, 36.0% flash) | Proven |

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
- `artifacts/benchmarks/hil-camera-latest.json`
- `artifacts/benchmarks/hil-daily-routines-latest.json`
- `artifacts/benchmarks/hil-schedules-latest.json`
- `artifacts/benchmarks/hil-music-styles-latest.json`
- `artifacts/benchmarks/hil-face-requests-latest.json`
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
3. GPT-5.6 Luna now passes bilingual answer quality, the authored Eve memory and
   schedule contracts, and the physical head-tool path under the bounded
   named-tool policy. Its warmed 1,559 ms p50 and 1,574 ms maximum narrowly miss
   the strict realtime gate. Arbitrary MCP promotion also remains optional: the generic
   bilingual voice approval transport now passes deterministic session,
   timeout, disconnect, and stale-ID contracts, but each real connection still
   needs a deliberate read/write allowlist and bounded qualified-name policy.
