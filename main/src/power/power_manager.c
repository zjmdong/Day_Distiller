#include "power_manager.h"

#include <stddef.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "audio_service.h"
#include "camera_service.h"
#include "day_pins.h"
#include "imu.h"
#include "led_status.h"
#include "storage_service.h"
#include "wifi_portal.h"
#include "esp_log.h"
#include "esp_sleep.h"
#include "esp_attr.h"
#include "esp_system.h"
#include "usb_protocol.h"

static const char *TAG = "day_power";

#define DAY_POWER_LATCH_MAGIC 0x4444504cU
#define DAY_POWER_LATCH_SCHEMA 1U
typedef struct {
    uint32_t magic;
    uint16_t schema;
    uint8_t latched;
    uint8_t reserved;
    uint32_t crc32;
} day_power_latch_t;
static RTC_NOINIT_ATTR day_power_latch_t s_latch;

static uint32_t latch_crc(const day_power_latch_t *latch)
{
    return day_usb_crc32(0, (const uint8_t *)latch, offsetof(day_power_latch_t, crc32));
}

static bool latch_valid(void)
{
    return s_latch.magic == DAY_POWER_LATCH_MAGIC && s_latch.schema == DAY_POWER_LATCH_SCHEMA &&
           s_latch.latched <= 1 && s_latch.crc32 == latch_crc(&s_latch);
}

void day_power_init(void)
{
    if (esp_reset_reason() == ESP_RST_POWERON || !latch_valid()) memset(&s_latch, 0, sizeof(s_latch));
}

bool day_power_low_battery_latched(void) { return latch_valid() && s_latch.latched == 1; }

void day_power_latch_low_battery(void)
{
    day_power_latch_t next = {.magic=DAY_POWER_LATCH_MAGIC,.schema=DAY_POWER_LATCH_SCHEMA,.latched=1};
    next.crc32 = latch_crc(&next); s_latch = next;
}

static void shutdown_peripherals(void)
{
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_imu_enter_sleep());
    day_audio_deinit(); day_camera_deinit(); day_wifi_deinit_for_sleep(); day_storage_deinit();
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_led_set_mode(DAY_LED_OFF));
    vTaskDelay(pdMS_TO_TICKS(50));
}

esp_err_t day_power_enter_locked_sleep(void)
{
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL));
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_imu_configure_shake_wake(false));
    shutdown_peripherals();
    ESP_LOGW(TAG, "entering low-battery locked sleep with all wake sources disabled");
    esp_deep_sleep_start();
}

esp_err_t day_power_enter_deep_sleep(const day_config_t *cfg)
{
    if (!cfg) {
        return ESP_ERR_INVALID_ARG;
    }
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL));
    if (cfg->auto_record_enabled) {
        uint64_t wake_us = (uint64_t)cfg->wake_interval_sec * 1000000ULL;
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_sleep_enable_timer_wakeup(wake_us));
    }

    if (cfg->shake_trigger_enabled) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_imu_configure_shake_wake(true));
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_sleep_enable_ext1_wakeup_io(1ULL << DAY_PIN_IMU_INT,
                                                                      ESP_EXT1_WAKEUP_ANY_HIGH));
    } else {
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_imu_configure_shake_wake(false));
    }

    shutdown_peripherals();

    ESP_LOGI(TAG, "entering deep sleep timer=%d interval=%lu seconds", cfg->auto_record_enabled,
             (unsigned long)cfg->wake_interval_sec);
    esp_deep_sleep_start();
}
