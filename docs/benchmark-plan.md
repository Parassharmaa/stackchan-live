# Benchmark plan

Every run writes machine-readable JSONL spans and a summary JSON.

`pixi run benchmark` measures each local cascade stage. With
`OPENAI_API_KEY` set, `pixi run benchmark-realtime` measures the native
speech-to-speech path on the exact same English and Japanese WAV fixtures and
records first audio, total turn time, transcripts, replies, and output duration.

## Latency metrics

- capture-to-server frame latency
- speech start detection latency
- speech end/endpointing latency
- first and final transcript latency
- first LLM token latency
- first TTS PCM latency
- first speakable-phrase TTS PCM latency
- first semantic TTS PCM latency
- first device playback latency
- end-to-end end-of-speech to first-audio latency
- barge-in to silent-speaker latency
- audio underruns and dropped frames
- STT, LLM, and TTS real-time factors

## Quality metrics

- Japanese character error rate and English word error rate
- code-switching accuracy
- semantic answer score
- voice naturalness listening score
- tool selection and argument accuracy
- durable-memory precision and recall
- interruption recovery correctness

## Initial gates

- first audible response p50 under 700 ms and p95 under 1,200 ms
- every audible answer must be attributable to the current semantic turn
- interruption stop p95 under 200 ms
- no playback underrun in a five-minute clean-Wi-Fi session
- English WER under 10% and Japanese CER under 12% on the project fixture set
- 100% safety-bound enforcement for motion commands

The gates are initial engineering targets and must be refined from measured hardware behavior.
