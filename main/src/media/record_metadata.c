#include "record_metadata.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "cJSON.h"
#include "esp_log.h"

static const char *TAG = "day_record_meta";

static bool add_timing(cJSON *stream, const day_record_stream_summary_t *summary,
                       const char *first_name, const char *last_name)
{
    if (summary->timing_valid) {
        return cJSON_AddNumberToObject(stream, first_name, (double)summary->first_offset_us) &&
               cJSON_AddNumberToObject(stream, last_name, (double)summary->last_offset_us);
    }
    return cJSON_AddNullToObject(stream, first_name) && cJSON_AddNullToObject(stream, last_name);
}

static cJSON *build_metadata_json(const day_record_metadata_t *metadata)
{
    cJSON *root = cJSON_CreateObject();
    cJSON *streams = NULL;
    cJSON *video = NULL;
    cJSON *audio = NULL;
    cJSON *imu = NULL;
    if (!root ||
        !cJSON_AddNumberToObject(root, "schema_version", 2) ||
        !cJSON_AddStringToObject(root, "record_state", "complete") ||
        !cJSON_AddStringToObject(root, "capture_result",
                                metadata->video.error == ESP_OK && metadata->audio.error == ESP_OK &&
                                metadata->imu.error == ESP_OK ? "complete" : "partial_stream_failure") ||
        !cJSON_AddStringToObject(root, "record_id", metadata->record_id) ||
        !cJSON_AddStringToObject(root, "device_id", metadata->device_id) ||
        !cJSON_AddNumberToObject(root, "sequence", metadata->sequence) ||
        !cJSON_AddNumberToObject(root, "record_counter", (double)metadata->record_counter) ||
        !cJSON_AddStringToObject(root, "wake_reason", metadata->wake_reason) ||
        !cJSON_AddNumberToObject(root, "start_time_utc_ms", (double)metadata->start_time_utc_ms) ||
        !cJSON_AddStringToObject(root, "local_time", metadata->local_time) ||
        !cJSON_AddStringToObject(root, "timezone", metadata->timezone) ||
        !cJSON_AddStringToObject(root, "time_quality", metadata->time_quality) ||
        !cJSON_AddNumberToObject(root, "capture_epoch_monotonic_us",
                                (double)metadata->capture_epoch_monotonic_us) ||
        !cJSON_AddNumberToObject(root, "actual_duration_ms", metadata->actual_duration_ms) ||
        !cJSON_AddNumberToObject(root, "duration_s", (double)metadata->actual_duration_ms / 1000.0) ||
        !cJSON_AddNumberToObject(root, "start_us", (double)metadata->capture_epoch_monotonic_us) ||
        !cJSON_AddNumberToObject(root, "video_frames", metadata->video.item_count) ||
        !cJSON_AddNumberToObject(root, "audio_bytes", metadata->audio_bytes) ||
        !cJSON_AddNumberToObject(root, "imu_samples", metadata->imu.item_count) ||
        !cJSON_AddStringToObject(root, "camera_err", esp_err_to_name(metadata->video.error)) ||
        !cJSON_AddStringToObject(root, "audio_err", esp_err_to_name(metadata->audio.error)) ||
        !cJSON_AddStringToObject(root, "imu_err", esp_err_to_name(metadata->imu.error)) ||
        !(streams = cJSON_AddObjectToObject(root, "streams")) ||
        !(video = cJSON_AddObjectToObject(streams, "video")) ||
        !cJSON_AddStringToObject(video, "file", "video.avi") ||
        !cJSON_AddStringToObject(video, "container", "avi") ||
        !cJSON_AddStringToObject(video, "codec", "mjpeg") ||
        !cJSON_AddNumberToObject(video, "width", metadata->video_width) ||
        !cJSON_AddNumberToObject(video, "height", metadata->video_height) ||
        !cJSON_AddNumberToObject(video, "target_fps", metadata->video_target_fps) ||
        !cJSON_AddNumberToObject(video, "actual_fps", metadata->video_actual_fps) ||
        !cJSON_AddNumberToObject(video, "frame_count", metadata->video.item_count) ||
        !add_timing(video, &metadata->video, "first_frame_offset_us", "last_frame_offset_us") ||
        !cJSON_AddStringToObject(video, "error", esp_err_to_name(metadata->video.error)) ||
        !(audio = cJSON_AddObjectToObject(streams, "audio")) ||
        !cJSON_AddStringToObject(audio, "file", "audio.wav") ||
        !cJSON_AddNumberToObject(audio, "sample_rate_hz", metadata->audio_sample_rate_hz) ||
        !cJSON_AddNumberToObject(audio, "channels", 1) ||
        !cJSON_AddStringToObject(audio, "sample_format", "pcm_s16le") ||
        !cJSON_AddNumberToObject(audio, "sample_count", metadata->audio.item_count) ||
        !add_timing(audio, &metadata->audio, "first_sample_offset_us", "last_sample_offset_us") ||
        !cJSON_AddStringToObject(audio, "error", esp_err_to_name(metadata->audio.error)) ||
        !(imu = cJSON_AddObjectToObject(streams, "imu")) ||
        !cJSON_AddStringToObject(imu, "file", "imu.json") ||
        !cJSON_AddNumberToObject(imu, "sample_rate_hz", metadata->imu_sample_rate_hz) ||
        !cJSON_AddNumberToObject(imu, "sample_count", metadata->imu.item_count) ||
        !add_timing(imu, &metadata->imu, "first_sample_offset_us", "last_sample_offset_us") ||
        !cJSON_AddStringToObject(imu, "timestamp_unit", "us") ||
        !cJSON_AddStringToObject(imu, "error", esp_err_to_name(metadata->imu.error))) {
        cJSON_Delete(root);
        return NULL;
    }
    return root;
}

esp_err_t day_record_metadata_write_atomic(const char *path, const day_record_metadata_t *metadata)
{
    if (!path || !metadata) {
        return ESP_ERR_INVALID_ARG;
    }
    char temporary[160];
    int written = snprintf(temporary, sizeof(temporary), "%s.tmp", path);
    if (written < 0 || written >= (int)sizeof(temporary)) {
        return ESP_ERR_INVALID_SIZE;
    }
    cJSON *root = build_metadata_json(metadata);
    if (!root) {
        return ESP_ERR_NO_MEM;
    }
    char *json = cJSON_Print(root);
    cJSON_Delete(root);
    if (!json) {
        return ESP_ERR_NO_MEM;
    }
    FILE *file = fopen(temporary, "wb");
    if (!file) {
        free(json);
        return ESP_FAIL;
    }
    size_t len = strlen(json);
    esp_err_t result = fwrite(json, 1, len, file) == len ? ESP_OK : ESP_FAIL;
    free(json);
    if (result == ESP_OK && fflush(file) != 0) {
        result = ESP_FAIL;
    }
    if (result == ESP_OK && fsync(fileno(file)) != 0) {
        result = ESP_FAIL;
    }
    if (fclose(file) != 0) {
        result = ESP_FAIL;
    }
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "metadata write failed; preserving temporary file: errno=%d", errno);
        return result;
    }
    if (rename(temporary, path) != 0) {
        ESP_LOGE(TAG, "metadata rename failed; preserving temporary file: errno=%d", errno);
        return ESP_FAIL;
    }
    return ESP_OK;
}
