#include "led_status.h"

#include "day_pins.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "led_strip.h"

static const char *TAG = "day_led";

static led_strip_handle_t s_strip;
static day_led_mode_t s_mode = DAY_LED_OFF;

static esp_err_t fill_all(uint8_t r, uint8_t g, uint8_t b)
{
    if (!s_strip) {
        return ESP_ERR_INVALID_STATE;
    }
    for (int i = 0; i < DAY_RGB_LED_COUNT; ++i) {
        ESP_RETURN_ON_ERROR(led_strip_set_pixel(s_strip, i, r, g, b), TAG, "set pixel failed");
    }
    return led_strip_refresh(s_strip);
}

esp_err_t day_led_init(void)
{
    if (s_strip) {
        return ESP_OK;
    }
    led_strip_config_t strip_config = {
        .strip_gpio_num = DAY_PIN_RGB_DIN,
        .max_leds = DAY_RGB_LED_COUNT,
    };
    led_strip_rmt_config_t rmt_config = {
        .resolution_hz = 10 * 1000 * 1000,
        .flags.with_dma = false,
    };
    ESP_RETURN_ON_ERROR(led_strip_new_rmt_device(&strip_config, &rmt_config, &s_strip),
                        TAG, "RMT LED init failed");
    return led_strip_clear(s_strip);
}

esp_err_t day_led_set_rgb(uint8_t r, uint8_t g, uint8_t b)
{
    s_mode = DAY_LED_OFF;
    return fill_all(r, g, b);
}

esp_err_t day_led_set_mode(day_led_mode_t mode)
{
    s_mode = mode;
    switch (mode) {
    case DAY_LED_OFF:
        return s_strip ? led_strip_clear(s_strip) : ESP_ERR_INVALID_STATE;
    case DAY_LED_BOOT:
        return fill_all(16, 16, 16);
    case DAY_LED_RECORDING:
        return fill_all(255, 48, 0);
    case DAY_LED_LOW_BATTERY:
        return fill_all(255, 0, 0);
    case DAY_LED_ERROR:
        return fill_all(64, 0, 0);
    default:
        return ESP_OK;
    }
}

esp_err_t day_led_show_battery(const day_battery_status_t *battery)
{
    if (!battery) {
        return ESP_ERR_INVALID_ARG;
    }
    if (battery->soc_percent < 20.0f) {
        return fill_all(255, 0, 0);
    }
    if (battery->soc_percent < 50.0f) {
        return fill_all(255, 160, 0);
    }
    return fill_all(0, 180, 40);
}

static void led_task(void *arg)
{
    bool on = false;
    while (true) {
        switch (s_mode) {
        case DAY_LED_WIFI_PORTAL:
            fill_all(on ? 0 : 0, on ? 0 : 0, on ? 255 : 0);
            vTaskDelay(pdMS_TO_TICKS(150));
            break;
        case DAY_LED_TIME_SYNC:
            fill_all(on ? 0 : 0, on ? 160 : 32, on ? 160 : 0);
            vTaskDelay(pdMS_TO_TICKS(600));
            break;
        default:
            vTaskDelay(pdMS_TO_TICKS(250));
            break;
        }
        on = !on;
    }
}

void day_led_task_start(void)
{
    static bool started;
    if (!started) {
        started = true;
        xTaskCreate(led_task, "day_led", 2048, NULL, 3, NULL);
    }
}
