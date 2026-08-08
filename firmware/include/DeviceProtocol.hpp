#pragma once

#include <Arduino.h>

namespace stackchan {

constexpr uint8_t kProtocolVersion = 1;
constexpr size_t kAudioHeaderSize = 16;

enum class AudioStream : uint8_t {
  microphone = 1,
  speaker = 2,
};

enum AudioFlags : uint16_t {
  audio_none = 0,
  audio_start = 1,
  audio_end = 2,
  audio_cancelled = 4,
};

struct __attribute__((packed)) AudioHeader {
  char magic[4];
  uint8_t version;
  uint8_t stream;
  uint16_t flags;
  uint32_t sequence;
  uint32_t timestamp_ms;
};

static_assert(sizeof(AudioHeader) == kAudioHeaderSize);

inline bool validAudioHeader(const uint8_t* payload, size_t length) {
  if (length < sizeof(AudioHeader)) return false;
  const auto* header = reinterpret_cast<const AudioHeader*>(payload);
  return memcmp(header->magic, "STKA", 4) == 0 && header->version == kProtocolVersion;
}

inline size_t encodeAudioFrame(uint8_t* output, size_t capacity, AudioStream stream,
                               uint16_t flags, uint32_t sequence, uint32_t timestamp_ms,
                               const int16_t* pcm, size_t sample_count) {
  const size_t pcm_bytes = sample_count * sizeof(int16_t);
  if (capacity < sizeof(AudioHeader) + pcm_bytes) return 0;
  AudioHeader header{{'S', 'T', 'K', 'A'}, kProtocolVersion, static_cast<uint8_t>(stream),
                     flags, sequence, timestamp_ms};
  memcpy(output, &header, sizeof(header));
  memcpy(output + sizeof(header), pcm, pcm_bytes);
  return sizeof(header) + pcm_bytes;
}

}  // namespace stackchan
