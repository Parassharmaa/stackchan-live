#include "AudioEndpoint.hpp"

namespace stackchan {

namespace {

constexpr uint8_t kEs7210Address = 0x40;
// ES7210 MICx_GAIN is 0x10 | gain step. Steps 0x09 and 0x0B are 27 dB and
// 33 dB respectively. The board support defaults to 33 dB.
constexpr uint8_t kEs7210MicGainIdle = 0x1B;
constexpr uint8_t kEs7210MicGainPlayback = 0x19;
constexpr uint8_t kAw88298Address = 0x36;
constexpr uint8_t kAw9523Address = 0x58;

bool write8(uint8_t address, uint8_t reg, uint8_t value) {
  return M5.In_I2C.writeRegister(address, reg, &value, 1, 400000);
}

bool write16Be(uint8_t address, uint8_t reg, uint16_t value) {
  const uint8_t bytes[] = {
      static_cast<uint8_t>(value >> 8), static_cast<uint8_t>(value & 0xFF)};
  return M5.In_I2C.writeRegister(address, reg, bytes, sizeof(bytes), 400000);
}

}  // namespace

int16_t AudioEndpoint::saturate(int32_t value) {
  if (value > INT16_MAX) return INT16_MAX;
  if (value < INT16_MIN) return INT16_MIN;
  return static_cast<int16_t>(value);
}

void AudioEndpoint::setDucked(bool ducked, float gain) {
  playback_duck_gain_ = constrain(gain, 0.0f, 1.0f);
  playback_ducked_ = ducked;
}

bool AudioEndpoint::configureMicrophoneCodec() {
  // CoreS3 ES7210 configuration for the two onboard microphones. Keeping the
  // codec setup here lets TX and RX share one custom I2S controller instead of
  // letting two M5Unified endpoints repeatedly uninstall each other.
  struct RegisterValue {
    uint8_t reg;
    uint8_t value;
  };
  static constexpr RegisterValue registers[] = {
      {0x00, 0xFF}, {0x00, 0x41}, {0x01, 0x1F}, {0x06, 0x00},
      {0x07, 0x20}, {0x08, 0x10}, {0x09, 0x30}, {0x0A, 0x30},
      {0x20, 0x0A}, {0x21, 0x2A}, {0x22, 0x0A}, {0x23, 0x2A},
      {0x02, 0xC1}, {0x04, 0x01}, {0x05, 0x00}, {0x11, 0x60},
      {0x40, 0x42}, {0x41, 0x70}, {0x42, 0x70}, {0x43, kEs7210MicGainIdle},
      {0x44, kEs7210MicGainIdle}, {0x45, 0x00}, {0x46, 0x00}, {0x47, 0x00},
      {0x48, 0x00}, {0x49, 0x00}, {0x4A, 0x00}, {0x4B, 0x00},
      {0x4C, 0xFF}, {0x01, 0x14},
  };
  for (const auto& item : registers) {
    if (!write8(kEs7210Address, item.reg, item.value)) return false;
  }
  return true;
}

bool AudioEndpoint::setMicrophoneCodecGain(bool playback_active) {
  const uint8_t value = playback_active ? kEs7210MicGainPlayback
                                        : kEs7210MicGainIdle;
  if (!write8(kEs7210Address, 0x43, value) ||
      !write8(kEs7210Address, 0x44, value)) {
    Serial.println("audio: microphone codec gain write failed");
    return false;
  }
  microphone_codec_gain_db_ = playback_active ? 27 : 33;
  return true;
}

bool AudioEndpoint::configureSpeakerCodec(bool enabled) {
  if (!enabled) {
    const bool codec_ok = write16Be(kAw88298Address, 0x04, 0x4000);
    M5.In_I2C.bitOff(kAw9523Address, 0x02, 0b00000100, 400000);
    return codec_ok;
  }
  M5.In_I2C.bitOn(kAw9523Address, 0x02, 0b00000100, 400000);
  // 24 kHz, 16-bit stereo slots (32 BCLKs per frame), conservative volume.
  return write16Be(kAw88298Address, 0x61, 0x0673) &&
         write16Be(kAw88298Address, 0x04, 0x4040) &&
         write16Be(kAw88298Address, 0x05, 0x0008) &&
         write16Be(kAw88298Address, 0x06, 0x14C7) &&
         write16Be(kAw88298Address, 0x0C, 0x0164);
}

bool AudioEndpoint::beginDuplex() {
  M5.Mic.end();
  M5.Speaker.end();

  i2s_config_t config{};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_TX | I2S_MODE_RX);
  config.sample_rate = kDuplexRate;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  config.channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 8;
  config.dma_buf_len = kDuplexFramesPerPacket;
  config.use_apll = false;
  config.tx_desc_auto_clear = true;
  config.fixed_mclk = 0;
  config.mclk_multiple = I2S_MCLK_MULTIPLE_128;
  config.bits_per_chan = I2S_BITS_PER_CHAN_16BIT;

  i2s_pin_config_t pins{};
  pins.mck_io_num = GPIO_NUM_0;
  pins.bck_io_num = GPIO_NUM_34;
  pins.ws_io_num = GPIO_NUM_33;
  pins.data_out_num = GPIO_NUM_13;
  pins.data_in_num = GPIO_NUM_14;

  i2s_driver_uninstall(I2S_NUM_1);
  esp_err_t error = i2s_driver_install(I2S_NUM_1, &config, 0, nullptr);
  if (error == ESP_OK) error = i2s_set_pin(I2S_NUM_1, &pins);
  if (error != ESP_OK || !configureMicrophoneCodec() || !configureSpeakerCodec(true)) {
    i2s_driver_uninstall(I2S_NUM_1);
    configureSpeakerCodec(false);
    return false;
  }
  i2s_zero_dma_buffer(I2S_NUM_1);
  return true;
}

bool AudioEndpoint::restartDuplexChannels() {
  if (!duplex_ready_) return false;
  if (i2s_stop(I2S_NUM_1) != ESP_OK) return false;
  i2s_zero_dma_buffer(I2S_NUM_1);
  return i2s_start(I2S_NUM_1) == ESP_OK;
}

bool AudioEndpoint::begin() {
  duplex_ready_ = beginDuplex();
  if (duplex_ready_) {
    microphone_active_ = true;
    Serial.println("audio: custom 24 kHz full duplex ready");
    return true;
  }

  Serial.println("audio: duplex unavailable, using safe half-duplex fallback");
  auto mic_config = M5.Mic.config();
  mic_config.sample_rate = kInputRate;
  M5.Mic.config(mic_config);
  auto speaker_config = M5.Speaker.config();
  speaker_config.sample_rate = kOutputRate;
  M5.Speaker.config(speaker_config);
  // Leave acoustic headroom for a nearby human to interrupt while Stack-chan
  // is speaking. Higher gain saturated the CoreS3 microphone during double-talk.
  M5.Speaker.setVolume(160);
  microphone_active_ = M5.Mic.begin();
  Serial.printf("audio: microphone=%s speaker_available=%s\n",
                microphone_active_ ? "ready" : "failed",
                M5.Speaker.isEnabled() ? "yes" : "no");
  return microphone_active_;
}

bool AudioEndpoint::update() {
  if (duplex_ready_) {
    if (playback_active_) updateDuplexPlayback();
    if (playback_active_ && playback_response_open_ && !playback_end_received_ &&
        !playback_ducked_ &&
        playback_count_ == 0 && !playback_starvation_latched_ &&
        millis() - last_playback_write_ms_ >= 40) {
      playback_starvation_latched_ = true;
      ++playback_starvation_events_;
      Serial.printf("audio: playback starvation events=%u\n",
                    static_cast<unsigned>(playback_starvation_events_));
    }
    if (playback_active_ && playback_count_ == 0 &&
        millis() - last_playback_write_ms_ >= 80) {
      playback_active_ = false;
      setMicrophoneCodecGain(false);
      playback_response_open_ = false;
      playback_energy_ = 0.0f;
      return true;
    }
    return false;
  }
  if (!playback_active_) return false;

  if (playback_frame_started_ && !M5.Speaker.isPlaying()) {
    playback_head_ = (playback_head_ + 1) % kPlaybackQueueDepth;
    --playback_count_;
    playback_frame_started_ = false;
  }

  if (!playback_frame_started_ && playback_count_ > 0) {
    playback_frame_started_ = M5.Speaker.playRaw(
        playback_queue_[playback_head_], playback_lengths_[playback_head_],
        kOutputRate, false, 1, 0, false);
    if (!playback_frame_started_) Serial.println("audio: playRaw rejected frame");
  }

  if (!playback_frame_started_ && playback_count_ == 0) {
    M5.Speaker.end();
    playback_active_ = false;
    microphone_active_ = M5.Mic.begin();
    Serial.printf("audio: playback drained, microphone=%s\n",
                  microphone_active_ ? "ready" : "failed");
    return true;
  }
  return false;
}

void AudioEndpoint::captureFrame() {
  if (duplex_ready_) {
    captureDuplexFrame();
    return;
  }
  // M5Unified currently arbitrates the shared clock path. Capture pauses while
  // queued output plays; the direct duplex I2S backend replaces this after the
  // codec/AEC hardware benchmark.
  if (playback_active_) return;
  if (!connected_ || !microphone_active_) return;
  if (!M5.Mic.record(microphone_, kSamplesPerFrame, kInputRate)) return;
  const size_t length = encodeAudioFrame(encoded_, sizeof(encoded_), AudioStream::microphone,
                                         sequence_ == 0 ? audio_start : audio_none, sequence_++,
                                         millis(), microphone_, kSamplesPerFrame);
  if (length) socket_.sendBIN(encoded_, length);
}

void AudioEndpoint::captureDuplexFrame() {
  if (!connected_ || !microphone_active_) return;
  size_t bytes_read = 0;
  const esp_err_t error = i2s_read(
      I2S_NUM_1, duplex_input_, sizeof(duplex_input_), &bytes_read, 0);
  if (error != ESP_OK || bytes_read != sizeof(duplex_input_)) return;

  // 24 kHz stereo -> 16 kHz mono. ES7210 exposes the two physical microphones
  // as the left/right slots; average them, then linearly sample at the 2:3
  // output/input ratio. Idle speech retains the board support's 2x
  // magnification. During playback the codec PGA is reduced by 6 dB before the
  // ADC rather than lowering this already-digitized signal. A separately
  // validated preliminary cue may also duck speaker playback by 26 dB. Together
  // they prevent double-talk saturation while preserving the near-end level
  // reaching VAD.
  const int32_t capture_gain = 2;
  microphone_gain_x100_ = static_cast<int>(capture_gain * 100);
  microphone_clipped_samples_ = 0;
  for (size_t output = 0; output < kSamplesPerFrame; ++output) {
    const size_t base = (output * 3) / 2;
    const int32_t first =
        (static_cast<int32_t>(duplex_input_[base * 2]) +
         static_cast<int32_t>(duplex_input_[base * 2 + 1])) /
        2;
    int32_t sample = first;
    if (output & 1U) {
      const size_t next = min(base + 1, kDuplexFramesPerPacket - 1);
      const int32_t second =
          (static_cast<int32_t>(duplex_input_[next * 2]) +
           static_cast<int32_t>(duplex_input_[next * 2 + 1])) /
          2;
      sample = (first + second) / 2;
    }
    const int32_t amplified = sample * capture_gain;
    if (amplified > INT16_MAX || amplified < INT16_MIN) {
      ++microphone_clipped_samples_;
    }
    microphone_[output] = saturate(amplified);
  }
  uint64_t square_sum = 0;
  microphone_peak_ = 0;
  for (const int16_t sample : microphone_) {
    const int magnitude = abs(sample);
    microphone_peak_ = max(microphone_peak_, magnitude);
    square_sum += static_cast<int64_t>(sample) * sample;
  }
  microphone_rms_ = sqrtf(
      static_cast<float>(square_sum) / static_cast<float>(kSamplesPerFrame));
  const size_t length = encodeAudioFrame(
      encoded_, sizeof(encoded_), AudioStream::microphone,
      sequence_ == 0 ? audio_start : audio_none, sequence_++, millis(),
      microphone_, kSamplesPerFrame);
  if (length) socket_.sendBIN(encoded_, length);
}

void AudioEndpoint::updateDuplexPlayback() {
  if (playback_count_ == 0) return;
  const size_t samples = playback_lengths_[playback_head_];
  // A preliminary semantic cue gets one bounded listening window. Ramp across
  // a complete 20 ms frame so the user hears a smooth 26 dB dip instead of an
  // abrupt volume jump; confirmed barge-in still flushes the queue immediately.
  // Physical double-talk showed that 12 dB still let the robot's render dominate
  // the replacement request after a correctly recovered Stop/Wait cue.
  const float target_gain = playback_ducked_ ? playback_duck_gain_ : 1.0f;
  const float gain_step =
      (target_gain - playback_gain_) / static_cast<float>(max(samples, size_t{1}));
  for (size_t index = 0; index < samples; ++index) {
    playback_gain_ += gain_step;
    const int16_t value = saturate(
        static_cast<int32_t>(
            static_cast<float>(playback_queue_[playback_head_][index]) *
            playback_gain_));
    duplex_output_[index * 2] = value;
    duplex_output_[index * 2 + 1] = value;
  }
  size_t bytes_written = 0;
  const size_t requested = samples * 2 * sizeof(int16_t);
  const esp_err_t error = i2s_write(
      I2S_NUM_1, duplex_output_, requested, &bytes_written, 0);
  if (error != ESP_OK || bytes_written != requested) return;
  playback_head_ = (playback_head_ + 1) % kPlaybackQueueDepth;
  --playback_count_;
  last_playback_write_ms_ = millis();
}

void AudioEndpoint::playFrame(const uint8_t* payload, size_t length) {
  if (!validAudioHeader(payload, length)) return;
  const auto* header = reinterpret_cast<const AudioHeader*>(payload);
  if (header->stream != static_cast<uint8_t>(AudioStream::speaker)) return;
  const size_t pcm_bytes = length - sizeof(AudioHeader);
  if (pcm_bytes == 0 || pcm_bytes % sizeof(int16_t) != 0) return;
  const size_t samples = pcm_bytes / sizeof(int16_t);
  if (samples > kOutputSamplesPerFrame || playback_count_ >= kPlaybackQueueDepth) {
    ++dropped_playback_frames_;
    Serial.printf("audio: dropped frame samples=%u queued=%u\n",
                  static_cast<unsigned>(samples), static_cast<unsigned>(playback_count_));
    return;
  }
  if (header->flags & audio_start) {
    playback_response_open_ = true;
    playback_end_received_ = false;
    playback_starvation_latched_ = false;
    playback_response_high_water_ = 0;
  }
  if (header->flags & audio_end) playback_end_received_ = true;
  if (playback_count_ == 0) playback_starvation_latched_ = false;
  const auto* pcm = reinterpret_cast<const int16_t*>(payload + sizeof(AudioHeader));
  uint32_t absolute_sum = 0;
  for (size_t index = 0; index < samples; ++index) absolute_sum += abs(pcm[index]);
  playback_energy_ = constrain(
      static_cast<float>(absolute_sum) / static_cast<float>(samples) / 7000.0f,
      0.08f, 1.0f);
  memcpy(playback_queue_[playback_tail_], payload + sizeof(AudioHeader), pcm_bytes);
  playback_lengths_[playback_tail_] = samples;
  playback_tail_ = (playback_tail_ + 1) % kPlaybackQueueDepth;
  ++playback_count_;
  playback_queue_high_water_ = max(playback_queue_high_water_, playback_count_);
  playback_response_high_water_ = max(playback_response_high_water_, playback_count_);
  if (!playback_active_ &&
      (playback_count_ >= playback_start_frames_ || playback_end_received_)) {
    if (duplex_ready_) {
      setMicrophoneCodecGain(true);
      Serial.printf("audio: duplex speaker started buffered=%u\n",
                    static_cast<unsigned>(playback_count_));
    } else {
      if (microphone_active_) {
        M5.Mic.end();
        microphone_active_ = false;
      }
      if (!M5.Speaker.begin()) {
        Serial.println("audio: speaker begin failed");
        microphone_active_ = M5.Mic.begin();
        return;
      }
      Serial.println("audio: speaker started");
    }
    playback_active_ = true;
  }
}

bool AudioEndpoint::flush() {
  playback_ducked_ = false;
  playback_gain_ = 1.0f;
  bool endpoint_ready = true;
  if (duplex_ready_) {
    endpoint_ready = restartDuplexChannels();
    if (!endpoint_ready) {
      Serial.println("audio: duplex flush restart failed; rebuilding endpoint");
      duplex_ready_ = beginDuplex();
      endpoint_ready = duplex_ready_;
    }
    if (endpoint_ready) endpoint_ready = setMicrophoneCodecGain(false);
    playback_head_ = 0;
    playback_tail_ = 0;
    playback_count_ = 0;
    playback_frame_started_ = false;
    playback_energy_ = 0.0f;
    playback_active_ = false;
    playback_response_open_ = false;
    playback_end_received_ = false;
    playback_starvation_latched_ = false;
    last_playback_write_ms_ = 0;
    return endpoint_ready;
  }
  if (playback_active_) {
    M5.Speaker.stop();
    M5.Speaker.end();
  }
  playback_head_ = 0;
  playback_tail_ = 0;
  playback_count_ = 0;
  playback_frame_started_ = false;
  playback_response_open_ = false;
  playback_end_received_ = false;
  playback_starvation_latched_ = false;
  playback_energy_ = 0.0f;
  playback_active_ = false;
  if (!microphone_active_) microphone_active_ = M5.Mic.begin();
  return true;
}

}  // namespace stackchan
