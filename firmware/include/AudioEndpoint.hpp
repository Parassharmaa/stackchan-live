#pragma once

#include <M5Unified.h>
#include <WebSocketsClient.h>
#include <driver/i2s.h>

#include "DeviceProtocol.hpp"

namespace stackchan {

enum class UiSoundEffect : uint8_t {
  agent_select,
  fast,
  assistant,
  approve,
  decline,
  mic_release,
  error,
};

class AudioEndpoint {
 public:
  explicit AudioEndpoint(WebSocketsClient& socket) : socket_(socket) {}
  bool begin();
  void setConnected(bool connected) { connected_ = connected; }
  void setConversationPaused(bool paused) { conversation_paused_ = paused; }
  bool conversationPaused() const { return conversation_paused_; }
  bool playbackActive() const { return playback_active_; }
  bool uiSoundActive() const { return ui_sound_active_; }
  void setDucked(bool ducked, float gain = 0.05f);
  float playbackDuckGain() const { return playback_duck_gain_; }
  void setPlaybackStartFrames(size_t frames) {
    playback_start_frames_ = constrain(frames, size_t{1}, kPlaybackQueueDepth);
  }
  bool duplexReady() const { return duplex_ready_; }
  float playbackEnergy() const { return playback_energy_; }
  float microphoneRms() const { return microphone_rms_; }
  float microphoneLeftRms() const { return microphone_left_rms_; }
  float microphoneRightRms() const { return microphone_right_rms_; }
  int microphonePeak() const { return microphone_peak_; }
  size_t microphoneClippedSamples() const {
    return microphone_clipped_samples_;
  }
  int microphoneGainX100() const { return microphone_gain_x100_; }
  int microphoneCodecGainDb() const { return microphone_codec_gain_db_; }
  uint32_t droppedPlaybackFrames() const { return dropped_playback_frames_; }
  size_t queuedPlaybackFrames() const { return playback_count_; }
  size_t playbackQueueHighWaterFrames() const { return playback_queue_high_water_; }
  size_t playbackResponseHighWaterFrames() const {
    return playback_response_high_water_;
  }
  size_t playbackStartFrames() const { return playback_start_frames_; }
  uint32_t playbackStarvationEvents() const { return playback_starvation_events_; }
  static constexpr size_t playbackQueueCapacityFrames() { return kPlaybackQueueDepth; }
  bool update();
  void captureFrame();
  void playFrame(const uint8_t* payload, size_t length);
  bool playUiSound(UiSoundEffect effect, uint8_t variant = 0);
  bool flush();

 private:
  static constexpr uint32_t kInputRate = 16000;
  static constexpr uint32_t kOutputRate = 24000;
  static constexpr uint32_t kDuplexRate = 24000;
  static constexpr size_t kSamplesPerFrame = kInputRate / 50;
  static constexpr size_t kOutputSamplesPerFrame = kOutputRate / 50;
  static constexpr size_t kDuplexFramesPerPacket = kDuplexRate / 50;
  // Streaming providers default to a low-latency 320 ms lead. A local cascade
  // can explicitly raise this before playback because its complete waveform
  // is available for an immediate burst.
  static constexpr size_t kDefaultPlaybackStartFrames = 16;
  // A 1.92 s queue absorbs occasional Wi-Fi/websocket bursts and slow sensor
  // I2C loops; barge-in flush still clears the queue immediately.
  static constexpr size_t kPlaybackQueueDepth = 96;
  bool beginDuplex();
  bool configureMicrophoneCodec();
  bool setMicrophoneCodecGain(bool playback_active);
  bool configureSpeakerCodec(bool enabled);
  bool restartDuplexChannels();
  void captureDuplexFrame();
  void updateDuplexPlayback();
  static int16_t saturate(int32_t value);
  WebSocketsClient& socket_;
  int16_t microphone_[kSamplesPerFrame]{};
  uint8_t encoded_[sizeof(AudioHeader) + sizeof(microphone_)]{};
  int16_t render_reference_[kSamplesPerFrame]{};
  uint8_t render_reference_encoded_[sizeof(AudioHeader) + sizeof(render_reference_)]{};
  int16_t duplex_input_[kDuplexFramesPerPacket * 2]{};
  int16_t duplex_output_[kDuplexFramesPerPacket * 2]{};
  int16_t playback_queue_[kPlaybackQueueDepth][kOutputSamplesPerFrame]{};
  size_t playback_lengths_[kPlaybackQueueDepth]{};
  size_t playback_head_ = 0;
  size_t playback_tail_ = 0;
  size_t playback_count_ = 0;
  size_t playback_queue_high_water_ = 0;
  size_t playback_response_high_water_ = 0;
  size_t playback_start_frames_ = kDefaultPlaybackStartFrames;
  bool playback_frame_started_ = false;
  bool playback_response_open_ = false;
  bool playback_end_received_ = false;
  bool playback_starvation_latched_ = false;
  uint32_t last_playback_write_ms_ = 0;
  uint32_t sequence_ = 0;
  uint32_t render_reference_sequence_ = 0;
  bool connected_ = false;
  bool conversation_paused_ = false;
  bool playback_active_ = false;
  bool ui_sound_active_ = false;
  bool playback_ducked_ = false;
  float playback_duck_gain_ = 0.05f;
  float playback_gain_ = 1.0f;
  bool microphone_active_ = false;
  bool duplex_ready_ = false;
  float playback_energy_ = 0.0f;
  float microphone_rms_ = 0.0f;
  float microphone_left_rms_ = 0.0f;
  float microphone_right_rms_ = 0.0f;
  int microphone_peak_ = 0;
  size_t microphone_clipped_samples_ = 0;
  int microphone_gain_x100_ = 200;
  int microphone_codec_gain_db_ = 33;
  uint32_t dropped_playback_frames_ = 0;
  uint32_t playback_starvation_events_ = 0;
};

}  // namespace stackchan
