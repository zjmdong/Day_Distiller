#include "rtc_clock.h"

#include <string.h>
#include "board.h"
#include "esp_log.h"

#define PCF8563_ADDR 0x51
#define REG_CTRL_STATUS1 0x00
#define REG_SECONDS 0x02

static const char *TAG = "day_rtc";

static i2c_master_dev_handle_t s_dev;
static day_rtc_status_t s_last = {
    .last_error = ESP_ERR_INVALID_STATE,
};

static uint8_t bcd_to_u8(uint8_t bcd)
{
    return (uint8_t)(((bcd >> 4) * 10) + (bcd & 0x0f));
}

static uint8_t u8_to_bcd(uint8_t value)
{
    return (uint8_t)(((value / 10) << 4) | (value % 10));
}

esp_err_t day_rtc_init(void)
{
    if (s_dev) {
        return ESP_OK;
    }
    esp_err_t ret = day_i2c_add_device(day_board_sensor_i2c_bus(), PCF8563_ADDR, 100000, &s_dev);
    if (ret != ESP_OK) {
        s_last.last_error = ret;
        return ret;
    }
    ret = day_i2c_write_reg(s_dev, REG_CTRL_STATUS1, 0x00);
    s_last.last_error = ret;
    return ret;
}

esp_err_t day_rtc_read(day_rtc_status_t *status)
{
    if (!status) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(status, 0, sizeof(*status));
    if (!s_dev) {
        status->last_error = ESP_ERR_INVALID_STATE;
        s_last = *status;
        return status->last_error;
    }

    uint8_t raw[7] = {0};
    esp_err_t ret = day_i2c_read_reg(s_dev, REG_SECONDS, raw, sizeof(raw));
    if (ret != ESP_OK) {
        status->last_error = ret;
        s_last = *status;
        return ret;
    }

    bool voltage_low = (raw[0] & 0x80) != 0;
    struct tm tm = {
        .tm_sec = bcd_to_u8(raw[0] & 0x7f),
        .tm_min = bcd_to_u8(raw[1] & 0x7f),
        .tm_hour = bcd_to_u8(raw[2] & 0x3f),
        .tm_mday = bcd_to_u8(raw[3] & 0x3f),
        .tm_mon = bcd_to_u8(raw[5] & 0x1f) - 1,
        .tm_year = 100 + bcd_to_u8(raw[6]),
        .tm_isdst = -1,
    };
    time_t now = mktime(&tm);
    status->valid = !voltage_low && now > 0;
    status->unix_time = now;
    if (status->valid) {
        strftime(status->iso8601, sizeof(status->iso8601), "%Y-%m-%dT%H:%M:%S", &tm);
    } else {
        snprintf(status->iso8601, sizeof(status->iso8601), "invalid");
    }
    status->last_error = ESP_OK;
    s_last = *status;
    ESP_LOGD(TAG, "RTC %s", status->iso8601);
    return ESP_OK;
}

esp_err_t day_rtc_write_time(time_t unix_time)
{
    if (!s_dev) {
        return ESP_ERR_INVALID_STATE;
    }
    struct tm tm;
    localtime_r(&unix_time, &tm);
    uint8_t buf[8] = {
        REG_SECONDS,
        u8_to_bcd(tm.tm_sec),
        u8_to_bcd(tm.tm_min),
        u8_to_bcd(tm.tm_hour),
        u8_to_bcd(tm.tm_mday),
        u8_to_bcd(tm.tm_wday),
        u8_to_bcd(tm.tm_mon + 1),
        u8_to_bcd((tm.tm_year + 1900) % 100),
    };
    esp_err_t ret = day_i2c_write(s_dev, buf, sizeof(buf));
    s_last.last_error = ret;
    return ret;
}

day_rtc_status_t day_rtc_get_last(void)
{
    return s_last;
}
