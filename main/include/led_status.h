#pragma once

#include <stdint.h>
#include "day_types.h"
#include "esp_err.h"

typedef enum {
    DAY_LED_OFF = 0,
    DAY_LED_BOOT,
    DAY_LED_WIFI_PORTAL,
    DAY_LED_TIME_SYNC,
    DAY_LED_USB_ENUMERATED,
    DAY_LED_USB_HANDSHAKE,
    DAY_LED_RECORDING,
    DAY_LED_LOW_BATTERY,
    DAY_LED_ERROR,
} day_led_mode_t;

esp_err_t day_led_init(void);
esp_err_t day_led_set_rgb(uint8_t r, uint8_t g, uint8_t b);
esp_err_t day_led_set_mode(day_led_mode_t mode);
esp_err_t day_led_apply_config(const day_config_t *config);
esp_err_t day_led_set_usb_enumerated(bool active);
esp_err_t day_led_set_usb_handshake(bool active);
esp_err_t day_led_preview(uint8_t r, uint8_t g, uint8_t b,
                          uint8_t brightness_percent, uint32_t duration_ms);
esp_err_t day_led_show_battery(const day_battery_status_t *battery);
esp_err_t day_led_get_snapshot(day_led_status_snapshot_t *snapshot);
void day_led_task_start(void);
