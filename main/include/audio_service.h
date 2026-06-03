#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include "day_types.h"
#include "esp_err.h"

esp_err_t day_audio_init(uint32_t sample_rate_hz);
esp_err_t day_audio_start(uint32_t sample_rate_hz);
esp_err_t day_audio_read_pcm16(int16_t *samples, size_t sample_count, size_t *out_samples, uint32_t timeout_ms);
esp_err_t day_audio_stop(void);
void day_audio_deinit(void);
day_audio_status_t day_audio_get_status(void);

esp_err_t day_audio_write_wav_header(FILE *file, uint32_t sample_rate_hz, uint32_t data_bytes);
esp_err_t day_audio_patch_wav_header(FILE *file, uint32_t sample_rate_hz, uint32_t data_bytes);
