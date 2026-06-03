#include "recorder.h"

#include <stdio.h>
#include <string.h>
#include "audio_service.h"
#include "avi_writer.h"
#include "camera_service.h"
#include "day_pins.h"
#include "imu.h"
#include "storage_service.h"
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

    day_avi_writer_t avi = {0};
    bool avi_started = false;
    while ((xEventGroupGetBits(ctx->events) & REC_BIT_STOP) == 0) {
        camera_fb_t *fb = NULL;
        esp_err_t ret = day_camera_capture(&fb);
        if (ret != ESP_OK) {
            ctx->camera_err = ret;
            break;
        }
        if (!avi_started) {
            ret = day_avi_begin(&avi, file, fb->width, fb->height, 15);
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
    }
    if (avi_started) {
        day_avi_finish(&avi);
    }
    fclose(file);
    day_camera_set_recording(false);
    xEventGroupSetBits(ctx->events, REC_BIT_CAMERA_DONE);
    vTaskDelete(NULL);
}

static void audio_task(void *arg)
{
    record_ctx_t *ctx = arg;
    FILE *file = fopen(ctx->paths.audio_path, "wb+");
    if (!file) {
        ctx->audio_err = ESP_FAIL;
        xEventGroupSetBits(ctx->events, REC_BIT_AUDIO_DONE);
        vTaskDelete(NULL);
    }
    uint32_t sample_rate = ctx->cfg->audio_sample_rate_hz;
    day_audio_write_wav_header(file, sample_rate, 0);

    int16_t samples[256];
    xEventGroupWaitBits(ctx->events, REC_BIT_START, false, true, portMAX_DELAY);
    esp_err_t ret = day_audio_start(sample_rate);
    if (ret != ESP_OK) {
        ctx->audio_err = ret;
    }
    while (ret == ESP_OK && (xEventGroupGetBits(ctx->events) & REC_BIT_STOP) == 0) {
        size_t got = 0;
        ret = day_audio_read_pcm16(samples, 256, &got, 100);
        if (ret == ESP_OK && got > 0) {
            size_t written = fwrite(samples, sizeof(int16_t), got, file);
            ctx->audio_bytes += written * sizeof(int16_t);
            if (written != got) {
                ret = ESP_FAIL;
                break;
            }
        }
    }
    if (ret == ESP_ERR_TIMEOUT) {
        ret = ESP_OK;
    }
    day_audio_stop();
    day_audio_patch_wav_header(file, sample_rate, ctx->audio_bytes);
    fclose(file);
    ctx->audio_err = ret;
    xEventGroupSetBits(ctx->events, REC_BIT_AUDIO_DONE);
    vTaskDelete(NULL);
}

static void imu_task(void *arg)
{
    record_ctx_t *ctx = arg;
    FILE *file = fopen(ctx->paths.imu_path, "w");
    if (!file) {
        ctx->imu_err = ESP_FAIL;
        xEventGroupSetBits(ctx->events, REC_BIT_IMU_DONE);
        vTaskDelete(NULL);
    }
    fprintf(file, "{\"sample_rate_hz\":%lu,\"samples\":[", (unsigned long)ctx->cfg->imu_sample_rate_hz);
    bool first = true;
    uint32_t delay_ms = 1000 / (ctx->cfg->imu_sample_rate_hz ? ctx->cfg->imu_sample_rate_hz : 104);
    if (delay_ms == 0) {
        delay_ms = 1;
    }

    xEventGroupWaitBits(ctx->events, REC_BIT_START, false, true, portMAX_DELAY);
    while ((xEventGroupGetBits(ctx->events) & REC_BIT_STOP) == 0) {
        day_imu_sample_t sample;
        esp_err_t ret = day_imu_read(&sample);
        if (ret != ESP_OK) {
            ctx->imu_err = ret;
            break;
        }
        sample.t_us -= ctx->start_us;
        day_imu_write_json_sample(file, &sample, first);
        first = false;
        ctx->imu_samples++;
        vTaskDelay(pdMS_TO_TICKS(delay_ms));
    }
    fprintf(file, "]}");
    fclose(file);
    if (ctx->imu_err == ESP_OK || ctx->imu_err == 0) {
        ctx->imu_err = ESP_OK;
    }
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
            task_started |= start_record_task(camera_task, "rec_camera", 6144, 8, &ctx,
                                              REC_BIT_CAMERA_DONE, &ctx.camera_err);
            if (ctx.camera_err != ESP_OK) {
                day_camera_set_recording(false);
            }
        } else {
            ESP_LOGW(TAG, "camera unavailable: %s", esp_err_to_name(ctx.camera_err));
            xEventGroupSetBits(ctx.events, REC_BIT_CAMERA_DONE);
        }
        if (ctx.audio_err == ESP_OK) {
            task_started |= start_record_task(audio_task, "rec_audio", 4096, 8, &ctx,
                                              REC_BIT_AUDIO_DONE, &ctx.audio_err);
        } else {
            ESP_LOGW(TAG, "audio unavailable: %s", esp_err_to_name(ctx.audio_err));
            xEventGroupSetBits(ctx.events, REC_BIT_AUDIO_DONE);
        }
        if (ctx.imu_err == ESP_OK) {
            task_started |= start_record_task(imu_task, "rec_imu", 4096, 7, &ctx,
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
