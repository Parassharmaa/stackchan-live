from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STACKCHAN_", env_file=PROJECT_ROOT / "server/.env"
    )

    host: str = "0.0.0.0"
    port: int = 8765
    log_level: str = "info"
    # The installed local stack is the production path. Mock mode must be an
    # explicit choice so a server restart cannot silently replace real speech.
    provider: Literal["mock", "cascade", "speech_to_speech"] = "cascade"
    # Eve owns durable intelligence; STT and TTS remain laptop-local.
    intelligence_backend: Literal["eve"] = "eve"
    eve_url: str = "http://127.0.0.1:2000"
    eve_model: str = "gpt-5.6-luna"
    eve_timeout_seconds: float = 90.0
    eve_approval_timeout_seconds: float = Field(default=30.0, ge=5.0, le=300.0)
    device_token: SecretStr | None = None
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("STACKCHAN_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    openai_realtime_url: str = "wss://api.openai.com/v1/realtime"
    openai_realtime_model: str = "gpt-realtime-2.1"
    openai_realtime_voice: str = "marin"
    openai_realtime_reasoning_effort: Literal["none", "low", "medium", "high"] = "low"
    openai_realtime_transcription_model: str = "gpt-4o-mini-transcribe"
    openai_realtime_max_output_tokens: int = 160
    openai_realtime_timeout_seconds: float = 20.0
    memory_path: Path = PROJECT_ROOT / "server/data/memory.sqlite3"
    schedule_path: Path = PROJECT_ROOT / "server/data/schedules.sqlite3"
    schedule_poll_seconds: float = Field(default=1.0, ge=0.1, le=30.0)
    schedule_retry_seconds: float = Field(default=60.0, ge=5.0, le=3600.0)
    memory_automatic_profiles: bool = True
    memory_episodic_enabled: bool = True
    memory_episode_retention_days: int = Field(default=30, ge=1, le=365)
    memory_episode_limit: int = Field(default=50, ge=1, le=500)
    trace_dir: Path = PROJECT_ROOT / "artifacts/benchmarks"
    trace_audio: bool = False
    input_sample_rate: int = 16_000
    output_sample_rate: int = 24_000
    audio_frame_ms: int = 20
    # MaAI is an optional, isolated attentive-listening sidecar. Its large ML
    # dependencies never enter the normal voice server process or environment.
    maai_enabled: bool = False
    maai_shadow_mode: bool = True
    maai_frame_rate: float = Field(default=10.0, ge=5.0, le=20.0)
    maai_backchannel_threshold: float = Field(default=0.72, ge=0.0, le=1.0)
    maai_nod_threshold: float = Field(default=0.68, ge=0.0, le=1.0)
    maai_backchannel_cooldown_ms: int = Field(default=2400, ge=500, le=15_000)
    maai_nod_cooldown_ms: int = Field(default=1800, ge=500, le=15_000)
    # Send a bounded initial burst so the device starts at its existing
    # 16-frame threshold but retains enough queued audio to survive a local
    # Whisper scheduling stall. Forty 20 ms frames remain well below the
    # firmware's 96-frame queue and do not add first-audio latency.
    playback_lead_ms: int = 800
    playback_cascade_start_frames: int = 40
    playback_realtime_start_frames: int = 16
    playback_tail_guard_ms: int = 320
    # One physical pat can be classified as hold -> swipe -> touch while the
    # hand leaves the six-zone capacitive sensor. Treat that burst as one
    # interaction so later classifications cannot cancel the first reaction.
    head_gesture_cooldown_ms: int = Field(default=2500, ge=500, le=10_000)
    aec_enabled: bool = True
    # The correlation peak includes render frames queued ahead of playback and
    # is not the WebRTC stream-delay value. Hardware A/B testing produced a
    # stronger median echo reduction at 160 ms than at 380 ms.
    aec_delay_ms: int = 160
    # Frames reach physical playback after the device lead buffer. A 500 ms
    # server-side guard discarded the first ~340 ms of an immediate human
    # interruption, so retain only a short AEC/bootstrap window.
    barge_in_guard_ms: int = 260
    # Four confident 20 ms near-end frames open an acoustically invisible
    # candidate. Preserve its onset, then gather enough continuous context for
    # Whisper to distinguish an explicit Stop/ストップ cue from self-echo.
    barge_in_duck_ms: int = 80
    # Once a cue is semantically corroborated, make room for the replacement
    # request. The firmware ramps to this gain over one 20 ms output frame.
    barge_in_duck_gain: float = Field(default=0.05, ge=0.0, le=1.0)
    # Far-field speech can cross the near-end gate near the end of a short
    # control phrase while Stack-chan is loud. Retain enough pre-trigger audio
    # to preserve the initial Stop/Wait word; semantic render-echo rejection,
    # rather than this acoustic buffer, still authorizes any duck or flush.
    barge_in_preroll_ms: int = 1200
    # The hidden candidate can begin on the robot's first loud frame, while a
    # person naturally starts speaking only after hearing playback. Keep the
    # initial un-ducked probe open long enough to include a complete short
    # Stop/Wait cue instead of decoding only its trailing consonants.
    barge_in_initial_probe_ms: int = 1100
    barge_in_probe_ms: int = 420
    # Japanese control phrases take longer to articulate than Stop/Wait. The
    # bilingual first probe uses this 700 ms window even during an English reply
    # so a cross-language 待ってください cue is not split at 420 ms.
    barge_in_probe_ja_ms: int = 700
    # The initial probe combines the pre-roll above with the post-trigger
    # capture. A 2.6 s cap preserves a complete far-field control phrase while
    # remaining short enough for the local bilingual Whisper verifier.
    barge_in_probe_window_ms: int = 2600
    # The raw lane is consulted only after an explicit Stop/Wait cue. Keep the
    # full short replacement utterance because a 1.6 s rolling window could
    # reduce "I need a short joke instead" to the non-actionable "I need" by
    # the time the local robust decoder became available.
    barge_in_raw_continuation_window_ms: int = 3200
    # A preliminary stop/wait cue may trigger one short listening dip. Rolling
    # probes must still produce a stable semantic confirmation before playback
    # is flushed.
    barge_in_probe_attempts: int = 4
    # The bilingual forced-language probes can contend for the same local model
    # and take just over one second on this Mac. The deadline includes the
    # longer initial cue window plus one cue-anchored continuation decode.
    barge_in_confirmation_timeout_ms: int = 6000
    # Preserve a natural pause after "Stop" / "ストップ" before the replacement
    # request. Normal non-barge turns retain the faster 560 ms endpoint.
    barge_in_silence_ms: int = 900
    barge_in_min_rms: int = 800
    barge_in_min_clean_ratio: float = 0.12
    barge_in_max_clean_ratio: float = 1.25
    barge_in_max_render_correlation: float = 0.92
    # With a post-gain physical render reference, echo-only frames are measured
    # much more accurately: failed hardware candidates stayed below a 0.44
    # clean/raw ratio while the confirmed English interruption reached 0.59.
    # Use that stronger evidence to keep echo from occupying the semantic
    # verifier exactly when a person begins speaking.
    barge_in_physical_min_clean_ratio: float = 0.50
    barge_in_physical_max_render_correlation: float = 0.65
    barge_in_physical_min_clean_rms: int = 1200
    barge_in_natural_max_render_correlation: float = 0.35
    barge_in_confidence_threshold: float = -0.60
    # Once a stable Stop/Wait cue has opened the bounded listening window, the
    # raw robust lane may contain only the beginning of the replacement request
    # in a rolling probe. It remains cue-anchored and render-echo checked, so a
    # small confidence margin improves retention without weakening initial
    # interruption authorization.
    barge_in_continuation_confidence_threshold: float = -0.70
    # A preliminary cue is allowed to open one ducked listening window at a
    # weaker confidence than is required to authorize a flush. This gives the
    # robust raw continuation lane enough context to recover the replacement
    # request without treating a noisy Stop/Wait decode as an interruption.
    barge_in_preliminary_cue_confidence_threshold: float = -1.00
    # Explicit words such as "Stop" are powerful cues, but the local decoder
    # can hallucinate them from motor/speaker noise. Require stronger physical
    # evidence than the general bilingual acceptance threshold.
    # This only opens the bounded listening window; it does not authorize a
    # flush. Physical double-talk produced a valid cue at -0.472, so leave
    # enough margin for that cue to receive a stable second semantic probe.
    barge_in_explicit_confidence_threshold: float = -0.50
    # Real close double-talk can correlate strongly with queued render audio
    # because the capture contains both signals. Content-level render rejection
    # plus a repeated control cue remains the decisive safety gate.
    barge_in_explicit_max_render_correlation: float = 0.80
    # Permit at most one acoustic listening window per spoken response. The
    # stricter correlation/clean-ratio gate prevents ordinary speaker echo from
    # repeatedly modulating playback while allowing nearby double-talk to make
    # its first control word audible to Whisper.
    barge_in_listening_max_render_correlation: float = 0.30
    barge_in_listening_min_clean_ratio: float = 0.55
    # Acoustic evidence may only attenuate playback; it can never flush it.
    # Hardware double-talk reached 17-22k RMS while low-correlation speaker-only
    # residuals stayed below 12k. Semantic cue + request checks still follow.
    # Normal conversation keeps uncued attenuation off: live speaker echo also
    # reached this range and produced the audible 26 dB dip reported by the user.
    # A decoded Stop/Wait cue can still open the semantic listening window.
    barge_in_acoustic_preduck_enabled: bool = False
    barge_in_early_listening_min_raw_rms: int = 12_000
    # A second-language decoder can hallucinate Stop/待って from quiet speaker
    # residuals. Real projected cross-language cues measured above 12k RMS;
    # retain conservative margin without affecting same-language control words.
    barge_in_cross_language_min_raw_rms: int = 6000
    whisper_cli: Path = Path("/opt/homebrew/bin/whisper-cli")
    whisper_server: Path = Path("/opt/homebrew/bin/whisper-server")
    whisper_server_host: str = "127.0.0.1"
    whisper_server_port: int = 8178
    whisper_threads: int = 8
    whisper_model: Path = PROJECT_ROOT / "artifacts/models/ggml-base-q5_1.bin"
    whisper_prompt: str = (
        "Stack-chan. Stop. Head, lights, dance, music, short joke. "
        "スタックちゃん。ストップ。頭、ライト、ダンス、音楽、短いジョーク。"
    )
    # Whisper treats these as prior transcript context. Keep them restricted to
    # playback-control words: including replacement intents such as "joke"
    # caused speaker echo to hallucinate an entire actionable request. A cue is
    # only preliminary; a second stable, non-render semantic probe must confirm
    # it before playback is flushed.
    whisper_barge_en_prompt: str = "Stop. Wait."
    whisper_barge_ja_prompt: str = "ストップ。待って。"
    # Used only after a validated playback-control cue. Keeping replacement
    # language out of the first-stage prompt prevents echo from hallucinating a
    # complete intent, while this context recovers the natural request from raw
    # double-talk.
    whisper_barge_continuation_en_prompt: str = (
        "Tell me. I need. A short joke instead."
    )
    # Applied only after a physical Japanese control cue has been verified.
    # The bounded command vocabulary disambiguates noisy double-talk (notably
    # ジョーク vs 条約) without biasing ordinary turns or authorizing a barge-in.
    whisper_barge_continuation_ja_prompt: str = (
        "待ってください。代わりに、ジョーク、音楽、頭、ライト。"
    )
    whisper_ja_server_port: int = 8181
    whisper_ja_fast_server_port: int = 8180
    whisper_ja_fast_model: Path = PROJECT_ROOT / "artifacts/models/ggml-small-q5_1.bin"
    whisper_ja_confidence_threshold: float = -0.18
    # Warm large-v3-turbo is ~1.15 s on this Mac. It is now the confidence
    # fallback for uncertain small-model turns rather than every Japanese turn.
    whisper_ja_model: Path = (
        PROJECT_ROOT / "artifacts/models/ggml-large-v3-turbo-q5_0.bin"
    )
    # Whisper treats this as prior transcript context, not an instruction. A
    # single natural barge-in phrase disambiguates ジョーク from 曲 in captured
    # double-talk without the command-list hallucinations caused by a long
    # vocabulary prompt.
    # A neutral wake-name hint improves proper-name recognition without biasing
    # silence or speaker echo toward an actionable phrase.
    whisper_ja_prompt: str = "スタックちゃん。"
    auto_turn_detection: bool = True
    # Idle hardware telemetry sits around 18-56 RMS. A 380 start threshold is
    # still over 6x that measured floor while allowing desk-distance speech
    # that the former 520 threshold could miss. Spectral speech-likeness and
    # consecutive-frame gates remain mandatory.
    vad_start_rms: int = 380
    vad_stop_rms: int = 220
    vad_start_ms: int = 160
    vad_silence_ms: int = 700
    vad_pre_roll_ms: int = 700
    vad_aggressiveness: int = 2
    # Servos are close to the microphone and can resemble voiced speech. During
    # a known motion window, retain barge-in for a nearby speaker but require a
    # stronger clean signal than ordinary turn start.
    motion_vad_start_rms: int = 1100
    motion_capture_tail_ms: int = 1_000
    supertonic_cli: Path = PROJECT_ROOT / ".pixi/envs/default/bin/supertonic"
    supertonic_host: str = "127.0.0.1"
    supertonic_port: int = 7788
    # F5 tied F1 on English and reached perfect Japanese round-trip recognition
    # in the local voice sweep while rendering faster across both languages.
    supertonic_voice: str = "F5"
    supertonic_steps: int = 5
    supertonic_speed: float = 1.08
    tts_voice_en: str = "Samantha"
    tts_voice_ja: str = "Kyoko"
