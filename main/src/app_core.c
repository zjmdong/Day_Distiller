#include "day_app.h"

#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>
#include "app_config.h"
#include "audio_service.h"
#include "battery.h"
#include "board.h"
#include "camera_service.h"
#include "device_settings.h"
#include "device_status.h"
#include "imu.h"
#include "led_status.h"
#include "power_manager.h"
#include "recorder.h"
#include "rtc_clock.h"
#include "storage_service.h"
#include "usb_link.h"
#include "web_server.h"
#include "wifi_portal.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

static const char *TAG = "day_app";

static day_config_t s_config;
static uint32_t s_config_revision;

static void apply_runtime_config(const day_config_t *config)
{
    if (!config) {
        return;
    }
    setenv("TZ", config->timezone[0] ? config->timezone : "CST-8", 1);
    tzset();
    day_imu_set_orientation(config->imu_orientation);
}

static esp_err_t init_nvs(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    return ret;
}

static esp_err_t collect_status(day_device_status_t *status)
{
    return day_status_get_snapshot(status);
}

static esp_err_t apply_config_cb(const day_config_t *config,
                                 uint32_t expected_revision,
                                 day_settings_result_t *save_result)
{
    if (!config) {
        return ESP_ERR_INVALID_ARG;
    }
    day_config_t previous;
    uint32_t previous_revision = 0;
    ESP_RETURN_ON_ERROR(day_settings_get_config(&previous, &previous_revision), TAG,
                        "settings snapshot failed");
    day_config_t next = *config;
    if (day_recorder_is_active() && day_settings_recording_fields_changed(&previous, &next)) {
        return ESP_ERR_INVALID_STATE;
    }
    bool camera_changed = next.camera_framesize != s_config.camera_framesize ||
                          next.camera_preview_framesize != s_config.camera_preview_framesize ||
                          next.camera_record_framesize != s_config.camera_record_framesize ||
                          next.camera_jpeg_quality != s_config.camera_jpeg_quality;
    bool audio_changed = next.audio_sample_rate_hz != s_config.audio_sample_rate_hz;
    bool imu_changed = next.imu_sample_rate_hz != s_config.imu_sample_rate_hz ||
                       next.imu_orientation != s_config.imu_orientation;
    day_settings_result_t local_result;
    day_settings_result_t *result = save_result ? save_result : &local_result;
    uint32_t revision_guard = expected_revision == DAY_SETTINGS_ANY_REVISION
                                  ? previous_revision
                                  : expected_revision;
    ESP_RETURN_ON_ERROR(day_settings_replace(&next, revision_guard, result), TAG,
                        "settings save failed");
    ESP_RETURN_ON_ERROR(day_settings_get_config(&s_config, &s_config_revision), TAG,
                        "settings refresh failed");
    apply_runtime_config(&s_config);
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_status_set_config(&s_config, s_config_revision));
    if (!day_recorder_is_active()) {
        if (camera_changed) {
            day_camera_deinit();
        }
        if (audio_changed) {
            day_audio_deinit();
        }
        if (imu_changed) {
            ESP_ERROR_CHECK_WITHOUT_ABORT(day_imu_init(s_config.imu_sample_rate_hz));
        }
    }
    return ESP_OK;
}

static esp_err_t save_config_cb(const day_config_t *config)
{
    return apply_config_cb(config, DAY_SETTINGS_ANY_REVISION, NULL);
}

static esp_err_t record_once_cb(void)
{
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_led_set_mode(DAY_LED_RECORDING));
    day_record_paths_t paths;
    esp_err_t ret = day_recorder_record_once(&s_config, &paths);
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_status_refresh(DAY_STATUS_REFRESH_BATTERY |
                                                     DAY_STATUS_REFRESH_STORAGE));
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_led_set_mode(ret == ESP_OK ? DAY_LED_BOOT : DAY_LED_ERROR));
    return ret;
}

static void sync_rtc_from_system_if_valid(void)
{
    time_t now = 0;
    time(&now);
    if (now > 1609459200) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_rtc_write_time(now));
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_status_refresh(DAY_STATUS_REFRESH_RTC));
    }
}

static void sync_system_from_rtc_if_valid(void)
{
    day_rtc_status_t rtc;
    if (day_rtc_read(&rtc) == ESP_OK && rtc.valid && rtc.unix_time > 1609459200) {
        struct timeval tv = {
            .tv_sec = rtc.unix_time,
            .tv_usec = 0,
        };
        if (settimeofday(&tv, NULL) == 0) {
            ESP_LOGI(TAG, "system time restored from RTC: %s", rtc.iso8601);
        }
    }
}

static bool low_battery_blocking_record(void)
{
    day_battery_status_t battery;
    if (day_battery_read(&battery) != ESP_OK) {
        return false;
    }
    return battery.soc_percent > 0.1f && battery.soc_percent < s_config.low_battery_percent;
}

static bool is_automatic_wakeup(uint32_t wakeup_causes)
{
    const uint32_t timer_wake = 1UL << ESP_SLEEP_WAKEUP_TIMER;
    const uint32_t shake_wake = 1UL << ESP_SLEEP_WAKEUP_EXT1;
    return (wakeup_causes & (timer_wake | shake_wake)) != 0;
}

void day_app_run(void)
{
    ESP_ERROR_CHECK(init_nvs());
    ESP_ERROR_CHECK(day_settings_init());
    ESP_ERROR_CHECK(day_settings_get_config(&s_config, &s_config_revision));
    apply_runtime_config(&s_config);
    ESP_ERROR_CHECK(day_status_service_init(&s_config, s_config_revision));
    day_usb_link_set_config_apply_callback(apply_config_cb);

    if (day_usb_link_should_run_msc_mode()) {
        day_usb_link_run_msc_mode();
        return;
    }

    ESP_ERROR_CHECK_WITHOUT_ABORT(day_usb_link_start_serial_mode());
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_board_init());
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_led_init());
    day_led_task_start();
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_led_set_mode(DAY_LED_BOOT));

    ESP_ERROR_CHECK_WITHOUT_ABORT(day_battery_init());
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_rtc_init());
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_imu_init(s_config.imu_sample_rate_hz));
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_storage_init());
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_status_refresh(DAY_STATUS_REFRESH_ALL));

    uint32_t wakeup_causes = esp_sleep_get_wakeup_causes();
    bool cold_boot = !is_automatic_wakeup(wakeup_causes);
    ESP_LOGI(TAG, "boot wake causes=0x%08lx cold=%d", (unsigned long)wakeup_causes, cold_boot);

    if (cold_boot && !day_usb_link_should_resume_maintenance()) {
        vTaskDelay(pdMS_TO_TICKS(1500));
    }
    bool maintenance_start = day_usb_link_maintenance_active();

    if (cold_boot && !maintenance_start) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_led_set_mode(DAY_LED_TIME_SYNC));
        esp_err_t sync_ret = day_wifi_sync_time(&s_config);
        if (sync_ret == ESP_OK) {
            sync_rtc_from_system_if_valid();
        } else {
            ESP_LOGW(TAG, "network time sync skipped/failed: %s", esp_err_to_name(sync_ret));
            sync_system_from_rtc_if_valid();
        }

        day_web_callbacks_t web = {
            .get_status = collect_status,
            .save_config = save_config_cb,
            .record_once = record_once_cb,
            .config = &s_config,
        };
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_web_start(&web));
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_led_set_mode(DAY_LED_WIFI_PORTAL));
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_wifi_run_portal_window(30000));
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_web_stop());
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_wifi_stop_portal());
    } else {
        sync_system_from_rtc_if_valid();
    }

    if (!day_usb_link_maintenance_active()) {
        if (low_battery_blocking_record()) {
            ESP_LOGW(TAG, "battery below threshold, skipping record");
            ESP_ERROR_CHECK_WITHOUT_ABORT(day_led_set_mode(DAY_LED_LOW_BATTERY));
            vTaskDelay(pdMS_TO_TICKS(1200));
        } else if (s_config.auto_record_enabled) {
            ESP_ERROR_CHECK_WITHOUT_ABORT(record_once_cb());
        }
    }

    if (day_usb_link_maintenance_active()) {
        ESP_LOGI(TAG, "USB Link maintenance active; staying awake");
        while (day_usb_link_maintenance_active()) {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }

    ESP_ERROR_CHECK_WITHOUT_ABORT(day_power_enter_deep_sleep(&s_config));
}
