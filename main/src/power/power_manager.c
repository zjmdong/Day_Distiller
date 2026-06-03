#include "power_manager.h"

#include "audio_service.h"
#include "camera_service.h"
#include "day_pins.h"
#include "imu.h"
#include "led_status.h"
#include "storage_service.h"
#include "wifi_portal.h"
#include "esp_log.h"
#include "esp_sleep.h"

static const char *TAG = "day_power";

esp_err_t day_power_enter_deep_sleep(const day_config_t *cfg)
{
    if (!cfg) {
        return ESP_ERR_INVALID_ARG;
    }
    uint64_t wake_us = (uint64_t)cfg->wake_interval_sec * 1000000ULL;
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_sleep_enable_timer_wakeup(wake_us));

    if (cfg->shake_trigger_enabled) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_imu_configure_shake_wake(true));
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_sleep_enable_ext1_wakeup_io(1ULL << DAY_PIN_IMU_INT,
                                                                      ESP_EXT1_WAKEUP_ANY_HIGH));
    } else {
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_imu_configure_shake_wake(false));
    }

    ESP_ERROR_CHECK_WITHOUT_ABORT(day_imu_enter_sleep());
    day_audio_deinit();
    day_camera_deinit();
    day_wifi_deinit_for_sleep();
    day_storage_deinit();
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_led_set_mode(DAY_LED_OFF));

    ESP_LOGI(TAG, "entering deep sleep for %lu seconds", (unsigned long)cfg->wake_interval_sec);
    esp_deep_sleep_start();
}
