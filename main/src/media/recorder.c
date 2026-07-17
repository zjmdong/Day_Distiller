#include "recorder.h"

#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include "audio_service.h"
#include "avi_writer.h"
#include "camera_service.h"
#include "day_pins.h"
#include "device_identity.h"
#include "imu.h"
#include "record_metadata.h"
#include "rtc_clock.h"
#include "storage_service.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

#define REC_BIT_START BIT0
#define REC_BIT_STOP BIT1
#define REC_BIT_CAMERA_READY BIT2
#define REC_BIT_AUDIO_READY BIT3
#define REC_BIT_IMU_READY BIT4
#define REC_BIT_CAMERA_DONE BIT5
#define REC_BIT_AUDIO_DONE BIT6
#define REC_BIT_IMU_DONE BIT7
#define REC_BIT_CAMERA_CANCEL BIT8
#define REC_BIT_AUDIO_CANCEL BIT9
#define REC_BIT_IMU_CANCEL BIT10
#define REC_BITS_READY (REC_BIT_CAMERA_READY | REC_BIT_AUDIO_READY | REC_BIT_IMU_READY)
#define REC_BITS_DONE (REC_BIT_CAMERA_DONE | REC_BIT_AUDIO_DONE | REC_BIT_IMU_DONE)
#define REC_READY_TIMEOUT_MS 3000
#define REC_DONE_TIMEOUT_MS 8000
#define REC_STORAGE_RESERVE_BYTES (8ULL * 1024ULL * 1024ULL)

static const char *TAG = "day_recorder";

typedef struct {
    EventGroupHandle_t events;
    const day_config_t *cfg;
    day_record_paths_t paths;
    day_record_metadata_t metadata;
} record_ctx_t;

static volatile bool s_recording;
static esp_err_t s_last_error = ESP_OK;

static uint32_t sanitize_fps(uint32_t fps)
{
    return (fps >= 1 && fps <= 30) ? fps : 15;
}

static esp_err_t first_error(esp_err_t current, esp_err_t candidate)
{
    return current == ESP_OK ? candidate : current;
}

static esp_err_t flush_sync_close(FILE *file, esp_err_t result)
{
    if (!file) {
        return first_error(result, ESP_ERR_INVALID_ARG);
    }
    if (fflush(file) != 0) {
        result = first_error(result, ESP_FAIL);
    }
    if (fsync(fileno(file)) != 0) {
        result = first_error(result, ESP_FAIL);
    }
    if (fclose(file) != 0) {
        result = first_error(result, ESP_FAIL);
    }
    return result;
}

static bool control_cancelled(record_ctx_t *ctx, EventBits_t cancel_bit)
{
    EventBits_t bits = xEventGroupWaitBits(ctx->events, REC_BIT_START | cancel_bit,
                                           false, false, portMAX_DELAY);
    return (bits & cancel_bit) != 0;
}

bool day_recorder_is_active(void)
{
    return s_recording;
}

esp_err_t day_recorder_get_last_error(void)
{
    return s_last_error;
}

static void camera_task(void *arg)
{
    record_ctx_t *ctx = arg;
    esp_err_t result = ESP_OK;
    FILE *file = fopen(ctx->paths.video_path, "wb");
    if (!file) {
        result = ESP_FAIL;
        ctx->metadata.video.error = result;
        xEventGroupSetBits(ctx->events, REC_BIT_CAMERA_READY | REC_BIT_CAMERA_DONE);
        vTaskDelete(NULL);
        return;
    }
    xEventGroupSetBits(ctx->events, REC_BIT_CAMERA_READY);
    if (control_cancelled(ctx, REC_BIT_CAMERA_CANCEL)) {
        result = ESP_ERR_TIMEOUT;
    } else {
        day_camera_set_recording(true);
        uint32_t target_fps = ctx->metadata.video_target_fps;
        int64_t frame_interval_us = 1000000LL / target_fps;
        day_avi_writer_t avi = {0};
        bool avi_started = false;
        while ((xEventGroupGetBits(ctx->events) & REC_BIT_STOP) == 0) {
            int64_t frame_start_us = esp_timer_get_time();
            camera_fb_t *frame = NULL;
            result = day_camera_capture(&frame);
            if (result != ESP_OK) {
                break;
            }
            if (!avi_started) {
                result = day_avi_begin(&avi, file, frame->width, frame->height, target_fps);
                if (result == ESP_OK) {
                    avi_started = true;
                    ctx->metadata.video_width = frame->width;
                    ctx->metadata.video_height = frame->height;
                }
            }
            if (result == ESP_OK) {
                result = day_avi_write_frame(&avi, frame->buf, frame->len);
            }
            day_camera_return(frame);
            if (result != ESP_OK) {
                break;
            }
            int64_t frame_done_us = esp_timer_get_time();
            int64_t offset_us = frame_done_us - ctx->metadata.capture_epoch_monotonic_us;
            if (!ctx->metadata.video.timing_valid) {
                ctx->metadata.video.first_offset_us = offset_us;
                ctx->metadata.video.timing_valid = true;
            }
            ctx->metadata.video.last_offset_us = offset_us;
            ctx->metadata.video.item_count++;
            int64_t remain_us = frame_interval_us - (frame_done_us - frame_start_us);
            if (remain_us > 1000) {
                vTaskDelay(pdMS_TO_TICKS((uint32_t)(remain_us / 1000)));
            }
            day_camera_note_frame_done(frame_start_us, target_fps);
        }
        if (avi_started) {
            uint32_t actual_frame_us = 1000000U / target_fps;
            if (ctx->metadata.video.item_count > 1 &&
                ctx->metadata.video.last_offset_us > ctx->metadata.video.first_offset_us) {
                int64_t span = ctx->metadata.video.last_offset_us -
                               ctx->metadata.video.first_offset_us;
                actual_frame_us = (uint32_t)((span + (ctx->metadata.video.item_count - 1) / 2) /
                                             (ctx->metadata.video.item_count - 1));
                ctx->metadata.video_actual_fps = actual_frame_us > 0 ?
                                                 1000000.0f / actual_frame_us : 0.0f;
            }
            result = first_error(result, day_avi_finish(&avi, actual_frame_us));
        } else if (result == ESP_OK) {
            result = ESP_ERR_NOT_FOUND;
        }
        day_camera_set_recording(false);
    }
    result = flush_sync_close(file, result);
    if (ctx->metadata.video.error == ESP_OK) {
        ctx->metadata.video.error = result;
    }
    xEventGroupSetBits(ctx->events, REC_BIT_CAMERA_DONE);
    vTaskDelete(NULL);
}

static void audio_task(void *arg)
{
    record_ctx_t *ctx = arg;
    uint32_t sample_rate = ctx->metadata.audio_sample_rate_hz;
    esp_err_t result = ESP_OK;
    FILE *file = fopen(ctx->paths.audio_path, "wb+");
    if (!file) {
        result = ESP_FAIL;
        ctx->metadata.audio.error = result;
        xEventGroupSetBits(ctx->events, REC_BIT_AUDIO_READY | REC_BIT_AUDIO_DONE);
        vTaskDelete(NULL);
        return;
    }
    result = day_audio_write_wav_header(file, sample_rate, 0);
    xEventGroupSetBits(ctx->events, REC_BIT_AUDIO_READY);
    bool started = false;
    if (result == ESP_OK && !control_cancelled(ctx, REC_BIT_AUDIO_CANCEL)) {
        result = day_audio_start(sample_rate);
        started = result == ESP_OK;
        int16_t samples[256];
        while (result == ESP_OK && (xEventGroupGetBits(ctx->events) & REC_BIT_STOP) == 0) {
            size_t count = 0;
            result = day_audio_read_pcm16(samples, 256, &count, 100);
            if (result == ESP_ERR_TIMEOUT) {
                result = ESP_OK;
                continue;
            }
            if (result != ESP_OK || count == 0) {
                continue;
            }
            if (fwrite(samples, sizeof(samples[0]), count, file) != count) {
                result = ESP_FAIL;
                break;
            }
            int64_t read_done_us = esp_timer_get_time();
            int64_t chunk_us = (int64_t)count * 1000000LL / sample_rate;
            int64_t first_us = read_done_us - chunk_us;
            if (first_us < ctx->metadata.capture_epoch_monotonic_us) {
                first_us = ctx->metadata.capture_epoch_monotonic_us;
            }
            if (!ctx->metadata.audio.timing_valid) {
                ctx->metadata.audio.first_offset_us = first_us -
                                                       ctx->metadata.capture_epoch_monotonic_us;
                ctx->metadata.audio.timing_valid = true;
            }
            ctx->metadata.audio.last_offset_us = read_done_us -
                                                  ctx->metadata.capture_epoch_monotonic_us;
            ctx->metadata.audio.item_count += (uint32_t)count;
        }
    } else if (result == ESP_OK) {
        result = ESP_ERR_TIMEOUT;
    }
    if (started) {
        result = first_error(result, day_audio_stop());
    }
    ctx->metadata.audio_bytes = ctx->metadata.audio.item_count * sizeof(int16_t);
    result = first_error(result, day_audio_patch_wav_header(file, sample_rate,
                                                            ctx->metadata.audio_bytes));
    result = flush_sync_close(file, result);
    if (ctx->metadata.audio.error == ESP_OK) {
        ctx->metadata.audio.error = result;
    }
    xEventGroupSetBits(ctx->events, REC_BIT_AUDIO_DONE);
    vTaskDelete(NULL);
}

static void imu_task(void *arg)
{
    record_ctx_t *ctx = arg;
    uint32_t sample_rate = ctx->metadata.imu_sample_rate_hz;
    esp_err_t result = ESP_OK;
    FILE *file = fopen(ctx->paths.imu_path, "wb");
    if (!file) {
        result = ESP_FAIL;
        ctx->metadata.imu.error = result;
        xEventGroupSetBits(ctx->events, REC_BIT_IMU_READY | REC_BIT_IMU_DONE);
        vTaskDelete(NULL);
        return;
    }
    if (fprintf(file, "{\n  \"sample_rate_hz\":%lu,\n  \"samples\":[\n",
                (unsigned long)sample_rate) < 0) {
        result = ESP_FAIL;
    }
    xEventGroupSetBits(ctx->events, REC_BIT_IMU_READY);
    if (result == ESP_OK && !control_cancelled(ctx, REC_BIT_IMU_CANCEL)) {
        uint32_t delay_ms = 1000 / sample_rate;
        if (delay_ms == 0) {
            delay_ms = 1;
        }
        TickType_t delay_ticks = pdMS_TO_TICKS(delay_ms);
        if (delay_ticks == 0) {
            delay_ticks = 1;
        }
        TickType_t last_wake = xTaskGetTickCount();
        while ((xEventGroupGetBits(ctx->events) & REC_BIT_STOP) == 0) {
            day_imu_sample_t sample;
            result = day_imu_read(&sample);
            if (result != ESP_OK) {
                break;
            }
            sample.t_us -= ctx->metadata.capture_epoch_monotonic_us;
            if (sample.t_us < 0) {
                sample.t_us = 0;
            }
            if (!ctx->metadata.imu.timing_valid) {
                ctx->metadata.imu.first_offset_us = sample.t_us;
                ctx->metadata.imu.timing_valid = true;
            }
            ctx->metadata.imu.last_offset_us = sample.t_us;
            result = day_imu_write_json_sample(file, &sample,
                                               ctx->metadata.imu.item_count,
                                               ctx->metadata.imu.item_count == 0);
            if (result != ESP_OK) {
                break;
            }
            ctx->metadata.imu.item_count++;
            vTaskDelayUntil(&last_wake, delay_ticks);
        }
    } else if (result == ESP_OK) {
        result = ESP_ERR_TIMEOUT;
    }
    if (fprintf(file, "\n  ]\n}\n") < 0 || ferror(file)) {
        result = first_error(result, ESP_FAIL);
    }
    result = flush_sync_close(file, result);
    if (ctx->metadata.imu.error == ESP_OK) {
        ctx->metadata.imu.error = result;
    }
    xEventGroupSetBits(ctx->events, REC_BIT_IMU_DONE);
    vTaskDelete(NULL);
}

static bool start_record_task(TaskFunction_t task, const char *name, uint32_t stack_words,
                              UBaseType_t priority, record_ctx_t *ctx,
                              EventBits_t ready_bit, EventBits_t done_bit,
                              day_record_stream_summary_t *summary)
{
    if (xTaskCreate(task, name, stack_words, ctx, priority, NULL) == pdPASS) {
        return true;
    }
    summary->error = ESP_ERR_NO_MEM;
    xEventGroupSetBits(ctx->events, ready_bit | done_bit);
    return false;
}

static void resolution_for_framesize(int framesize, uint32_t *width, uint32_t *height)
{
    switch (framesize) {
    case 10:
        *width = 640;
        *height = 480;
        break;
    case 11:
        *width = 800;
        *height = 600;
        break;
    case 13:
        *width = 1280;
        *height = 720;
        break;
    case 15:
        *width = 1600;
        *height = 1200;
        break;
    default:
        *width = 1920;
        *height = 1080;
        break;
    }
}

static uint64_t estimated_record_bytes(const day_config_t *cfg)
{
    uint32_t width = 0;
    uint32_t height = 0;
    resolution_for_framesize(cfg->camera_record_framesize, &width, &height);
    uint64_t frames = (uint64_t)sanitize_fps(cfg->camera_record_fps) * DAY_RECORD_SECONDS + 2;
    uint64_t video = (uint64_t)width * height * frames;
    uint64_t audio = (uint64_t)cfg->audio_sample_rate_hz * sizeof(int16_t) * DAY_RECORD_SECONDS + 44;
    uint64_t imu = (uint64_t)cfg->imu_sample_rate_hz * DAY_RECORD_SECONDS * 320 + 1024;
    return video + audio + imu + REC_STORAGE_RESERVE_BYTES;
}

static void format_local_time(time_t record_time, char output[40])
{
    struct tm local;
    localtime_r(&record_time, &local);
    if (strftime(output, 40, "%Y-%m-%dT%H:%M:%S%z", &local) == 0) {
        strlcpy(output, "invalid", 40);
        return;
    }
    size_t len = strlen(output);
    if (len >= 5 && (output[len - 5] == '+' || output[len - 5] == '-')) {
        memmove(output + len - 1, output + len - 2, 3);
        output[len - 2] = ':';
    }
}

static const char *current_wake_reason(void)
{
    uint32_t causes = esp_sleep_get_wakeup_causes();
    if ((causes & (1UL << ESP_SLEEP_WAKEUP_TIMER)) != 0) {
        return "timer";
    }
    if ((causes & (1UL << ESP_SLEEP_WAKEUP_EXT1)) != 0) {
        return "shake";
    }
    return "manual";
}

static esp_err_t sync_or_create_plain(const char *path)
{
    struct stat st;
    const char *mode = "ab";
    if (stat(path, &st) == 0) {
        if (!S_ISREG(st.st_mode)) {
            return ESP_ERR_INVALID_STATE;
        }
    } else if (errno != ENOENT) {
        return ESP_FAIL;
    }
    FILE *file = fopen(path, mode);
    return file ? flush_sync_close(file, ESP_OK) : ESP_FAIL;
}

static esp_err_t sync_or_create_audio(const char *path, uint32_t sample_rate)
{
    struct stat st;
    if (stat(path, &st) == 0) {
        return S_ISREG(st.st_mode) ? sync_or_create_plain(path) : ESP_ERR_INVALID_STATE;
    } else if (errno != ENOENT) {
        return ESP_FAIL;
    }
    FILE *file = fopen(path, "wb");
    if (!file) {
        return ESP_FAIL;
    }
    esp_err_t result = day_audio_write_wav_header(file, sample_rate, 0);
    return flush_sync_close(file, result);
}

static esp_err_t sync_or_create_imu(const char *path, uint32_t sample_rate)
{
    struct stat st;
    if (stat(path, &st) == 0) {
        return S_ISREG(st.st_mode) ? sync_or_create_plain(path) : ESP_ERR_INVALID_STATE;
    } else if (errno != ENOENT) {
        return ESP_FAIL;
    }
    FILE *file = fopen(path, "wb");
    if (!file) {
        return ESP_FAIL;
    }
    esp_err_t result = fprintf(file, "{\n  \"sample_rate_hz\":%lu,\n  \"samples\":[]\n}\n",
                               (unsigned long)sample_rate) >= 0 ? ESP_OK : ESP_FAIL;
    return flush_sync_close(file, result);
}

static esp_err_t synchronize_media_files(record_ctx_t *ctx)
{
    esp_err_t result = sync_or_create_plain(ctx->paths.video_path);
    if (result == ESP_OK) {
        result = sync_or_create_audio(ctx->paths.audio_path, ctx->metadata.audio_sample_rate_hz);
    }
    if (result == ESP_OK) {
        result = sync_or_create_imu(ctx->paths.imu_path, ctx->metadata.imu_sample_rate_hz);
    }
    return result;
}

static void initialize_metadata(record_ctx_t *ctx, const day_rtc_status_t *rtc)
{
    day_record_metadata_t *metadata = &ctx->metadata;
    strlcpy(metadata->device_id, day_device_id(), sizeof(metadata->device_id));
    metadata->sequence = ctx->paths.sequence;
    metadata->record_counter = ctx->paths.record_counter;
    metadata->start_time_utc_ms = rtc->valid ? (int64_t)rtc->unix_time * 1000 : 0;
    snprintf(metadata->record_id, sizeof(metadata->record_id), "%s-%013" PRId64 "-%013" PRIu64,
             metadata->device_id, metadata->start_time_utc_ms, metadata->record_counter);
    strlcpy(metadata->wake_reason, current_wake_reason(), sizeof(metadata->wake_reason));
    format_local_time(ctx->paths.record_time, metadata->local_time);
    strlcpy(metadata->timezone, ctx->cfg->timezone[0] ? ctx->cfg->timezone : "CST-8",
            sizeof(metadata->timezone));
    strlcpy(metadata->time_quality, rtc->valid ? "rtc_valid" : "invalid",
            sizeof(metadata->time_quality));
    metadata->video_target_fps = sanitize_fps(ctx->cfg->camera_record_fps);
    metadata->audio_sample_rate_hz = ctx->cfg->audio_sample_rate_hz;
    metadata->imu_sample_rate_hz = ctx->cfg->imu_sample_rate_hz ?
                                   ctx->cfg->imu_sample_rate_hz : 104;
    metadata->video.error = ESP_OK;
    metadata->audio.error = ESP_OK;
    metadata->imu.error = ESP_OK;
}

static void mark_prepare_timeout(record_ctx_t *ctx, EventBits_t ready_bits)
{
    EventBits_t cancel = 0;
    if ((ready_bits & REC_BIT_CAMERA_READY) == 0) {
        ctx->metadata.video.error = ESP_ERR_TIMEOUT;
        cancel |= REC_BIT_CAMERA_CANCEL;
    }
    if ((ready_bits & REC_BIT_AUDIO_READY) == 0) {
        ctx->metadata.audio.error = ESP_ERR_TIMEOUT;
        cancel |= REC_BIT_AUDIO_CANCEL;
    }
    if ((ready_bits & REC_BIT_IMU_READY) == 0) {
        ctx->metadata.imu.error = ESP_ERR_TIMEOUT;
        cancel |= REC_BIT_IMU_CANCEL;
    }
    if (cancel) {
        ESP_LOGE(TAG, "record stream READY timeout bits=0x%lx", (unsigned long)ready_bits);
        xEventGroupSetBits(ctx->events, cancel);
    }
}

static void mark_completion_timeout(record_ctx_t *ctx, EventBits_t done_bits)
{
    EventBits_t cancel = 0;
    if ((done_bits & REC_BIT_CAMERA_DONE) == 0) {
        if (ctx->metadata.video.error == ESP_OK) {
            ctx->metadata.video.error = ESP_ERR_TIMEOUT;
        }
        cancel |= REC_BIT_CAMERA_CANCEL;
    }
    if ((done_bits & REC_BIT_AUDIO_DONE) == 0) {
        if (ctx->metadata.audio.error == ESP_OK) {
            ctx->metadata.audio.error = ESP_ERR_TIMEOUT;
        }
        cancel |= REC_BIT_AUDIO_CANCEL;
    }
    if ((done_bits & REC_BIT_IMU_DONE) == 0) {
        if (ctx->metadata.imu.error == ESP_OK) {
            ctx->metadata.imu.error = ESP_ERR_TIMEOUT;
        }
        cancel |= REC_BIT_IMU_CANCEL;
    }
    xEventGroupSetBits(ctx->events, cancel | REC_BIT_STOP);
}

static esp_err_t capture_result(const record_ctx_t *ctx)
{
    esp_err_t result = ctx->metadata.video.error;
    result = first_error(result, ctx->metadata.audio.error);
    return first_error(result, ctx->metadata.imu.error);
}

esp_err_t day_recorder_record_once(const day_config_t *cfg, day_record_paths_t *out_paths)
{
    if (!cfg) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_recording) {
        return ESP_ERR_INVALID_STATE;
    }
    s_recording = true;
    record_ctx_t ctx = {
        .cfg = cfg,
    };
    ctx.events = xEventGroupCreate();
    if (!ctx.events) {
        s_recording = false;
        return ESP_ERR_NO_MEM;
    }

    esp_err_t result = day_storage_init();
    if (result == ESP_OK) {
        result = day_storage_require_free_bytes(estimated_record_bytes(cfg));
    }
    day_rtc_status_t rtc = {0};
    time_t record_time = time(NULL);
    if (day_rtc_read(&rtc) == ESP_OK && rtc.valid && rtc.unix_time > 1609459200) {
        record_time = rtc.unix_time;
    } else {
        rtc.valid = false;
    }
    if (result == ESP_OK) {
        result = day_storage_make_record_paths(&ctx.paths, record_time);
    }
    if (result == ESP_OK) {
        initialize_metadata(&ctx, &rtc);
        ctx.metadata.video.error = day_camera_init_record(cfg);
        ctx.metadata.audio.error = day_audio_stop();
        if (ctx.metadata.audio.error == ESP_OK) {
            ctx.metadata.audio.error = day_audio_init(cfg->audio_sample_rate_hz);
        }
        ctx.metadata.imu.error = day_imu_init(ctx.metadata.imu_sample_rate_hz);

        bool task_started = false;
        if (ctx.metadata.video.error == ESP_OK) {
            task_started |= start_record_task(camera_task, "rec_camera", 6144, 5, &ctx,
                                              REC_BIT_CAMERA_READY, REC_BIT_CAMERA_DONE,
                                              &ctx.metadata.video);
        } else {
            ESP_LOGW(TAG, "camera unavailable: %s", esp_err_to_name(ctx.metadata.video.error));
            xEventGroupSetBits(ctx.events, REC_BIT_CAMERA_READY | REC_BIT_CAMERA_DONE);
        }
        if (ctx.metadata.audio.error == ESP_OK) {
            task_started |= start_record_task(audio_task, "rec_audio", 4096, 14, &ctx,
                                              REC_BIT_AUDIO_READY, REC_BIT_AUDIO_DONE,
                                              &ctx.metadata.audio);
        } else {
            ESP_LOGW(TAG, "audio unavailable: %s", esp_err_to_name(ctx.metadata.audio.error));
            xEventGroupSetBits(ctx.events, REC_BIT_AUDIO_READY | REC_BIT_AUDIO_DONE);
        }
        if (ctx.metadata.imu.error == ESP_OK) {
            task_started |= start_record_task(imu_task, "rec_imu", 4096, 12, &ctx,
                                              REC_BIT_IMU_READY, REC_BIT_IMU_DONE,
                                              &ctx.metadata.imu);
        } else {
            ESP_LOGW(TAG, "IMU unavailable: %s", esp_err_to_name(ctx.metadata.imu.error));
            xEventGroupSetBits(ctx.events, REC_BIT_IMU_READY | REC_BIT_IMU_DONE);
        }

        EventBits_t ready = xEventGroupWaitBits(ctx.events, REC_BITS_READY, false, true,
                                                pdMS_TO_TICKS(REC_READY_TIMEOUT_MS));
        if ((ready & REC_BITS_READY) != REC_BITS_READY) {
            mark_prepare_timeout(&ctx, ready);
        }
        ctx.metadata.capture_epoch_monotonic_us = esp_timer_get_time();
        xEventGroupSetBits(ctx.events, REC_BIT_START);
        EventBits_t before_capture = xEventGroupGetBits(ctx.events);
        if (task_started && (before_capture & REC_BITS_DONE) != REC_BITS_DONE) {
            vTaskDelay(pdMS_TO_TICKS(DAY_RECORD_SECONDS * 1000));
        }
        int64_t stop_us = esp_timer_get_time();
        xEventGroupSetBits(ctx.events, REC_BIT_STOP);
        EventBits_t done = xEventGroupWaitBits(ctx.events, REC_BITS_DONE, false, true,
                                               pdMS_TO_TICKS(REC_DONE_TIMEOUT_MS));
        if ((done & REC_BITS_DONE) != REC_BITS_DONE) {
            ESP_LOGE(TAG, "record stream completion timeout bits=0x%lx; preserving partial record",
                     (unsigned long)done);
            mark_completion_timeout(&ctx, done);
            (void)xEventGroupWaitBits(ctx.events, REC_BITS_DONE, false, true, portMAX_DELAY);
        }
        int64_t duration_us = stop_us - ctx.metadata.capture_epoch_monotonic_us;
        if (duration_us < 0) {
            duration_us = 0;
        }
        ctx.metadata.actual_duration_ms = duration_us > (int64_t)UINT32_MAX * 1000 ?
                                          UINT32_MAX : (uint32_t)((duration_us + 500) / 1000);

        result = synchronize_media_files(&ctx);
        if (result == ESP_OK) {
            result = day_record_metadata_write_atomic(ctx.paths.meta_path, &ctx.metadata);
        }
        if (result == ESP_OK) {
            result = day_storage_finalize_record(&ctx.paths);
        }
        if (result == ESP_OK) {
            result = capture_result(&ctx);
        }
    }

    if (out_paths) {
        *out_paths = ctx.paths;
    }
    vEventGroupDelete(ctx.events);
    s_last_error = result;
    s_recording = false;
    ESP_LOGI(TAG, "record finished: %s path=%s", esp_err_to_name(result),
             ctx.paths.dir_path[0] ? ctx.paths.dir_path : "none");
    return result;
}
