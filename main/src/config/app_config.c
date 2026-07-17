#include "app_config.h"

#include <string.h>
#include "day_pins.h"
#define DEFAULT_NTP_SERVER "ntp1.aliyun.com"
#define DEFAULT_TIMEZONE "CST-8"

uint32_t day_config_camera_max_fps(int framesize)
{
    if (framesize <= 10) { /* VGA and below */
        return 30;
    }
    if (framesize <= 11) { /* SVGA */
        return 20;
    }
    if (framesize <= 13) { /* XGA/HD */
        return 15;
    }
    if (framesize <= 15) { /* SXGA/UXGA */
        return 10;
    }
    if (framesize == 16) { /* FHD */
        return 15;
    }
    return 5;
}

static bool is_preview_framesize_valid(int framesize)
{
    return framesize == 6 ||  /* FRAMESIZE_QVGA */
           framesize == 10 || /* FRAMESIZE_VGA */
           framesize == 11 || /* FRAMESIZE_SVGA */
           framesize == 13;   /* FRAMESIZE_HD */
}

bool day_config_record_framesize_valid(int framesize)
{
    return framesize == 10 || /* FRAMESIZE_VGA */
           framesize == 11 || /* FRAMESIZE_SVGA */
           framesize == 13 || /* FRAMESIZE_HD */
           framesize == 15 || /* FRAMESIZE_UXGA */
           framesize == 16;   /* FRAMESIZE_FHD */
}

bool day_config_record_fps_valid(uint32_t fps)
{
    return fps == 5 || fps == 10 || fps == 12 || fps == 15 ||
           fps == 20 || fps == 24 || fps == 30;
}

void day_config_defaults(day_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->auto_record_enabled = true;
    cfg->wake_interval_sec = DAY_DEFAULT_WAKE_INTERVAL_SEC;
    cfg->shake_trigger_enabled = false;
    cfg->camera_framesize = 16; /* Legacy FRAMESIZE_FHD in esp32-camera */
    cfg->camera_preview_framesize = 10; /* FRAMESIZE_VGA */
    cfg->camera_record_framesize = 16; /* FRAMESIZE_FHD */
    cfg->camera_jpeg_quality = 12;
    cfg->camera_preview_fps = 30;
    cfg->camera_record_fps = 15;
    cfg->audio_sample_rate_hz = 16000;
    cfg->imu_sample_rate_hz = 104;
    cfg->imu_orientation = 0;
    cfg->low_battery_percent = 20;
    strlcpy(cfg->ntp_server, DEFAULT_NTP_SERVER, sizeof(cfg->ntp_server));
    strlcpy(cfg->timezone, DEFAULT_TIMEZONE, sizeof(cfg->timezone));
    cfg->led_brightness_percent = DAY_LED_DEFAULT_BRIGHTNESS_PERCENT;
    cfg->led_recording_r = DAY_LED_DEFAULT_RECORDING_R;
    cfg->led_recording_g = DAY_LED_DEFAULT_RECORDING_G;
    cfg->led_recording_b = DAY_LED_DEFAULT_RECORDING_B;
}

void day_config_normalize(day_config_t *cfg)
{
    if (!cfg) {
        return;
    }
    if (cfg->wake_interval_sec < 60 || cfg->wake_interval_sec > 86400) {
        cfg->wake_interval_sec = DAY_DEFAULT_WAKE_INTERVAL_SEC;
    }
    if (cfg->camera_framesize < 0 || cfg->camera_framesize > 23) {
        cfg->camera_framesize = 16;
    }
    if (cfg->camera_preview_framesize <= 0) {
        cfg->camera_preview_framesize = 10;
    }
    if (cfg->camera_record_framesize <= 0) {
        cfg->camera_record_framesize = cfg->camera_framesize;
    }
    if (!is_preview_framesize_valid(cfg->camera_preview_framesize)) {
        cfg->camera_preview_framesize = 10;
    }
    if (!day_config_record_framesize_valid(cfg->camera_record_framesize)) {
        cfg->camera_record_framesize = 16;
    }
    if (cfg->camera_jpeg_quality < 4 || cfg->camera_jpeg_quality > 63) {
        cfg->camera_jpeg_quality = 12;
    }
    uint32_t preview_max_fps = day_config_camera_max_fps(cfg->camera_preview_framesize);
    uint32_t record_max_fps = day_config_camera_max_fps(cfg->camera_record_framesize);
    if (cfg->camera_preview_fps < 1) {
        cfg->camera_preview_fps = preview_max_fps;
    }
    if (cfg->camera_preview_fps > preview_max_fps) {
        cfg->camera_preview_fps = preview_max_fps;
    }
    if (!day_config_record_fps_valid(cfg->camera_record_fps) ||
        cfg->camera_record_fps > record_max_fps) {
        cfg->camera_record_fps = record_max_fps >= 15 ? 15 : 10;
    }
    if (cfg->audio_sample_rate_hz < 8000 || cfg->audio_sample_rate_hz > 48000) {
        cfg->audio_sample_rate_hz = 16000;
    }
    if (cfg->imu_sample_rate_hz == 0 || cfg->imu_sample_rate_hz > 833) {
        cfg->imu_sample_rate_hz = 104;
    }
    if (cfg->imu_orientation > 5) {
        cfg->imu_orientation = 0;
    }
    if (cfg->low_battery_percent < 5 || cfg->low_battery_percent >= 40) {
        cfg->low_battery_percent = 20;
    }
    if (cfg->ntp_server[0] == '\0') {
        strlcpy(cfg->ntp_server, DEFAULT_NTP_SERVER, sizeof(cfg->ntp_server));
    }
    if (cfg->timezone[0] == '\0') {
        strlcpy(cfg->timezone, DEFAULT_TIMEZONE, sizeof(cfg->timezone));
    }
    if (cfg->led_brightness_percent < 5 || cfg->led_brightness_percent > 100) {
        cfg->led_brightness_percent = DAY_LED_DEFAULT_BRIGHTNESS_PERCENT;
    }
    if (cfg->led_recording_r == 0 && cfg->led_recording_g == 0 && cfg->led_recording_b == 0) {
        cfg->led_recording_r = DAY_LED_DEFAULT_RECORDING_R;
        cfg->led_recording_g = DAY_LED_DEFAULT_RECORDING_G;
        cfg->led_recording_b = DAY_LED_DEFAULT_RECORDING_B;
    }
}
