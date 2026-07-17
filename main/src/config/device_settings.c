#include "device_settings.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "app_config.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"

#define SETTINGS_NS "day_cfg"

static SemaphoreHandle_t s_mutex;
static day_settings_snapshot_t s_settings;
static bool s_initialized;

static void copy_text(char *dst, size_t dst_len, const char *text)
{
    if (dst && dst_len > 0) {
        strlcpy(dst, text ? text : "", dst_len);
    }
}

static esp_err_t reject(char *field, size_t field_len,
                        char *reason, size_t reason_len,
                        const char *bad_field, const char *bad_reason)
{
    copy_text(field, field_len, bad_field);
    copy_text(reason, reason_len, bad_reason);
    return ESP_ERR_INVALID_ARG;
}

static bool utf8_valid(const char *value, size_t max_len, size_t *length)
{
    if (!value) {
        return false;
    }
    size_t len = strnlen(value, max_len + 1);
    if (len > max_len) {
        return false;
    }
    const unsigned char *p = (const unsigned char *)value;
    size_t i = 0;
    while (i < len) {
        unsigned char c = p[i];
        size_t continuation = 0;
        uint32_t codepoint = 0;
        if (c <= 0x7f) {
            codepoint = c;
        } else if (c >= 0xc2 && c <= 0xdf) {
            continuation = 1;
            codepoint = c & 0x1f;
        } else if (c >= 0xe0 && c <= 0xef) {
            continuation = 2;
            codepoint = c & 0x0f;
        } else if (c >= 0xf0 && c <= 0xf4) {
            continuation = 3;
            codepoint = c & 0x07;
        } else {
            return false;
        }
        if (i + continuation >= len) {
            return false;
        }
        for (size_t j = 1; j <= continuation; ++j) {
            if ((p[i + j] & 0xc0) != 0x80) {
                return false;
            }
            codepoint = (codepoint << 6) | (p[i + j] & 0x3f);
        }
        if ((continuation == 2 && codepoint < 0x800) ||
            (continuation == 3 && codepoint < 0x10000) ||
            codepoint > 0x10ffff || (codepoint >= 0xd800 && codepoint <= 0xdfff)) {
            return false;
        }
        i += continuation + 1;
    }
    if (length) {
        *length = len;
    }
    return true;
}

static bool ascii_printable_without_space(const char *value, size_t min_len, size_t max_len)
{
    size_t len = strnlen(value, max_len + 1);
    if (len < min_len || len > max_len) {
        return false;
    }
    for (size_t i = 0; i < len; ++i) {
        unsigned char c = (unsigned char)value[i];
        if (c < 0x21 || c > 0x7e) {
            return false;
        }
    }
    return true;
}

static bool ntp_host_valid(const char *value)
{
    if (!ascii_printable_without_space(value, 1, DAY_NTP_SERVER_MAX)) {
        return false;
    }
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p) {
        if (!(isalnum(*p) || *p == '.' || *p == '-' || *p == ':')) {
            return false;
        }
    }
    return true;
}

esp_err_t day_settings_validate(const day_config_t *config,
                                char *field, size_t field_len,
                                char *reason, size_t reason_len)
{
    copy_text(field, field_len, "");
    copy_text(reason, reason_len, "");
    if (!config) {
        return reject(field, field_len, reason, reason_len, "config", "missing_config");
    }
    if (!day_config_record_framesize_valid(config->camera_record_framesize)) {
        return reject(field, field_len, reason, reason_len,
                      "video.record_framesize", "unsupported_framesize");
    }
    if (config->camera_jpeg_quality < 4 || config->camera_jpeg_quality > 63) {
        return reject(field, field_len, reason, reason_len,
                      "video.jpeg_quality", "out_of_range");
    }
    if (!day_config_record_fps_valid(config->camera_record_fps) ||
        config->camera_record_fps > day_config_camera_max_fps(config->camera_record_framesize)) {
        return reject(field, field_len, reason, reason_len,
                      "video.record_fps", "unsupported_for_framesize");
    }
    if (config->camera_preview_framesize != 6 && config->camera_preview_framesize != 10 &&
        config->camera_preview_framesize != 11 && config->camera_preview_framesize != 13) {
        return reject(field, field_len, reason, reason_len,
                      "video.preview_framesize", "unsupported_framesize");
    }
    if (config->camera_preview_fps == 0 ||
        config->camera_preview_fps > day_config_camera_max_fps(config->camera_preview_framesize)) {
        return reject(field, field_len, reason, reason_len,
                      "video.preview_fps", "unsupported_for_framesize");
    }
    if (config->audio_sample_rate_hz < 8000 || config->audio_sample_rate_hz > 48000) {
        return reject(field, field_len, reason, reason_len, "audio.sample_rate_hz", "out_of_range");
    }
    if (config->imu_sample_rate_hz == 0 || config->imu_sample_rate_hz > 833 ||
        config->imu_orientation > 5) {
        return reject(field, field_len, reason, reason_len, "imu", "out_of_range");
    }
    if (config->wake_interval_sec < 60 || config->wake_interval_sec > 86400) {
        return reject(field, field_len, reason, reason_len,
                      "system.wake_interval_sec", "out_of_range");
    }
    if (config->low_battery_percent < 5 || config->low_battery_percent >= 40) {
        return reject(field, field_len, reason, reason_len,
                      "system.low_battery_percent", "out_of_range");
    }
    size_t ignored = 0;
    if (!utf8_valid(config->wifi_ssid, DAY_WIFI_SSID_MAX, &ignored)) {
        return reject(field, field_len, reason, reason_len, "wifi.ssid", "invalid_utf8_or_length");
    }
    if (!utf8_valid(config->wifi_password, 63, &ignored)) {
        return reject(field, field_len, reason, reason_len, "wifi.password", "invalid_utf8_or_length");
    }
    if (!ascii_printable_without_space(config->timezone, 1, DAY_TIMEZONE_MAX)) {
        return reject(field, field_len, reason, reason_len, "time.timezone", "invalid_posix_timezone");
    }
    if (!ntp_host_valid(config->ntp_server)) {
        return reject(field, field_len, reason, reason_len, "time.ntp_server", "invalid_host");
    }
    if (config->led_brightness_percent < 5 || config->led_brightness_percent > 100) {
        return reject(field, field_len, reason, reason_len, "led.brightness_percent", "out_of_range");
    }
    if (config->led_recording_r == 0 && config->led_recording_g == 0 &&
        config->led_recording_b == 0) {
        return reject(field, field_len, reason, reason_len, "led.recording_color", "color_must_be_visible");
    }
    return ESP_OK;
}

static void read_u8(nvs_handle_t nvs, const char *key, uint8_t *value)
{
    uint8_t candidate = *value;
    if (nvs_get_u8(nvs, key, &candidate) == ESP_OK) {
        *value = candidate;
    }
}

static void read_u32(nvs_handle_t nvs, const char *key, uint32_t *value)
{
    uint32_t candidate = *value;
    if (nvs_get_u32(nvs, key, &candidate) == ESP_OK) {
        *value = candidate;
    }
}

static void read_i32(nvs_handle_t nvs, const char *key, int *value)
{
    int32_t candidate = *value;
    if (nvs_get_i32(nvs, key, &candidate) == ESP_OK) {
        *value = candidate;
    }
}

static void read_string(nvs_handle_t nvs, const char *key, char *value, size_t value_len)
{
    size_t stored_len = value_len;
    (void)nvs_get_str(nvs, key, value, &stored_len);
}

static esp_err_t load_config(day_config_t *config, uint16_t *stored_schema, uint32_t *revision)
{
    day_config_defaults(config);
    nvs_handle_t nvs;
    esp_err_t result = nvs_open(SETTINGS_NS, NVS_READWRITE, &nvs);
    if (result != ESP_OK) {
        return result;
    }
    uint8_t flag = config->auto_record_enabled;
    read_u8(nvs, "auto_rec", &flag);
    config->auto_record_enabled = flag != 0;
    flag = config->shake_trigger_enabled;
    read_u8(nvs, "shake", &flag);
    config->shake_trigger_enabled = flag != 0;
    read_u32(nvs, "wake_s", &config->wake_interval_sec);
    read_i32(nvs, "cam_fs", &config->camera_framesize);
    config->camera_record_framesize = config->camera_framesize;
    read_i32(nvs, "prev_fs", &config->camera_preview_framesize);
    read_i32(nvs, "rec_fs", &config->camera_record_framesize);
    read_i32(nvs, "cam_q", &config->camera_jpeg_quality);
    read_u32(nvs, "prev_fps", &config->camera_preview_fps);
    read_u32(nvs, "rec_fps", &config->camera_record_fps);
    read_u32(nvs, "aud_rate", &config->audio_sample_rate_hz);
    read_u32(nvs, "imu_rate", &config->imu_sample_rate_hz);
    read_u8(nvs, "imu_orient", &config->imu_orientation);
    read_u8(nvs, "low_batt", &config->low_battery_percent);
    read_string(nvs, "ntp", config->ntp_server, sizeof(config->ntp_server));
    read_string(nvs, "tz", config->timezone, sizeof(config->timezone));
    read_string(nvs, "ssid", config->wifi_ssid, sizeof(config->wifi_ssid));
    read_string(nvs, "pass", config->wifi_password, sizeof(config->wifi_password));
    read_u8(nvs, "led_bri", &config->led_brightness_percent);
    read_u8(nvs, "led_rec_r", &config->led_recording_r);
    read_u8(nvs, "led_rec_g", &config->led_recording_g);
    read_u8(nvs, "led_rec_b", &config->led_recording_b);
    *stored_schema = 0;
    *revision = 0;
    (void)nvs_get_u16(nvs, "cfg_schema", stored_schema);
    (void)nvs_get_u32(nvs, "cfg_revision", revision);
    nvs_close(nvs);
    day_config_normalize(config);
    return ESP_OK;
}

#define SETTINGS_WRITE(expr) do { if (result == ESP_OK) { result = (expr); } } while (0)

static esp_err_t persist_config(const day_config_t *config, uint32_t revision)
{
    nvs_handle_t nvs;
    esp_err_t result = nvs_open(SETTINGS_NS, NVS_READWRITE, &nvs);
    if (result != ESP_OK) {
        return result;
    }
    SETTINGS_WRITE(nvs_set_u16(nvs, "cfg_schema", DAY_SETTINGS_NVS_SCHEMA_VERSION));
    SETTINGS_WRITE(nvs_set_u32(nvs, "cfg_revision", revision));
    SETTINGS_WRITE(nvs_set_u8(nvs, "auto_rec", config->auto_record_enabled));
    SETTINGS_WRITE(nvs_set_u8(nvs, "shake", config->shake_trigger_enabled));
    SETTINGS_WRITE(nvs_set_u32(nvs, "wake_s", config->wake_interval_sec));
    SETTINGS_WRITE(nvs_set_i32(nvs, "cam_fs", config->camera_framesize));
    SETTINGS_WRITE(nvs_set_i32(nvs, "prev_fs", config->camera_preview_framesize));
    SETTINGS_WRITE(nvs_set_i32(nvs, "rec_fs", config->camera_record_framesize));
    SETTINGS_WRITE(nvs_set_i32(nvs, "cam_q", config->camera_jpeg_quality));
    SETTINGS_WRITE(nvs_set_u32(nvs, "prev_fps", config->camera_preview_fps));
    SETTINGS_WRITE(nvs_set_u32(nvs, "rec_fps", config->camera_record_fps));
    SETTINGS_WRITE(nvs_set_u32(nvs, "aud_rate", config->audio_sample_rate_hz));
    SETTINGS_WRITE(nvs_set_u32(nvs, "imu_rate", config->imu_sample_rate_hz));
    SETTINGS_WRITE(nvs_set_u8(nvs, "imu_orient", config->imu_orientation));
    SETTINGS_WRITE(nvs_set_u8(nvs, "low_batt", config->low_battery_percent));
    SETTINGS_WRITE(nvs_set_str(nvs, "ntp", config->ntp_server));
    SETTINGS_WRITE(nvs_set_str(nvs, "tz", config->timezone));
    SETTINGS_WRITE(nvs_set_str(nvs, "ssid", config->wifi_ssid));
    SETTINGS_WRITE(nvs_set_str(nvs, "pass", config->wifi_password));
    SETTINGS_WRITE(nvs_set_u8(nvs, "led_bri", config->led_brightness_percent));
    SETTINGS_WRITE(nvs_set_u8(nvs, "led_rec_r", config->led_recording_r));
    SETTINGS_WRITE(nvs_set_u8(nvs, "led_rec_g", config->led_recording_g));
    SETTINGS_WRITE(nvs_set_u8(nvs, "led_rec_b", config->led_recording_b));
    if (result == ESP_OK) {
        result = nvs_commit(nvs);
    }
    nvs_close(nvs);
    return result;
}

static void apply_immediate_runtime_settings(const day_config_t *config)
{
    setenv("TZ", config->timezone, 1);
    tzset();
}

esp_err_t day_settings_init(void)
{
    if (s_initialized) {
        return ESP_OK;
    }
    if (!s_mutex) {
        s_mutex = xSemaphoreCreateMutex();
        if (!s_mutex) {
            return ESP_ERR_NO_MEM;
        }
    }
    day_config_t config;
    uint16_t stored_schema = 0;
    uint32_t revision = 0;
    esp_err_t result = load_config(&config, &stored_schema, &revision);
    if (result != ESP_OK) {
        return result;
    }
    if (stored_schema != DAY_SETTINGS_NVS_SCHEMA_VERSION || revision == 0) {
        revision = 1;
        result = persist_config(&config, revision);
        if (result != ESP_OK) {
            return result;
        }
    }
    s_settings.schema_version = DAY_SETTINGS_SCHEMA_VERSION;
    s_settings.revision = revision;
    s_settings.config = config;
    apply_immediate_runtime_settings(&config);
    s_initialized = true;
    return ESP_OK;
}

esp_err_t day_settings_get(day_settings_snapshot_t *snapshot)
{
    if (!snapshot || !s_initialized || !s_mutex) {
        return ESP_ERR_INVALID_STATE;
    }
    if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(250)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    *snapshot = s_settings;
    xSemaphoreGive(s_mutex);
    return ESP_OK;
}

esp_err_t day_settings_get_config(day_config_t *config, uint32_t *revision)
{
    if (!config) {
        return ESP_ERR_INVALID_ARG;
    }
    day_settings_snapshot_t snapshot;
    esp_err_t result = day_settings_get(&snapshot);
    if (result == ESP_OK) {
        *config = snapshot.config;
        if (revision) {
            *revision = snapshot.revision;
        }
    }
    return result;
}

esp_err_t day_settings_replace(const day_config_t *candidate,
                               uint32_t expected_revision,
                               day_settings_result_t *result_out)
{
    day_settings_result_t local = {0};
    day_settings_result_t *result = result_out ? result_out : &local;
    memset(result, 0, sizeof(*result));
    esp_err_t validation = day_settings_validate(candidate,
                                                  result->field, sizeof(result->field),
                                                  result->reason, sizeof(result->reason));
    if (validation != ESP_OK) {
        return validation;
    }
    if (!s_initialized || !s_mutex) {
        copy_text(result->reason, sizeof(result->reason), "service_not_initialized");
        return ESP_ERR_INVALID_STATE;
    }
    if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(1000)) != pdTRUE) {
        copy_text(result->reason, sizeof(result->reason), "settings_lock_timeout");
        return ESP_ERR_TIMEOUT;
    }
    esp_err_t status = ESP_OK;
    if (expected_revision != DAY_SETTINGS_ANY_REVISION && expected_revision != s_settings.revision) {
        copy_text(result->reason, sizeof(result->reason), "revision_conflict");
        status = ESP_ERR_INVALID_STATE;
    } else if (memcmp(candidate, &s_settings.config, sizeof(*candidate)) == 0) {
        result->revision = s_settings.revision;
    } else if (s_settings.revision == UINT32_MAX) {
        copy_text(result->reason, sizeof(result->reason), "revision_exhausted");
        status = ESP_ERR_INVALID_STATE;
    } else {
        uint32_t next_revision = s_settings.revision + 1;
        status = persist_config(candidate, next_revision);
        if (status == ESP_OK) {
            s_settings.config = *candidate;
            s_settings.revision = next_revision;
            result->revision = next_revision;
            result->changed = true;
            apply_immediate_runtime_settings(candidate);
        } else {
            copy_text(result->reason, sizeof(result->reason), "nvs_commit_failed");
        }
    }
    if (result->revision == 0) {
        result->revision = s_settings.revision;
    }
    xSemaphoreGive(s_mutex);
    return status;
}

bool day_settings_recording_fields_changed(const day_config_t *before,
                                           const day_config_t *after)
{
    if (!before || !after) {
        return false;
    }
    return before->camera_record_framesize != after->camera_record_framesize ||
           before->camera_jpeg_quality != after->camera_jpeg_quality ||
           before->camera_record_fps != after->camera_record_fps ||
           before->audio_sample_rate_hz != after->audio_sample_rate_hz ||
           before->imu_sample_rate_hz != after->imu_sample_rate_hz ||
           before->imu_orientation != after->imu_orientation;
}

void day_settings_format_recording_color(const day_config_t *config, char out[8])
{
    if (!out) {
        return;
    }
    if (!config) {
        strlcpy(out, "#FF3000", 8);
        return;
    }
    snprintf(out, 8, "#%02X%02X%02X",
             config->led_recording_r,
             config->led_recording_g,
             config->led_recording_b);
}
