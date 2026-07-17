#include "led_status.h"

#include "day_pins.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "led_strip.h"

static const char *TAG = "day_led";

static led_strip_handle_t s_strip;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static struct {
    day_led_mode_t base_mode;
    bool recording;
    bool fatal_error;
    bool usb_enumerated;
    bool usb_handshake;
    bool preview;
    int64_t preview_until_us;
    uint8_t preview_r, preview_g, preview_b, preview_brightness;
    uint8_t brightness;
    uint8_t recording_r, recording_g, recording_b;
    int64_t battery_pattern_until_us;
    bool battery_lock;
} s_state = {
    .base_mode = DAY_LED_OFF,
    .brightness = DAY_LED_DEFAULT_BRIGHTNESS_PERCENT,
    .recording_r = DAY_LED_DEFAULT_RECORDING_R,
    .recording_g = DAY_LED_DEFAULT_RECORDING_G,
    .recording_b = DAY_LED_DEFAULT_RECORDING_B,
};

static esp_err_t fill_all(uint8_t r, uint8_t g, uint8_t b)
{
    if (!s_strip) {
        return ESP_ERR_INVALID_STATE;
    }
    for (int i = 0; i < DAY_RGB_LED_COUNT; ++i) {
        ESP_RETURN_ON_ERROR(led_strip_set_pixel(s_strip, i, r, g, b), TAG, "set pixel failed");
    }
    return led_strip_refresh(s_strip); /* LED task is the sole hardware writer. */
}

static uint8_t scale_channel(uint8_t value, uint8_t percent)
{
    if (value == 0) return 0;
    uint16_t scaled = ((uint16_t)value * percent + 50U) / 100U;
    return (uint8_t)(scaled > 255U ? 255U : (scaled == 0 ? 1 : scaled));
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
    return ESP_OK;
}

esp_err_t day_led_set_rgb(uint8_t r, uint8_t g, uint8_t b)
{
    return day_led_preview(r, g, b, 100, 5000);
}

esp_err_t day_led_set_mode(day_led_mode_t mode)
{
    if (mode < DAY_LED_OFF || mode > DAY_LED_ERROR) return ESP_ERR_INVALID_ARG;
    portENTER_CRITICAL(&s_lock);
    s_state.recording = mode == DAY_LED_RECORDING;
    if (mode == DAY_LED_ERROR) s_state.fatal_error = true;
    if (mode != DAY_LED_RECORDING && mode != DAY_LED_ERROR && mode != DAY_LED_LOW_BATTERY) {
        s_state.base_mode = mode;
    }
    if (mode == DAY_LED_LOW_BATTERY) s_state.battery_pattern_until_us = esp_timer_get_time() + 5000000;
    portEXIT_CRITICAL(&s_lock);
    return ESP_OK;
}

esp_err_t day_led_apply_config(const day_config_t *config)
{
    if (!config || config->led_brightness_percent < 5 || config->led_brightness_percent > 100 ||
        (config->led_recording_r == 0 && config->led_recording_g == 0 && config->led_recording_b == 0)) {
        return ESP_ERR_INVALID_ARG;
    }
    portENTER_CRITICAL(&s_lock);
    s_state.brightness = config->led_brightness_percent;
    s_state.recording_r = config->led_recording_r;
    s_state.recording_g = config->led_recording_g;
    s_state.recording_b = config->led_recording_b;
    portEXIT_CRITICAL(&s_lock);
    return ESP_OK;
}

esp_err_t day_led_set_usb_enumerated(bool active) { portENTER_CRITICAL(&s_lock); s_state.usb_enumerated = active; if (!active) s_state.usb_handshake = false; portEXIT_CRITICAL(&s_lock); return ESP_OK; }
esp_err_t day_led_set_usb_handshake(bool active) { portENTER_CRITICAL(&s_lock); s_state.usb_handshake = active; portEXIT_CRITICAL(&s_lock); return ESP_OK; }

esp_err_t day_led_preview(uint8_t r, uint8_t g, uint8_t b, uint8_t brightness, uint32_t duration_ms)
{
    if ((r == 0 && g == 0 && b == 0) || brightness < 5 || brightness > 100 || duration_ms < 250 || duration_ms > 5000) return ESP_ERR_INVALID_ARG;
    portENTER_CRITICAL(&s_lock);
    s_state.preview_r = r; s_state.preview_g = g; s_state.preview_b = b;
    s_state.preview_brightness = brightness; s_state.preview = true;
    s_state.preview_until_us = esp_timer_get_time() + (int64_t)duration_ms * 1000;
    portEXIT_CRITICAL(&s_lock);
    return ESP_OK;
}

esp_err_t day_led_show_battery(const day_battery_status_t *battery)
{
    if (!battery) {
        return ESP_ERR_INVALID_ARG;
    }
    portENTER_CRITICAL(&s_lock);
    s_state.battery_lock = battery->soc_percent > 0.1f && battery->soc_percent < 20.0f;
    s_state.battery_pattern_until_us = esp_timer_get_time() + (s_state.battery_lock ? 5000000 : 1800000);
    portEXIT_CRITICAL(&s_lock);
    return ESP_OK;
}

static const char *mode_name(day_led_mode_t mode)
{
    switch (mode) { case DAY_LED_BOOT:return "boot"; case DAY_LED_WIFI_PORTAL:return "wifi_portal"; case DAY_LED_TIME_SYNC:return "time_sync"; case DAY_LED_USB_ENUMERATED:return "usb_enumerated"; case DAY_LED_USB_HANDSHAKE:return "usb_handshake"; case DAY_LED_RECORDING:return "recording"; case DAY_LED_LOW_BATTERY:return "low_battery"; case DAY_LED_ERROR:return "error"; default:return "off"; }
}

esp_err_t day_led_get_snapshot(day_led_status_snapshot_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    int64_t now = esp_timer_get_time(); day_led_mode_t mode;
    portENTER_CRITICAL(&s_lock);
    mode = s_state.battery_lock ? DAY_LED_LOW_BATTERY : s_state.fatal_error ? DAY_LED_ERROR : s_state.recording ? DAY_LED_RECORDING : (s_state.preview && now < s_state.preview_until_us) ? DAY_LED_RECORDING : s_state.usb_handshake ? DAY_LED_USB_HANDSHAKE : s_state.usb_enumerated ? DAY_LED_USB_ENUMERATED : s_state.base_mode;
    out->brightness_percent = s_state.brightness; out->recording_r = s_state.recording_r; out->recording_g = s_state.recording_g; out->recording_b = s_state.recording_b;
    strlcpy(out->mode, (s_state.preview && now < s_state.preview_until_us) ? "preview" : mode_name(mode), sizeof(out->mode));
    portEXIT_CRITICAL(&s_lock); return ESP_OK;
}

static void led_task(void *arg)
{
    while (true) {
        int64_t now = esp_timer_get_time(); uint8_t r=0,g=0,b=0,brightness=100; day_led_mode_t mode;
        portENTER_CRITICAL(&s_lock);
        if (s_state.preview && now >= s_state.preview_until_us) s_state.preview = false;
        if (s_state.battery_pattern_until_us && now >= s_state.battery_pattern_until_us) { s_state.battery_pattern_until_us=0; s_state.battery_lock=false; }
        mode = s_state.battery_lock ? DAY_LED_LOW_BATTERY : s_state.fatal_error ? DAY_LED_ERROR : s_state.recording ? DAY_LED_RECORDING : s_state.preview ? DAY_LED_RECORDING : s_state.usb_handshake ? DAY_LED_USB_HANDSHAKE : s_state.usb_enumerated ? DAY_LED_USB_ENUMERATED : s_state.base_mode;
        brightness = s_state.preview ? s_state.preview_brightness : s_state.brightness;
        if (s_state.preview) { r=s_state.preview_r;g=s_state.preview_g;b=s_state.preview_b; }
        else if (mode==DAY_LED_RECORDING) {r=s_state.recording_r;g=s_state.recording_g;b=s_state.recording_b;}
        portEXIT_CRITICAL(&s_lock);
        uint32_t ms=(uint32_t)(now/1000);
        if (!s_state.preview) {
            if (mode==DAY_LED_BOOT) r=g=b=24;
            else if (mode==DAY_LED_TIME_SYNC && ((ms/600)%2)==0) g=b=180;
            else if (mode==DAY_LED_WIFI_PORTAL && ((ms/150)%2)==0) b=255;
            else if (mode==DAY_LED_USB_ENUMERATED) { uint32_t phase=ms%2400; uint8_t level=(phase<1200)?(20+phase*180/1200):(200-(phase-1200)*180/1200); g=level; }
            else if (mode==DAY_LED_USB_HANDSHAKE) g=200;
            else if (mode==DAY_LED_ERROR) r=64;
            else if (mode==DAY_LED_LOW_BATTERY) { if (((ms/150)%2)==0) r=255; }
        }
        fill_all(scale_channel(r,brightness),scale_channel(g,brightness),scale_channel(b,brightness));
        vTaskDelay(pdMS_TO_TICKS(33)); /* >= 30 FPS breathing animation */
    }
}

void day_led_task_start(void)
{
    static bool started;
    if (!started) {
        started = true;
        xTaskCreate(led_task, "day_led", 3072, NULL, 3, NULL);
    }
}
