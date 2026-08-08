# Benchmark results

Measured on the connected Stack-chan and a 36 GB M3 Pro MacBook Pro on
2026-08-06. JSON reports and cleaned diagnostic WAVs are local ignored
artifacts under `artifacts/benchmarks/`.

## Far-field interruption status (deferred, 2026-08-08)

The current `hil-latest.json` is a failed repeatability run and must not be
treated as a passing gate. Both primary counting prompts were recognized and
physical playback started and drained with zero drops or starvation, but the
two cue attempts in each language retained only `Stop talking.` and
`待ってください。`; the replacement request was not preserved, so no semantic
barge-in, physical duck, or queue flush was authorized.

An earlier one-off normalized fixture recovered the complete English and
Japanese replacement requests, received correlated physical duck/flush
acknowledgements, and measured 7.892/7.014 ms command-to-physical-ack flush
latency. The next replay did not reproduce it, so the positive run remains
historical evidence rather than current acceptance.

This run exposed and verifies two server fixes: the physical duck handoff now
retains the already-spoken raw continuation instead of clearing it, and the
confirmation deadline restarts at the correlated duck acknowledgement. The raw
cue-anchored lane retains a 3.2-second window; projected and AEC probes keep the
shorter anti-echo window. Speaker echo may still open at most one bounded
listening dip and can never authorize a queue flush by itself.

Consecutive far-field replay is not deterministic. The runner reports
`control_cue_attempts` rather than hiding retries. This keeps repeatable
far-field capture open while preserving the historical physical proof above.
Further hardware interruption work is intentionally deferred. The separate
`hil-no-false-barge-latest.json` speaker-only regression passes both languages
with zero false barges, drops, or starvation.

## Streaming-TTS physical and Eve regression (2026-08-08)

`hil-conversation-depth-gpt-5.6-luna-streaming-boot34.json` passed complete
English and Japanese acoustic turns on the physical boot-34 device. Rendering
the first complete phrase while prefetching the remainder reduced local TTS
first-audio latency from the prior 1,797/2,649 ms English/Japanese result to
752/721 ms. Both three-sentence answers drained completely with zero dropped
frames, zero starvation, 108/137 mouth transitions, and 5/6 blinks.

The refreshed `eve-intelligence-latest.json` passed all four quality scenarios
and every safety/session/memory/device contract. A natural “Are your physical
head sensors ready?” request was grounded in live status, and the session-bound
12-degree move completed with raw yaw/pitch error 4/3 before torque and power
were released. First-token p50 was 1,225 ms; the 1,514 ms maximum missed the
strict 1,500 ms gate by 14 ms, so hosted tail latency remains open.

`long-memory-latest.json` passed 20 alternating bilingual distractor turns,
exact English/Japanese recall, duplicate suppression, HTTP 422 sensitive-data
rejection, and verified cleanup. Distractor p50 was 1,886 ms; Japanese recall
had a 6,248 ms tail.

## Earlier Eve GPT-5.6 Luna intelligence benchmark (2026-08-08)

`eve-intelligence-gpt-5.6-luna-selective-tools-boot34.json` exercises the active
Eve sidecar through the Python adapter and local Codex login, with no Ollama
process. GPT-5.6 Luna at reasoning `none` passed all four English/Japanese
quality scenarios and every contract check. First-token p50 improved to 1,213
ms after ordinary turns were limited to the `load_skill` tool, but one 4,229 ms
outlier failed the strict 1.5-second maximum gate. Three direct transport probes
without Eve started in 925-1,035 ms, locating most latency in the hosted
model/network path rather than local orchestration.

The run passed the exact ten-tool published surface, connection-scoped follow-
up, early cancellation recovery, sensitive-memory denial, authored memory
create/delete, device status, and a physical 12-degree head command. Firmware
completed that move at raw yaw/pitch error 4/2 and released torque and power.
Conditional or metalinguistic requests cannot expose mutating tools; ordinary
turns expose only `load_skill`, and post-result turns expose no tools. The
current quality and safety contract therefore passes, while tail-latency and
arbitrary MCP voice approval remain open.

## Historical Eve local-model benchmark (2026-08-07)

`eve-intelligence-authorized-middleware-boot34.json` exercises the live Eve sidecar
through the same Python adapter used by the device. The current Qwen3 4B
sidecar run passed all four quality scenarios with a 609 ms first-token median
and 715 ms maximum. A
startup warmup pays Eve's workflow/model cold-start before live device speech.

The exact tool allowlist, connection-scoped follow-up, early pre-turn-ID remote
cancellation recovery, authored and direct memory create/recall/delete,
sensitive-memory HTTP 422 denial, device status, and the physical head move all
passed. A previous build let the 4B model speak text resembling a `remember`
call instead of executing it. The bounded middleware now emits a structured
call for one explicitly requested, advertised authored tool from a fixed safe
list. Ordinary turns expose only passive read tools, and post-result answer
steps expose none, preventing unauthorized actions and duplicate calls. It
ignores context data, unknown tools, and future MCP names. The exact visible
surface is `load_skill` plus nine typed
memory/device tools; shell, filesystem, arbitrary network, todo, questions, and
subagent delegation remain disabled. Eve also read the physical head-sensor
status and requested a bounded 12-degree head move. Firmware verified completion
at raw yaw/pitch error 4/2 and released torque and power. Cleanup left no
temporary benchmark rows. Printed pseudo-tool syntax is never interpreted as an
action. Arbitrary MCP stays below the promotion gate until a per-connection
allowlist and a voice-capable approval path exist.

The following results are retained only as historical provider comparisons;
Ollama is no longer in the active runtime. Three stronger candidates were
downloaded, measured, rejected, and then removed
to recover disk space. Qwen 3.5 9B correctly used the typed memory tool and fixed
the common-sense answer, but took 36.5 seconds for that tool turn and 13.0
seconds for a normal answer. Qwen3 8B used the tool but took 55.0 seconds,
repeated the idempotent write, and needed 14.9 seconds for the normal answer.
Qwen 3.5 4B passed all four content scenarios, but its first-token median was
8.05 seconds, cancellation recovery was incorrect, and its Eve memory-tool turn
returned no model response. No rejected local model remains an operational
fallback. The active path is Eve with the configured hosted model; its current
authored-tool contract passes while arbitrary MCP promotion remains open.

DeepSeek V4 Flash was also measured as an explicit Ollama-hosted candidate. The
direct eight-case bilingual suite passed 100% at a 791 ms median. The full Eve
run passed content quality and reliably executed memory create/delete, but one
explanation started at 6.83 seconds and cancellation recovery started at 6.62
seconds. That tail latency fails the 1.5-second realtime gate, so the hosted
model is opt-in only and is never selected automatically.

`hil-conversation-depth-eve-boot29.json` then validated the selected Eve backend
through the full physical acoustic path. English and Japanese questions were
transcribed accurately enough to verify intent, produced correct two-sentence
explanations, started Eve output at 457/511 ms, physically started and drained
both replies, animated 63/65 mouth transitions and 3/4 blinks, and recorded zero
unexpected barges, dropped frames, or starvation events.

## Adaptive local intelligence benchmark (2026-08-08)

`llm-adaptive-depth-latest.json` covers eight English/Japanese scenarios with
three repetitions each: jokes, durable-memory grounding, unconfirmed motion,
and explanatory depth. Qwen3 4B passed all 24 runs with a 522 ms median response
latency across the suite. Explanatory answers now finish as complete two-to-four
sentence responses instead of being cut at the former short character ceiling;
median generation time was 2.09 seconds in English and 2.70 seconds in Japanese.

The comparison run in `llm-models-latest.json` showed why the 1.7B model is not
the default despite its 281 ms median: it failed all three Japanese
motion-grounding repetitions. The 4B model passed 24/24 at a 632 ms median in
that same run. Commands and explicit brevity requests remain one sentence,
memory and physical-action confirmations use up to two, ordinary conversation
uses up to three, and explanation/comparison intents use up to four.

The memory tool probes used temporary facts and deleted their exact SQLite rows
after validation.

## Long-session memory benchmark (2026-08-08)

`long-memory-latest.json` passes the direct production intelligence path after
20 bilingual distractor turns. It stores and deduplicates temporary facts,
recalls `amber comet` in English and `青い月` in Japanese, rejects a sensitive
memory write with HTTP 422, and removes every temporary row after the run.
Distractor-turn latency p50 was 307 ms; final English and Japanese recall took
1,479 ms and 1,376 ms respectively. This proves explicit durable memory across
a long conversation.

## Adaptive profile and episode memory (2026-08-08)

`adaptive-memory-latest.json` passed all 11 checks through the production
`CascadePipeline` and live Eve sidecar. Its eight main English/Japanese turns
opened fresh Eve sessions and used an isolated temporary SQLite database, so
the correct profile and episode answers could not come from connection history
or the user's existing memories. English recalled dragonfruit with “your”
perspective; Japanese recalled ほうじ茶 with 「あなたの」 perspective. Both
languages also recalled the latest rainy-day discussion after session
retirement. Warm first-delta latency ranged from 462 to 854 ms; one cold turn
took 4,285 ms, and the complete run took 29.79 seconds.

The same run verified 30-day episode expiry metadata, automatic sensitive-data
denial, no unconfirmed “I remembered that” claims, non-empty responses, and
temporary-data cleanup. A final same-session English-to-Japanese pair also
proved that the latest turn language overrides earlier Eve session language.
Unit coverage additionally proves a 50-episode cap,
language-preferred recall, semantic profile replacement, explicit promotion to
permanent memory, negated-memory handling, command exclusion, and legacy-schema
migration without data loss.

`hil-profile-memory-latest.json` then passed the automatic profile path through
the real Mac speaker, Stack-chan microphone, Eve generation, and Stack-chan
speaker. Stack-chan heard “My favorite fruit is dragon fruit”, stored a profile,
and later answered “Your favorite fruit is dragon fruit”. It separately heard
「私の好きな飲み物はほうじ茶です」 and later answered
「あなたの好きな飲み物はほうじ茶です」. Both replies preserved the exact
value and correct user perspective. All four physical replies started and
drained, with zero new dropped playback frames and zero starvation events. The
test ran against an initially empty isolated SQLite database, removed its test
rows, and finished with zero rows and `PRAGMA integrity_check = ok`.

## Boot-31 conservative interruption regression (2026-08-08)

Boot 31 retains the 33-to-27 dB ES7210 playback switch and adds a smooth,
one-shot 12 dB speaker duck only after a validated preliminary Stop/Wait cue.
The server can use a stable repeated cross-language cue to confirm that window,
or accept a preferred-language raw continuation when it contains an actionable
replacement request. On confirmation it commits the exact semantic probe that
proved the request plus only later audio, preventing earlier robot echo from
dominating final STT.

The first one-language safety artifact passed, but the next bilingual stress run
correctly exposed a remaining English failure: AEC repeatedly decoded robot
speech as `Stop laughing`, then a raw `I need.` hallucination was accepted as a
replacement command, producing three false barges. That failed artifact is
retained as `hil-no-false-barge-bilingual-boot31-safe-final.json`. Cue-only
confirmation now also requires a non-render control cue in the raw lane, and a
bare `I need` is not actionable.

`hil-no-false-barge-bilingual-boot31-final.json` is the current physical
safety regression. Stack-chan heard the intended English and Japanese sky-color
questions, generated substantive two-sentence answers through Eve/Qwen3 4B,
started and drained both real playbacks, animated 64/65 mouth transitions and
four blinks in each language, and recorded zero unexpected barges, dropped
frames, or starvation events. An experimental immediate single-probe path is
also intentionally absent because the speaker-only fixture made robot render
audio decode as high-confidence `Stop`. Voice-only cue-plus-replacement remains
open until the conservative path passes repeatably on current firmware.

An isolated English run did pass the full physical replacement path as
`hil-interruption-en-boot31-fast-preferred-safe-request.json`: the final
transcript was `I need a short joke instead`, the queue flush took 0.069 ms, and
the second answer completed with no drops or starvation. The later strict
bilingual run and an English repeat did not recover the complete replacement
request, so this is positive evidence for the logic but not a repeatable current
acceptance result.

## Boot-33 acknowledged interruption window and Eve regression (2026-08-08)

Boot 33 replaces the fixed cue-to-request delay with a correlated firmware
acknowledgement. After a corroborated Stop/Wait cue, the device ramps its speaker
to 0.05 gain (about 26 dB attenuation) over one 20 ms frame and returns
`playback.duck.state` with the request ID and applied gain. The server pauses new
outbound frames during this bounded continuation window and resumes them on a
rejected candidate; intentional queue drain while ducked is no longer counted as
a transport starvation.

`hil-interruption-en-boot33-naturalcue-paused-ackduck26-run1.json` passed the
physical English path: Stack-chan acknowledged the duck, captured `I need a short
joke instead`, dispatched its flush command in 0.060 ms, played the replacement response, and
reported no dropped/starved frames or extra barges. A repeat and the corresponding
Japanese run missed the initial control cue before the duck could open. Their raw
mixtures were already near full scale from robot render, so increasing digital mic
gain would amplify clipping rather than improve the near-end signal. The positive
interruption gate therefore remains partial until a closer human cue or a fixed
far-field fixture repeats both languages.

`hil-no-false-barge-bilingual-boot33-eve-final.json` passed the current safety,
intelligence, and face-animation regression. Eve/Qwen3 4B produced substantive
two-sentence English and Japanese explanations; the firmware recorded 59/80 mouth
transitions and 4/5 blinks, with zero unexpected barges, dropped frames, or
starvation events.

## Boot-34 correlated physical flush proof (2026-08-08)

A review found that the boot-33 `0.060 ms` value measured local command dispatch,
not physical ESP/I2S completion. Boot 34 closes that proof gap. Firmware now
returns `playback.flush.state` with the original request ID, success, post-flush
speaker state, and its own operation duration. A failed fast channel restart
rebuilds the duplex endpoint and cannot be reported as a successful stop. The
runtime also waits for the matching duck acknowledgement before enabling raw
continuation decoding.

`hil-interruption-en-boot34-correlated-physical-ack-run2.json` passed the strict
path. The device acknowledged the 26 dB duck in 20.583 ms, captured `I need a
short joke instead`, and returned the same flush request ID with a stopped
speaker. End-to-end command-to-ack physical flush latency was 7.485 ms; there
were zero extra barges, dropped frames, or starvation events. An immediately
preceding run missed the distant laptop cue before any duck request and remains
failed evidence of the acoustic-fixture limitation.

`hil-no-false-barge-bilingual-boot34-reviewed-final.json` then passed the current
speaker-only regression with substantive Eve/Qwen3 4B answers, 49/77 mouth
transitions, 3/5 blinks, zero false barges, and zero drops/starvation.

## Boot-34 reproducible bilingual interruption follow-up (2026-08-08)

The physical runner no longer depends on an external shell trap: it reads the
Mac output mute state, temporarily unmutes the acoustic fixture, and restores
the exact prior state in `finally`, including failed trials. The standalone
`hil-no-false-barge` task now makes the speaker-only safety gate reproducible
instead of relying on an ad-hoc runner.

Japanese preliminary probes use a 700 ms semantic window so the natural
`待ってください` cue is not split at the former 420 ms English boundary. After
a verified cue, a specialized large-model prompt disambiguates noisy command
words without affecting ordinary turns. The captured physical waveform that
the neutral prompt decoded as `条約を言って` decoded as `ジョークを言って` with
the bounded post-cue prompt. A second large Japanese cue decoder can corroborate
the small model before an immediate same-language stop; cross-language and
single-decoder cues retain the conservative ducked confirmation path.

`hil-interruption-ja-boot34-ja700-specialized-run1.json` passed the complete
Japanese physical path with `ジョークを言って`, an 837.5 ms post-barge STT,
7.708 ms correlated physical flush, a completed replacement response, and zero
drops/starvation. `hil-interruption-en-boot34-current-regression.json` passed
the current English path with the exact short-joke replacement and a 7.606 ms
physical flush. `hil-no-false-barge-bilingual-boot34-dual-model-current.json`
then passed English and Japanese speaker-only playback with zero false barges,
drops, or starvation.

The distant laptop fixture is still not repeatable: multiple adjacent Japanese
trials captured only Stack-chan's counting and never delivered the Mac cue to
either decoder. Those failures remain authoritative negative evidence. The
changes improve the captured-speech path and benchmark reproducibility, but do
not yet prove repeatable far-field Japanese interruption.

## Boot-30 head-sensor interruption fallback (2026-08-08)

Boot 30 adds a deliberately narrow physical fallback for stopping speech: a
strong contact across at least two Si12T zones must remain continuously present
for 700 ms. Firmware flushes the speaker queue locally, changes the face and
lights to listening feedback, and reports `interrupt_hold`; the server cancels
generation, clears echo-contaminated capture buffers, and records a correlated
`sensor_head_interrupt` barge event. Ordinary playback sensor gestures remain
suppressed so speaker and motor switching cannot launch reactions.

`hil-conversation-depth-en-boot30-sensor-soak.json` passed a complete real Eve
conversation with a 184-character answer, 59 speaking-mouth transitions, three
blinks, zero sensor events, zero unexpected barges, zero dropped frames, and
zero starvation. This is the required false-trigger regression. The first
positive run, `hil-sensor-interrupt-en-boot30.json`, observed physical playback
but no sensor transition before the reply ended; it is retained as a failed
artifact and does not prove that a person performed the hold. Voice-only
cue-plus-replacement interruption remains a separate open gate.

## Boot-29 microphone and speaking-face regression (2026-08-08)

Boot 29 dynamically reduces the ES7210 codec PGA from 33 dB to 27 dB only while
the speaker is active, then restores 33 dB for ordinary listening. The device
keeps 2x software gain, preserving far-field sensitivity while creating 6 dB of
pre-ADC double-talk headroom. Idle telemetry reported 38-48 RMS, 2x gain, 33 dB
codec gain, and zero clipped samples. Playback telemetry proved 27 dB codec
gain, zero dropped/starved frames, and concurrent face animation with 18 mouth
transitions plus one blink.

The direct interruption fixture file transcribes exactly at high confidence,
but the physical laptop-speaker/robot-speaker mixture still does not repeatedly
preserve the entire cue and replacement request. The server now decodes the raw
continuation in parallel after a validated cue and correctly permits a
same-language `Stop`/`Wait` cue to open the one bounded listening dip without
weakening the cross-language anti-hallucination threshold. Physical
interruption therefore remains an open quality gate, not a claimed pass.

## Latest boot-27 physical voice status (2026-08-08)

`hil-no-false-barge-bilingual-boot27-dual-source-final.json` is the latest
conservative-code playback regression. Both long English and Japanese answers
passed with four complete sentences, zero unexpected barges, zero dropped
frames, and zero starvation events. The physical face produced 119/133 mouth
transitions and 7/8 blinks while speaking. Cascade playback uses a 40-frame
start buffer; the speech-to-speech provider keeps the lower 16-frame start.

Current physical interruption is only partially proven. In
`hil-interruption-en-boot27-dual-source-live.json`, an AEC probe recovered a
stable cue and flushed the physical queue in 0.066 ms with no drops or
starvation, but the committed replacement was transcribed as `910`.
`hil-interruption-en-boot27-dual-source-preroll200.json` recovered `Hold on`,
confirmed the barge, and flushed in 6.923 ms, but residual counting again
dominated the replacement transcript. Other boot-27 runs missed the cue. New
source-capture runs proved that a robust raw window can recover `I need a short
joke instead`, but the physical result is not repeatable yet. The retained
implementation compares WebRTC-AEC, render-projected, and raw audio; lets a
weaker physically grounded cue open only one bounded listening window; rejects
cross-language cue-only authorization; commits the stream that actually supplied
the actionable request; uses a 200 ms onset pre-roll; and keeps strict onset-only
English/Japanese control grammar. A lower-safety immediate-flush experiment was
removed after it failed to produce a repeatable quality pass.

Therefore the older passing interruption soaks below remain historical
baselines. They do not override the open latest-firmware cue-plus-replacement
gate.

## Final production voice regression

`hil-soak-final-flush-first-5m.json` is the authoritative local-cascade HIL
result. It records 301.8 seconds and nine alternating English/Japanese acoustic
cases on the final custom firmware and server state.

| Signal | Result |
| --- | ---: |
| Fully passing cases | 9 / 9 |
| Semantic prompt + interrupt recognition | 100% |
| Completed replacement replies | 9 / 9 |
| Confirmed intended barges | 9 |
| False confirmed barges | 0 |
| Physical flush range | 0.30-1.82 ms |
| New playback drops / observed disconnects | 0 / 0 |

English STT ranged from 187-219 ms and Japanese STT from 1,379-1,489 ms.
Substantive response audio followed STT by 301-332 ms in English and 407-484
ms in Japanese. The Japanese lane uses resident `large-v3-turbo-q5_0`; the
base model remains the fast English result and language detector.

This result includes the final two-stage interruption state machine. Eighty
milliseconds of confident near-end speech ducks and pauses outgoing frames;
120 ms of post-duck speech confirms a barge. Confirmed barge turns use a 900 ms
silence endpoint so a natural pause after “Stop” / “ストップ” does not split
the replacement request. The immediate replacement reply has a one-response
refractory state, preventing expressive TTS or laughter from recursively
interrupting itself. The physical queue is flushed before inference/TTS
producer cleanup, which removed a measured 290 ms cancellation outlier.

The failed tuning artifacts are intentionally retained. The pre-refractory
`hil-soak-final-context-confirm120-div4-5m.json` exposed three recursive echo
barges. `hil-soak-final-refractory-5m.json` then reached 9/9 semantic cases,
zero false barges, zero drops, and stable transport, but correctly failed the
old under-50-ms flush gate because one flush waited 289.8 ms behind producer
cleanup. The final report above reran the complete five-minute physical suite
after fixing that ordering.

The five-minute run's original `boot_count` field used RTC memory and was not a
valid power-cycle detector. Transport stability in that artifact is supported
by zero observed WebSocket disconnects, not by that old counter. Firmware now
stores the counter in a separate metadata NVS namespace and a controlled reset
sequence proved `1 -> 2 -> 3` persistence without modifying factory Wi-Fi.

## Post sensor-initialization physical regression

`hil-post-sensor-fix-bilingual.json` reruns both physical acoustic interruption
cases on boot `3`, with `head_sensor_present` and register-verified
`head_sensor_ready` both true.

| Language | STT | Semantic audio after STT | Flush | Intent | False barges / drops |
| --- | ---: | ---: | ---: | --- | ---: |
| English | 206-214 ms | 345 ms | 0.289 ms | both turns recognized | 0 / 0 |
| Japanese | 1,385-1,535 ms | 473 ms | 0.667 ms | both turns recognized | 0 / 0 |

Both real speaker streams started and drained. The companion
`hil-routine-music-post-sensor-fix.json` recognized “dance with music,” emitted
44 original music frames, completed the head target at 5/0 raw error, and
dropped zero playback frames. This proves the Si12T initialization did not
regress the full-duplex voice or coordinated face/head/light/music paths.

## Immediate bilingual backchannel and gap-safe pacing

`hil-backchannel-pacing-fix-bilingual.json` is the latest passing physical
regression. It distinguishes the first audible acknowledgement from the first
substantive generated speech.

| Language | Physical first sound | Semantic TTS | STT | Flush | Recognition | New drops |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| English | 124 ms | 750 ms | 182-198 ms | 0.049 ms | WER 0.000 / 0.143 | 0 |
| Japanese | 33 ms | 1,095 ms | 777-779 ms | 0.147 ms | CER 0.120 / 0.222 | 0 |

Both replacement replies completed, both physical speakers started and
drained, and neither case produced a false barge-in. The acknowledgements are
the animated thinking state; semantic generation and speech rendering run concurrently
with reasoning rather than replacing the semantic answer.

An earlier tuning run exposed 13 newly dropped frames when semantic TTS arrived
after the cached acknowledgement. The server was incorrectly bursting frames
whose original pacing deadlines were in the past. The final implementation
rebases the segment clock after an inference gap, and the authoritative rerun
held the cumulative counter at 29→29.

## Exact-playback five-minute soak

`hil-soak-playback-state-5m.json` records 307.3 seconds, 11 alternating
bilingual physical cases, 11 completed replies, 11 confirmed interruptions,
zero false barges, zero disconnects or ESP resets, and no new playback drops
(16→16). Every case reported actual speaker start and drain edges.

Re-scoring the same transcripts under the later stricter normalized WER/CER
gate yields 9/11 passing cases. Both misses were Japanese double-talk captures;
transport remained correct, but this 81.8% strict recognition rate is not
claimed as a quality pass. The subsequent isolated bilingual regression above
passes the strict thresholds after reducing early duck time to 80 ms.

## Exact physical-playback regression

The final firmware reports the real speaker start and drain edges instead of
requiring the server to infer them from periodic telemetry. The two isolated
final artifacts are `hil-playback-state-en-final.json` and
`hil-playback-state-ja-final.json`.

| Language | STT | LLM first token | TTS first PCM | STT end to reply audio | Flush | New dropped frames |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| English | 170-208 ms | 292 ms | 346 ms | 839 ms | 0.059 ms | 0 |
| Japanese | 729-789 ms | 280 ms | 315 ms | 704 ms | 0.053 ms | 0 |

Both passed real acoustic recognition thresholds, observed two turns, confirmed
one intended interruption, reported physical speaker start and drain, completed
the replacement reply, and produced zero unexpected barge-ins. A separate
physical “dance with music” command yielded exactly one STT turn, proving the
music and reply were not decoded as a phantom user command.

## Verified motion and coordinated presets

The firmware's SCSCL implementation was corrected to the hardware protocol's
big-endian word order. Three read-only probes consistently identified yaw ID 1
at raw 461 and pitch ID 2 at raw 672, with limits 20-1003 and motor power
released. Auto-diagnosis now reports `motion_verified: true` at boot.

| Preset | Target yaw/pitch | Measured yaw/pitch | Absolute error |
| --- | --- | --- | --- |
| greet | 511 / 754 | 506 / 754 | 5 / 0 |
| celebrate | 402 / 732 | 408 / 732 | 6 / 0 |
| curious | 505 / 700 | 501 / 699 | 4 / 1 |
| comfort | 460 / 796 | 464 / 793 | 4 / 3 |
| dance | 390 / 732 | 395 / 733 | 5 / 1 |

Every routine completed inside the 24-raw-unit tolerance, then released servo
torque and motor power. Overlapping motion was rejected, and capacitive head
events are suppressed while motors are active so motor noise cannot recursively
start another routine.

## Physical bilingual interruption

| Case | STT | Barge flush | Recognition | False barge | Reply completed |
| --- | ---: | ---: | --- | --- | --- |
| English | 183-222 ms | 0.32 ms | preserved short-joke intent, WER 0.14 | no | yes |
| Japanese | 739-844 ms | 0.33 ms | preserved short-joke intent, CER 0.35 | no | yes |

English evidence is `hil-preroll-en.json`; Japanese evidence is
`hil-preroll-ja.json`. Both used physical Mac-speaker to robot-microphone audio,
automatic device VAD, real robot playback, and an interruption synchronized to
the device's `playback_active` telemetry.

The transport and cancellation path is comfortably within the interruption
target. Japanese recognition is now routed to resident
`large-v3-turbo-q5_0`; it is slower than small but recovered more intents in the
captured double-talk corpus. The final semantic-audio latency is reported in
the authoritative regression above.

This historical interruption path used an 80 ms early duck followed by post-flush
speech confirmation. Outgoing audio pauses rather than being discarded while
the confirmation runs, so an echo-only candidate resumes the existing reply.
A 400 ms cleaned pre-roll is committed only for a confirmed human interruption,
preserving the start of the utterance. Device boot diagnostics remained at boot
count 1 through both cases, confirming that interruption no longer replaces the
WebSocket transport or resets the ESP.

## Five-minute physical soak

`hil-soak-5m-drops.json` records 318.3 seconds and 13 alternating bilingual
cycles on the physical device. All 13 responses completed, 12/13 met the
recognition thresholds (92.3%), two cycles contained confirmed overlapping
barge-in, and there were zero false barges, WebSocket disconnect observations,
ESP boot-count changes, or playback-frame drops. Later cycles often became
ordinary follow-ups because the short bounded reply finished before macOS
started the interruption phrase; overlap coverage is therefore reported
separately rather than misclassified as a transport failure.

## Local Whisper comparison

| Model | English warm | Japanese warm | Decision |
| --- | ---: | ---: | --- |
| base q5_1 | 199-237 ms | 227-240 ms | fast language detector and English result |
| small q5_1 | ~280 ms captured warm | ~280 ms captured warm | one additional intent miss |
| large-v3-turbo q5_0 | ~1.15 s captured warm | ~1.15 s captured warm | selected Japanese result |

The captured 14-case Japanese comparison missed two joke intents with small
and one with large-v3-turbo. The adaptive router returns base immediately for
English and runs the resident large model only after base detects Japanese;
parallel Metal decoding was rejected because it slowed English substantially.

## Local TTS diffusion-step sweep

`tts-steps-latest.json` measures three repetitions per language and uses the
resident bilingual Whisper models as an intelligibility proxy for synthesized
audio. The input text, F1 voice, 1.08 speech speed, and 24 kHz output were held
constant.

| Supertonic steps | English first PCM / error | Japanese first PCM / error | Decision |
| ---: | ---: | ---: | --- |
| 1 | 262 ms / 1.000 | 267 ms / 1.000 | reject, unintelligible |
| 2 | 395 ms / 0.455 | 389 ms / 0.792 | reject, high error |
| 3 | 545 ms / 0.091 | 581 ms / 0.042 | selected |
| 5 | 906 ms / 0.091 | 935 ms / 0.014 | highest quality, too slow |

Three steps preserves strong bilingual round-trip recognition while removing
roughly 360 ms of median synthesis time versus five. It is now the local
cascade default; the physical HIL result below remains the authority for actual
robot-loop latency.

## Local conversation-model comparison

`llm-models-latest.json` repeats six English/Japanese joke, durable-memory, and
unconfirmed-motion cases three times per model. The quality gate checks output
language, required recalled facts, motion-completion safety wording, bounded
shape, and complete joke punchlines.

| Model | First token p50 | Contract pass rate | Decision |
| --- | ---: | ---: | --- |
| Qwen3 4B Instruct 2507 Q4_K_M | 207 ms | 100% | selected |
| Qwen3 1.7B Q4_K_M | 159 ms | 83.3% | reject for Japanese motion intent |

The 48 ms median gain from 1.7B does not justify the repeatable behavior loss.
The streaming response bound now treats a joke's first question mark as a setup
and continues through the punchline; this raised the 4B score from 83.3% to
100% without relaxing the ordinary one-sentence limit.

## Remaining gates

- Produce a repeatable boot-27 physical interruption that both flushes playback
  and preserves the complete replacement request without weakening the current
  bilingual false-trigger regressions.
- Reduce substantive generated-speech latency without lowering bilingual
  accuracy. Immediate physical acknowledgement is now 33-124 ms, while
  semantic speech is 750 ms English and 1,095 ms Japanese in the latest valid
  cases.
- Run `pixi run benchmark-realtime` and the physical bilingual HIL suite with a
  user-supplied API key. The GA protocol adapter, audio framing, bilingual
  transcript event, tool continuation, credential gate, and played-audio
  truncation are covered by deterministic tests, but hosted latency is not
  claimed without a live credentialed measurement.
- Evaluate a smaller Japanese model only if it can match the final physical
  double-talk intent accuracy; the current 1.38-1.49 second STT lane is the
  principal Japanese latency cost.

## Provider-boundary physical regression

After adding the native Realtime adapter, the local cascade server was restarted
from the updated source and the device reconnected without an ESP restart.
`hil-realtime-adapter-regression.json` passed both physical acoustic cases:

| Language | STT | Barge flush | First reply audio after STT | Result |
| --- | ---: | ---: | ---: | --- |
| English | 201-216 ms | 0.056 ms | 799 ms | passed |
| Japanese | 728-821 ms | 0.143 ms | 1,613 ms | passed |

Both cases observed real playback before interruption, two recognized turns,
one expected barge-in, and a completed reply. Device telemetry remained at boot
count 1 and zero cumulative playback-frame drops. This validates that the new
provider boundary did not regress the current custom firmware/cascade path; it
does not substitute for a credentialed hosted Realtime measurement.

## Tuned local physical latency

With three-step Supertonic and early word/clause phrase streaming enabled,
`hil-fast-phrase-streaming.json` measured the latest valid English case and
`hil-fast-phrase-streaming-ja-retry.json` measured the valid Japanese retry:

| Language | LLM first token | TTS first PCM | First reply audio after STT | Flush |
| --- | ---: | ---: | ---: | ---: |
| English | 291 ms | 327 ms | 822 ms | 0.189 ms |
| Japanese | 295 ms | 302 ms | 711 ms | 0.104 ms |

The first combined run's Japanese interrupt was acoustically misrecognized and
therefore correctly failed the quality gate; the immediate single-language
retry passed with the intended short-joke request. Both valid language cases
completed two turns with one expected interruption and no unexpected barge-in.
Boot count remained 1 and cumulative playback drops remained zero.

## Original face and concurrent routine regression

The original 3x4 cream-and-lavender face sheet is now the sole firmware face
source. Fixed 320x240 crops produced 13 embedded PNG assets (sleepy is reused as
the natural blink). Speaking frames are decoded once into PSRAM; the connected
device reported `face_speaking_cache: true`, 7,865,107 free PSRAM bytes, and
214,704 free internal heap bytes after boot.

`hil-final-original-face-motion-bilingual.json` passed the final physical
conversation regression on the cached raster renderer and asynchronous motion
firmware:

| Language | STT | Semantic audio after STT | Physical acknowledgement | Flush | New drops |
| --- | ---: | ---: | ---: | ---: | ---: |
| English | 190-209 ms | 793 ms | 20 ms | 0.223 ms | 0 |
| Japanese | 731-905 ms | 740 ms | 21 ms | 0.211 ms | 0 |

Both languages completed two turns with one intended interruption, no extra
barge-ins, real speaker start/drain edges, and zero new playback drops.

`hil-motion-overlap-en-v2.json` deliberately started a 1.2-second head move
after physical playback began. It passed with verified final position, one
intentional interruption, 0.139 ms flush, 751 ms semantic response latency,
and zero dropped frames. This validates that the 650 ms servo power warm-up no
longer blocks the firmware WebSocket loop.

`hil-dance-music-en.json` then exercised the actual voice-triggered combined
routine. Stack-chan recognized “dance with music,” emitted a 44-frame original
jingle on the observable PCM lane, ran the dance face/light/head preset,
verified the final servo target, observed physical speaker start and drain, and
added zero playback drops.

`hil-memory-latest.json` physically exercised temporary bilingual durable facts.
English stored `the memory test color is lavender`, retrieved that exact row,
and answered “The memory test color is lavender.” Japanese stored
`メモリーテストの色は紫だ`, retrieved that exact row for a natural unsegmented
question, and answered `紫です！`. Both cases passed; the benchmark then removed
only its test rows and a direct SQLite check confirmed none remained.

## Boot-18 grounded regression

`hil-soak-bilingual-90s-grounded-boot18.json` ran three alternating physical
English/Japanese prompt-and-interrupt cases for 97.96 seconds. All three intended
barges were confirmed, no unintended barge was accepted, the boot count stayed
at 18, and playback added zero dropped frames and zero starvation events.
Physical response start after final STT measured 1,349-1,510 ms.

`hil-voice-motion-latest.json` passed four acoustic commands: English left and
right, then Japanese up and center. Every dispatch and terminal completion had
the same per-command request ID, and every terminal result included verified
servo feedback with torque and power released. No playback drops or starvation
events were added.

`hil-routine-music-latest.json` recognized “Please play music and dance.” through
the physical microphone, emitted the 44-frame jingle, and returned a correlated
terminal success only after all motion steps and the LED-frame write were
verified. Device playback started and drained with zero new dropped frames.

The raster face renderer is exercised on the physical display path, including
cached speaking frames, but the automated HIL suite has no camera or framebuffer
oracle. Expression alignment and cuteness therefore remain a manual visual QA
claim rather than an automated pixel-level assertion.
