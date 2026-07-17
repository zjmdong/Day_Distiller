#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

typedef struct {
    esp_err_t error;
    bool timing_valid;
    int64_t first_offset_us;
    int64_t last_offset_us;
    uint32_t item_count;
} day_record_stream_summary_t;

typedef struct {
    char record_id[64];
    char device_id[16];
    char wake_reason[16];
    char local_time[40];
    char timezone[33];
    char time_quality[16];
    uint32_t sequence;
    uint64_t record_counter;
    int64_t start_time_utc_ms;
    int64_t capture_epoch_monotonic_us;
    uint32_t actual_duration_ms;

    uint32_t video_width;
    uint32_t video_height;
    uint32_t video_target_fps;
    float video_actual_fps;
    day_record_stream_summary_t video;

    uint32_t audio_sample_rate_hz;
    uint32_t audio_bytes;
    day_record_stream_summary_t audio;

    uint32_t imu_sample_rate_hz;
    day_record_stream_summary_t imu;
} day_record_metadata_t;

esp_err_t day_record_metadata_write_atomic(const char *path, const day_record_metadata_t *metadata);
