#include "device_status.h"

#include <limits.h>
#include <string.h>
#include <time.h>
#include "audio_service.h"
#include "battery.h"
#include "camera_service.h"
#include "imu.h"
#include "recorder.h"
#include "rtc_clock.h"
#include "storage_service.h"
#include "wifi_portal.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define BATTERY_REFRESH_US 2000000LL
#define RTC_REFRESH_US 1000000LL
#define STATUS_TASK_PERIOD_MS 250

static const char *TAG = "day_status";

static SemaphoreHandle_t s_mutex;
static TaskHandle_t s_task;
static day_device_status_t s_status;
static int64_t s_battery_sample_us = -1;
static int64_t s_rtc_sample_us = -1;
static int64_t s_storage_sample_us = -1;
static bool s_initialized;

static uint32_t sample_age_ms(int64_t sample_us, int64_t now_us)
{
    if (sample_us < 0 || now_us < sample_us) {
        return UINT32_MAX;
    }
    uint64_t age = (uint64_t)(now_us - sample_us) / 1000ULL;
    return age > UINT32_MAX ? UINT32_MAX : (uint32_t)age;
}

static const char *reset_reason_name(esp_reset_reason_t reason)
{
    switch (reason) {
    case ESP_RST_POWERON: return "power_on";
    case ESP_RST_EXT: return "external";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "interrupt_wdt";
    case ESP_RST_TASK_WDT: return "task_wdt";
    case ESP_RST_WDT: return "watchdog";
    case ESP_RST_DEEPSLEEP: return "deep_sleep";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SDIO: return "sdio";
    default: return "unknown";
    }
}

static const char *wake_reason_name(uint32_t causes)
{
    if (causes == 0) {
        return "cold_boot";
    }
#define WOKE_BY(cause) ((causes & (1UL << (cause))) != 0)
    if (WOKE_BY(ESP_SLEEP_WAKEUP_TIMER)) return "timer";
    if (WOKE_BY(ESP_SLEEP_WAKEUP_EXT1)) return "shake";
    if (WOKE_BY(ESP_SLEEP_WAKEUP_EXT0)) return "ext0";
    if (WOKE_BY(ESP_SLEEP_WAKEUP_TOUCHPAD)) return "touch";
    if (WOKE_BY(ESP_SLEEP_WAKEUP_ULP)) return "ulp";
    if (WOKE_BY(ESP_SLEEP_WAKEUP_GPIO)) return "gpio";
    if (WOKE_BY(ESP_SLEEP_WAKEUP_UART)) return "uart";
    if (WOKE_BY(ESP_SLEEP_WAKEUP_WIFI)) return "wifi";
    if (WOKE_BY(ESP_SLEEP_WAKEUP_COCPU)) return "cocpu";
    if (WOKE_BY(ESP_SLEEP_WAKEUP_COCPU_TRAP_TRIG)) return "cocpu_trap";
    if (WOKE_BY(ESP_SLEEP_WAKEUP_BT)) return "bluetooth";
#undef WOKE_BY
    return "other";
}

static void update_clock_locked(void)
{
    time_t now = 0;
    time(&now);
    s_status.clock.system_valid = now > 1609459200;
    strlcpy(s_status.clock.timezone,
            s_status.config.timezone[0] ? s_status.config.timezone : "CST-8",
            sizeof(s_status.clock.timezone));
}

static void cache_battery(const day_battery_status_t *battery, esp_err_t result, int64_t now_us)
{
    if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
        return;
    }
    s_status.battery = *battery;
    s_status.battery.available = result == ESP_OK;
    s_status.battery.last_error = result;
    s_battery_sample_us = now_us;
    xSemaphoreGive(s_mutex);
}

static void cache_rtc(const day_rtc_status_t *rtc, esp_err_t result, int64_t now_us)
{
    if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
        return;
    }
    s_status.rtc = *rtc;
    s_status.rtc.available = result == ESP_OK;
    s_status.rtc.last_error = result;
    s_rtc_sample_us = now_us;
    xSemaphoreGive(s_mutex);
}

static void cache_storage(const day_storage_status_t *storage, esp_err_t result, int64_t now_us)
{
    if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
        return;
    }
    s_status.storage = *storage;
    s_status.storage.available = result == ESP_OK;
    s_status.storage.last_error = result;
    s_storage_sample_us = now_us;
    xSemaphoreGive(s_mutex);
}

esp_err_t day_status_refresh(uint32_t flags)
{
    if (!s_initialized || !s_mutex) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t first_error = ESP_OK;
    if ((flags & DAY_STATUS_REFRESH_BATTERY) != 0) {
        day_battery_status_t battery;
        esp_err_t result = day_battery_read(&battery);
        cache_battery(&battery, result, esp_timer_get_time());
        if (result != ESP_OK && first_error == ESP_OK) {
            first_error = result;
        }
    }
    if ((flags & DAY_STATUS_REFRESH_RTC) != 0) {
        day_rtc_status_t rtc;
        esp_err_t result = day_rtc_read(&rtc);
        cache_rtc(&rtc, result, esp_timer_get_time());
        if (result != ESP_OK && first_error == ESP_OK) {
            first_error = result;
        }
    }
    if ((flags & DAY_STATUS_REFRESH_STORAGE) != 0) {
        day_storage_status_t storage;
        esp_err_t result = day_storage_refresh_status(&storage);
        cache_storage(&storage, result, esp_timer_get_time());
        if (result != ESP_OK && first_error == ESP_OK) {
            first_error = result;
        }
    }
    return first_error;
}

static void cache_nonblocking_driver_state(void)
{
    day_camera_status_t camera = day_camera_get_status();
    day_audio_status_t audio = day_audio_get_status();
    day_imu_status_t imu = day_imu_get_status();
    day_wifi_status_t wifi = day_wifi_get_status();
    day_storage_status_t storage = day_storage_get_status();
    bool recording = day_recorder_is_active();
    esp_err_t record_error = day_recorder_get_last_error();
    if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
        return;
    }
    camera.available = camera.initialized;
    audio.available = audio.initialized;
    imu.available = imu.present;
    s_status.camera = camera;
    s_status.audio = audio;
    s_status.imu = imu;
    s_status.wifi = wifi;
    if (storage.mounted != s_status.storage.mounted ||
        storage.last_error != s_status.storage.last_error) {
        s_status.storage = storage;
        s_status.storage.available = storage.mounted && storage.last_error == ESP_OK;
        s_storage_sample_us = esp_timer_get_time();
    }
    s_status.recording_active = recording;
    s_status.last_record_error = record_error;
    update_clock_locked();
    xSemaphoreGive(s_mutex);
}

static void status_task(void *arg)
{
    (void)arg;
    int64_t last_stack_report_us = 0;
    while (true) {
        int64_t now_us = esp_timer_get_time();
        bool recording = day_recorder_is_active();
        if (!recording && (s_battery_sample_us < 0 || now_us - s_battery_sample_us >= BATTERY_REFRESH_US)) {
            (void)day_status_refresh(DAY_STATUS_REFRESH_BATTERY);
        }
        if (!recording && (s_rtc_sample_us < 0 || now_us - s_rtc_sample_us >= RTC_REFRESH_US)) {
            (void)day_status_refresh(DAY_STATUS_REFRESH_RTC);
        }
        cache_nonblocking_driver_state();
        if (now_us - last_stack_report_us >= 60000000LL) {
            UBaseType_t words = uxTaskGetStackHighWaterMark(NULL);
            if (words < 256) {
                ESP_LOGW(TAG, "status task stack low-water=%u words", (unsigned)words);
            }
            last_stack_report_us = now_us;
        }
        vTaskDelay(pdMS_TO_TICKS(STATUS_TASK_PERIOD_MS));
    }
}

esp_err_t day_status_service_init(const day_config_t *config, uint32_t config_revision)
{
    if (!config) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_initialized) {
        return day_status_set_config(config, config_revision);
    }
    s_mutex = xSemaphoreCreateMutex();
    if (!s_mutex) {
        return ESP_ERR_NO_MEM;
    }
    memset(&s_status, 0, sizeof(s_status));
    s_status.schema_version = DAY_DEVICE_STATUS_SCHEMA_VERSION;
    s_status.config_revision = config_revision;
    s_status.config = *config;
    s_status.battery.last_error = ESP_ERR_INVALID_STATE;
    s_status.rtc.last_error = ESP_ERR_INVALID_STATE;
    s_status.storage.last_error = ESP_ERR_INVALID_STATE;
    s_status.camera.last_error = ESP_ERR_INVALID_STATE;
    s_status.audio.last_error = ESP_ERR_INVALID_STATE;
    s_status.imu.last_error = ESP_ERR_INVALID_STATE;
    s_status.wifi.last_error = ESP_ERR_INVALID_STATE;
    s_status.last_record_error = ESP_ERR_INVALID_STATE;
    strlcpy(s_status.clock.source, "untrusted", sizeof(s_status.clock.source));
    strlcpy(s_status.clock.last_sync_source, "none", sizeof(s_status.clock.last_sync_source));
    strlcpy(s_status.power.reset_reason, reset_reason_name(esp_reset_reason()),
            sizeof(s_status.power.reset_reason));
    strlcpy(s_status.power.wake_reason, wake_reason_name(esp_sleep_get_wakeup_causes()),
            sizeof(s_status.power.wake_reason));
    s_status.power.timer_wake_enabled = config->auto_record_enabled;
    s_status.power.next_wake_sec = config->auto_record_enabled ? config->wake_interval_sec : 0;
    s_status.led.brightness_percent = config->led_brightness_percent;
    s_status.led.recording_r = config->led_recording_r;
    s_status.led.recording_g = config->led_recording_g;
    s_status.led.recording_b = config->led_recording_b;
    strlcpy(s_status.led.mode, "off", sizeof(s_status.led.mode));
    update_clock_locked();
    s_initialized = true;
    if (xTaskCreate(status_task, "day_status", 4096, NULL, 4, &s_task) != pdPASS) {
        s_initialized = false;
        vSemaphoreDelete(s_mutex);
        s_mutex = NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t day_status_get_snapshot(day_device_status_t *snapshot)
{
    if (!snapshot || !s_initialized || !s_mutex) {
        return ESP_ERR_INVALID_STATE;
    }
    if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    *snapshot = s_status;
    int64_t now_us = esp_timer_get_time();
    snapshot->snapshot_monotonic_ms = (uint64_t)now_us / 1000ULL;
    snapshot->battery.sample_age_ms = sample_age_ms(s_battery_sample_us, now_us);
    snapshot->rtc.sample_age_ms = sample_age_ms(s_rtc_sample_us, now_us);
    snapshot->storage.sample_age_ms = sample_age_ms(s_storage_sample_us, now_us);
    xSemaphoreGive(s_mutex);
    return ESP_OK;
}

esp_err_t day_status_set_config(const day_config_t *config, uint32_t config_revision)
{
    if (!config || !s_initialized || !s_mutex) {
        return ESP_ERR_INVALID_STATE;
    }
    if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    s_status.config = *config;
    s_status.config_revision = config_revision;
    s_status.power.timer_wake_enabled = config->auto_record_enabled;
    s_status.power.next_wake_sec = config->auto_record_enabled ? config->wake_interval_sec : 0;
    s_status.led.brightness_percent = config->led_brightness_percent;
    s_status.led.recording_r = config->led_recording_r;
    s_status.led.recording_g = config->led_recording_g;
    s_status.led.recording_b = config->led_recording_b;
    update_clock_locked();
    xSemaphoreGive(s_mutex);
    return ESP_OK;
}

void day_status_set_clock_state(const char *source, const char *last_sync_source,
                                time_t last_sync_unix)
{
    if (!s_initialized || !s_mutex || xSemaphoreTake(s_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
        return;
    }
    strlcpy(s_status.clock.source, source ? source : "untrusted", sizeof(s_status.clock.source));
    strlcpy(s_status.clock.last_sync_source, last_sync_source ? last_sync_source : "none",
            sizeof(s_status.clock.last_sync_source));
    s_status.clock.last_sync_unix = last_sync_unix;
    update_clock_locked();
    xSemaphoreGive(s_mutex);
}

void day_status_set_low_battery_latched(bool latched)
{
    if (!s_initialized || !s_mutex || xSemaphoreTake(s_mutex, pdMS_TO_TICKS(100)) != pdTRUE) {
        return;
    }
    s_status.power.low_battery_latched = latched;
    xSemaphoreGive(s_mutex);
}
