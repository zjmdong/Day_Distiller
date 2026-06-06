#include "recorder.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "audio_service.h"
#include "avi_writer.h"
#include "camera_service.h"
#include "day_pins.h"
#include "imu.h"
#include "storage_service.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

#define REC_BIT_START BIT0
#define REC_BIT_STOP BIT1
#define REC_BIT_CAMERA_DONE BIT2
#define REC_BIT_AUDIO_DONE BIT3
#define REC_BIT_IMU_DONE BIT4
#define REC_BITS_DONE (REC_BIT_CAMERA_DONE | REC_BIT_AUDIO_DONE | REC_BIT_IMU_DONE)

static const char *TAG = "day_recorder";

typedef struct {
    EventGroupHandle_t events;
    const day_config_t *cfg;
    day_record_paths_t paths;
    int64_t start_us;
    uint32_t video_frames;
    uint32_t audio_bytes;
    uint32_t imu_samples;
    esp_err_t camera_err;
    esp_err_t audio_err;
    esp_err_t imu_err;
} record_ctx_t;

static volatile bool s_recording;
static esp_err_t s_last_error = ESP_OK;

static uint32_t sanitize_fps(uint32_t fps)
{
    return (fps >= 1 && fps <= 30) ? fps : 15;
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
    FILE *file = fopen(ctx->paths.video_path, "wb");
    if (!file) {
        ctx->camera_err = ESP_FAIL;
        xEventGroupSetBits(ctx->events, REC_BIT_CAMERA_DONE);
        vTaskDelete(NULL);
    }

    xEventGroupWaitBits(ctx->events, REC_BIT_START, false, true, portMAX_DELAY);
    day_camera_set_recording(true);

    uint32_t target_fps = sanitize_fps(ctx->cfg->camera_record_fps);
    int64_t frame_interval_us = 1000000LL / target_fps;
    day_avi_writer_t avi = {0};
    bool avi_started = false;
    while ((xEventGroupGetBits(ctx->events) & REC_BIT_STOP) == 0) {
        int64_t frame_start_us = esp_timer_get_time();
        camera_fb_t *fb = NULL;
        esp_err_t ret = day_camera_capture(&fb);
        if (ret != ESP_OK) {
            ctx->camera_err = ret;
            break;
        }
        if (!avi_started) {
            ret = day_avi_begin(&avi, file, fb->width, fb->height, target_fps);
            avi_started = ret == ESP_OK;
        }
        if (ret == ESP_OK) {
            ret = day_avi_write_frame(&avi, fb->buf, fb->len);
        }
        day_camera_return(fb);
        if (ret != ESP_OK) {
            ctx->camera_err = ret;
            break;
        }
        ctx->video_frames++;
        int64_t remain_us = frame_interval_us - (esp_timer_get_time() - frame_start_us);
        if (remain_us > 1000) {
            vTaskDelay(pdMS_TO_TICKS((uint32_t)(remain_us / 1000)));
        }
        day_camera_note_frame_done(frame_start_us, target_fps);
    }
    if (avi_started) {
        int64_t elapsed_us = DAY_RECORD_SECONDS * 1000000LL;
        uint32_t actual_frame_us = ctx->video_frames > 0 ?
                                   (uint32_t)((elapsed_us + ctx->video_frames / 2) / ctx->video_frames) :
                                   (1000000U / target_fps);
        if (actual_frame_us == 0) {
            actual_frame_us = 1000000U / target_fps;
        }
        day_avi_finish(&avi, actual_frame_us);
    }
    fclose(file);
    day_camera_set_recording(false);
    xEventGroupSetBits(ctx->events, REC_BIT_CAMERA_DONE);
    vTaskDelete(NULL);
}

static void audio_task(void *arg)
{
    record_ctx_t *ctx = arg;
    uint32_t sample_rate = ctx->cfg->audio_sample_rate_hz;
    size_t max_samples = (size_t)sample_rate * DAY_RECORD_SECONDS + sample_rate / 2;
    int16_t *pcm = heap_caps_malloc(max_samples * sizeof(int16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!pcm) {
        pcm = malloc(max_samples * sizeof(int16_t));
    }
    if (!pcm) {
        ctx->audio_err = ESP_ERR_NO_MEM;
        xEventGroupSetBits(ctx->events, REC_BIT_AUDIO_DONE);
        vTaskDelete(NULL);
    }

    size_t total_samples = 0;
    int16_t samples[256];
    xEventGroupWaitBits(ctx->events, REC_BIT_START, false, true, portMAX_DELAY);
    esp_err_t ret = day_audio_start(sample_rate);
    if (ret != ESP_OK) {
        ctx->audio_err = ret;
    }
    while (ret == ESP_OK && (xEventGroupGetBits(ctx->events) & REC_BIT_STOP) == 0 && total_samples < max_samples) {
        size_t got = 0;
        ret = day_audio_read_pcm16(samples, 256, &got, 100);
        if (ret == ESP_ERR_TIMEOUT) {
            ret = ESP_OK;
            continue;
        }
        if (ret == ESP_OK && got > 0) {
            if (got > max_samples - total_samples) {
                got = max_samples - total_samples;
            }
            memcpy(pcm + total_samples, samples, got * sizeof(int16_t));
            total_samples += got;
        }
    }
    day_audio_stop();
    if (ret == ESP_OK) {
        FILE *file = fopen(ctx->paths.audio_path, "wb");
        if (!file) {
            ret = ESP_FAIL;
        } else {
            uint32_t data_bytes = (uint32_t)(total_samples * sizeof(int16_t));
            day_audio_write_wav_header(file, sample_rate, data_bytes);
            size_t written = fwrite(pcm, sizeof(int16_t), total_samples, file);
            if (written != total_samples) {
                ret = ESP_FAIL;
            } else {
                ctx->audio_bytes = data_bytes;
            }
            fclose(file);
        }
    }
    free(pcm);
    ctx->audio_err = ret;
    xEventGroupSetBits(ctx->events, REC_BIT_AUDIO_DONE);
    vTaskDelete(NULL);
}

static void imu_task(void *arg)
{
    record_ctx_t *ctx = arg;
    uint32_t sample_rate = ctx->cfg->imu_sample_rate_hz ? ctx->cfg->imu_sample_rate_hz : 104;
    size_t max_samples = (size_t)sample_rate * DAY_RECORD_SECONDS + sample_rate;
    day_imu_sample_t *samples = heap_caps_malloc(max_samples * sizeof(day_imu_sample_t),
                                                 MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!samples) {
        samples = malloc(max_samples * sizeof(day_imu_sample_t));
    }
    if (!samples) {
        ctx->imu_err = ESP_ERR_NO_MEM;
        xEventGroupSetBits(ctx->events, REC_BIT_IMU_DONE);
        vTaskDelete(NULL);
    }

    uint32_t delay_ms = 1000 / sample_rate;
    if (delay_ms == 0) {
        delay_ms = 1;
    }
    TickType_t delay_ticks = pdMS_TO_TICKS(delay_ms);
    if (delay_ticks == 0) {
        delay_ticks = 1;
    }
    size_t sample_count = 0;
    esp_err_t ret = ESP_OK;

    xEventGroupWaitBits(ctx->events, REC_BIT_START, false, true, portMAX_DELAY);
    TickType_t last_wake = xTaskGetTickCount();
    while ((xEventGroupGetBits(ctx->events) & REC_BIT_STOP) == 0 && sample_count < max_samples) {
        day_imu_sample_t sample;
        ret = day_imu_read(&sample);
        if (ret != ESP_OK) {
            break;
        }
        sample.t_us -= ctx->start_us;
        samples[sample_count++] = sample;
        vTaskDelayUntil(&last_wake, delay_ticks);
    }
    if (ret == ESP_OK) {
        FILE *file = fopen(ctx->paths.imu_path, "w");
        if (!file) {
            ret = ESP_FAIL;
        } else {
            fprintf(file, "{\"sample_rate_hz\":%lu,\"samples\":[", (unsigned long)sample_rate);
            for (size_t i = 0; i < sample_count; ++i) {
                day_imu_write_json_sample(file, &samples[i], i == 0);
            }
            fprintf(file, "]}");
            fclose(file);
            ctx->imu_samples = (uint32_t)sample_count;
        }
    }
    free(samples);
    ctx->imu_err = ret;
    xEventGroupSetBits(ctx->events, REC_BIT_IMU_DONE);
    vTaskDelete(NULL);
}

static esp_err_t write_meta(const record_ctx_t *ctx)
{
    FILE *file = fopen(ctx->paths.meta_path, "w");
    if (!file) {
        return ESP_FAIL;
    }
    fprintf(file,
            "{\n"
            "  \"duration_s\":%d,\n"
            "  \"sequence\":%lu,\n"
            "  \"start_us\":%lld,\n"
            "  \"video_frames\":%lu,\n"
            "  \"audio_bytes\":%lu,\n"
            "  \"imu_samples\":%lu,\n"
            "  \"camera_err\":\"%s\",\n"
            "  \"audio_err\":\"%s\",\n"
            "  \"imu_err\":\"%s\"\n"
            "}\n",
            DAY_RECORD_SECONDS,
            (unsigned long)ctx->paths.sequence,
            (long long)ctx->start_us,
            (unsigned long)ctx->video_frames,
            (unsigned long)ctx->audio_bytes,
            (unsigned long)ctx->imu_samples,
            esp_err_to_name(ctx->camera_err),
            esp_err_to_name(ctx->audio_err),
            esp_err_to_name(ctx->imu_err));
    fclose(file);
    return ESP_OK;
}

static bool start_record_task(TaskFunction_t task, const char *name, uint32_t stack_words, UBaseType_t priority,
                              record_ctx_t *ctx, EventBits_t done_bit, esp_err_t *slot)
{
    BaseType_t ok = xTaskCreate(task, name, stack_words, ctx, priority, NULL);
    if (ok == pdPASS) {
        return true;
    }
    *slot = ESP_ERR_NO_MEM;
    xEventGroupSetBits(ctx->events, done_bit);
    return false;
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
        .camera_err = ESP_OK,
        .audio_err = ESP_OK,
        .imu_err = ESP_OK,
    };
    ctx.events = xEventGroupCreate();
    if (!ctx.events) {
        s_recording = false;
        return ESP_ERR_NO_MEM;
    }

    esp_err_t ret = day_storage_init();
    if (ret == ESP_OK) {
        ret = day_storage_make_record_paths(&ctx.paths, time(NULL));
    }
    if (ret == ESP_OK) {
        ctx.camera_err = day_camera_init(cfg);
        ctx.imu_err = day_imu_init(cfg->imu_sample_rate_hz);
        ctx.audio_err = day_audio_init(cfg->audio_sample_rate_hz);

        bool task_started = false;
        if (ctx.camera_err == ESP_OK) {
            day_camera_set_recording(true);
            task_started |= start_record_task(camera_task, "rec_camera", 6144, 5, &ctx,
                                              REC_BIT_CAMERA_DONE, &ctx.camera_err);
            if (ctx.camera_err != ESP_OK) {
                day_camera_set_recording(false);
            }
        } else {
            ESP_LOGW(TAG, "camera unavailable: %s", esp_err_to_name(ctx.camera_err));
            xEventGroupSetBits(ctx.events, REC_BIT_CAMERA_DONE);
        }
        if (ctx.audio_err == ESP_OK) {
            task_started |= start_record_task(audio_task, "rec_audio", 4096, 14, &ctx,
                                              REC_BIT_AUDIO_DONE, &ctx.audio_err);
        } else {
            ESP_LOGW(TAG, "audio unavailable: %s", esp_err_to_name(ctx.audio_err));
            xEventGroupSetBits(ctx.events, REC_BIT_AUDIO_DONE);
        }
        if (ctx.imu_err == ESP_OK) {
            task_started |= start_record_task(imu_task, "rec_imu", 4096, 12, &ctx,
                                              REC_BIT_IMU_DONE, &ctx.imu_err);
        } else {
            ESP_LOGW(TAG, "imu unavailable: %s", esp_err_to_name(ctx.imu_err));
            xEventGroupSetBits(ctx.events, REC_BIT_IMU_DONE);
        }

        ctx.start_us = esp_timer_get_time();
        if (task_started) {
            vTaskDelay(pdMS_TO_TICKS(120));
            xEventGroupSetBits(ctx.events, REC_BIT_START);
            vTaskDelay(pdMS_TO_TICKS(DAY_RECORD_SECONDS * 1000));
            xEventGroupSetBits(ctx.events, REC_BIT_STOP);
            xEventGroupWaitBits(ctx.events, REC_BITS_DONE, false, true, pdMS_TO_TICKS(8000));
        }
        write_meta(&ctx);
        if (out_paths) {
            *out_paths = ctx.paths;
        }
        if (ctx.camera_err != ESP_OK) {
            ret = ctx.camera_err;
        } else if (ctx.audio_err != ESP_OK) {
            ret = ctx.audio_err;
        } else {
            ret = ctx.imu_err;
        }
    }

    vEventGroupDelete(ctx.events);
    s_last_error = ret;
    s_recording = false;
    ESP_LOGI(TAG, "record finished: %s", esp_err_to_name(ret));
    return ret;
}
