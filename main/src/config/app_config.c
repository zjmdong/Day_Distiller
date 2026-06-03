#include "app_config.h"

#include <string.h>
#include "day_pins.h"
#include "nvs.h"
#include "nvs_flash.h"

#define CFG_NS "day_cfg"

void day_config_defaults(day_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->auto_record_enabled = true;
    cfg->wake_interval_sec = DAY_DEFAULT_WAKE_INTERVAL_SEC;
    cfg->shake_trigger_enabled = false;
    cfg->camera_framesize = 13; /* FRAMESIZE_FHD in esp32-camera */
    cfg->camera_jpeg_quality = 12;
    cfg->audio_sample_rate_hz = 16000;
    cfg->imu_sample_rate_hz = 104;
    cfg->low_battery_percent = 20;
}

static void read_u8(nvs_handle_t nvs, const char *key, uint8_t *value)
{
    uint8_t tmp = *value;
    if (nvs_get_u8(nvs, key, &tmp) == ESP_OK) {
        *value = tmp;
    }
}

static void read_u32(nvs_handle_t nvs, const char *key, uint32_t *value)
{
    uint32_t tmp = *value;
    if (nvs_get_u32(nvs, key, &tmp) == ESP_OK) {
        *value = tmp;
    }
}

static void read_i32(nvs_handle_t nvs, const char *key, int *value)
{
    int32_t tmp = *value;
    if (nvs_get_i32(nvs, key, &tmp) == ESP_OK) {
        *value = tmp;
    }
}

static void read_string(nvs_handle_t nvs, const char *key, char *value, size_t max_len)
{
    size_t len = max_len;
    if (nvs_get_str(nvs, key, value, &len) != ESP_OK) {
        value[0] = '\0';
    }
}

esp_err_t day_config_load(day_config_t *cfg)
{
    if (!cfg) {
        return ESP_ERR_INVALID_ARG;
    }
    day_config_defaults(cfg);

    nvs_handle_t nvs;
    esp_err_t ret = nvs_open(CFG_NS, NVS_READWRITE, &nvs);
    if (ret != ESP_OK) {
        return ret;
    }

    uint8_t b = cfg->auto_record_enabled;
    read_u8(nvs, "auto_rec", &b);
    cfg->auto_record_enabled = b;
    b = cfg->shake_trigger_enabled;
    read_u8(nvs, "shake", &b);
    cfg->shake_trigger_enabled = b;

    read_u32(nvs, "wake_s", &cfg->wake_interval_sec);
    read_i32(nvs, "cam_fs", &cfg->camera_framesize);
    read_i32(nvs, "cam_q", &cfg->camera_jpeg_quality);
    read_u32(nvs, "aud_rate", &cfg->audio_sample_rate_hz);
    read_u32(nvs, "imu_rate", &cfg->imu_sample_rate_hz);
    read_u8(nvs, "low_batt", &cfg->low_battery_percent);
    read_string(nvs, "ssid", cfg->wifi_ssid, sizeof(cfg->wifi_ssid));
    read_string(nvs, "pass", cfg->wifi_password, sizeof(cfg->wifi_password));

    if (cfg->wake_interval_sec < 30) {
        cfg->wake_interval_sec = DAY_DEFAULT_WAKE_INTERVAL_SEC;
    }
    if (cfg->camera_jpeg_quality < 4 || cfg->camera_jpeg_quality > 63) {
        cfg->camera_jpeg_quality = 12;
    }
    if (cfg->audio_sample_rate_hz < 8000 || cfg->audio_sample_rate_hz > 48000) {
        cfg->audio_sample_rate_hz = 16000;
    }
    if (cfg->imu_sample_rate_hz == 0 || cfg->imu_sample_rate_hz > 833) {
        cfg->imu_sample_rate_hz = 104;
    }
    nvs_close(nvs);
    return ESP_OK;
}

esp_err_t day_config_save(const day_config_t *cfg)
{
    if (!cfg) {
        return ESP_ERR_INVALID_ARG;
    }
    nvs_handle_t nvs;
    esp_err_t ret = nvs_open(CFG_NS, NVS_READWRITE, &nvs);
    if (ret != ESP_OK) {
        return ret;
    }
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_u8(nvs, "auto_rec", cfg->auto_record_enabled));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_u8(nvs, "shake", cfg->shake_trigger_enabled));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_u32(nvs, "wake_s", cfg->wake_interval_sec));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_i32(nvs, "cam_fs", cfg->camera_framesize));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_i32(nvs, "cam_q", cfg->camera_jpeg_quality));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_u32(nvs, "aud_rate", cfg->audio_sample_rate_hz));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_u32(nvs, "imu_rate", cfg->imu_sample_rate_hz));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_u8(nvs, "low_batt", cfg->low_battery_percent));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_str(nvs, "ssid", cfg->wifi_ssid));
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs_set_str(nvs, "pass", cfg->wifi_password));
    ret = nvs_commit(nvs);
    nvs_close(nvs);
    return ret;
}
