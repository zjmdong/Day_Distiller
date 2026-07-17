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

static bool bcd_is_valid(uint8_t bcd)
{
    return (bcd & 0x0f) <= 9 && ((bcd >> 4) & 0x0f) <= 9;
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
    uint8_t seconds_bcd = raw[0] & 0x7f;
    uint8_t minutes_bcd = raw[1] & 0x7f;
    uint8_t hours_bcd = raw[2] & 0x3f;
    uint8_t day_bcd = raw[3] & 0x3f;
    uint8_t month_bcd = raw[5] & 0x1f;
    uint8_t year_bcd = raw[6];
    uint8_t second = bcd_to_u8(seconds_bcd);
    uint8_t minute = bcd_to_u8(minutes_bcd);
    uint8_t hour = bcd_to_u8(hours_bcd);
    uint8_t day = bcd_to_u8(day_bcd);
    uint8_t month = bcd_to_u8(month_bcd);
    uint8_t year = bcd_to_u8(year_bcd);
    bool fields_valid = bcd_is_valid(seconds_bcd) && bcd_is_valid(minutes_bcd) &&
                        bcd_is_valid(hours_bcd) && bcd_is_valid(day_bcd) &&
                        bcd_is_valid(month_bcd) && bcd_is_valid(year_bcd) &&
                        second <= 59 && minute <= 59 && hour <= 23 &&
                        day >= 1 && day <= 31 && month >= 1 && month <= 12;
    struct tm tm = {
        .tm_sec = second,
        .tm_min = minute,
        .tm_hour = hour,
        .tm_mday = day,
        .tm_mon = month - 1,
        .tm_year = 100 + year,
        .tm_isdst = -1,
    };
    struct tm requested = tm;
    time_t now = mktime(&tm);
    bool calendar_exact = now > 0 && tm.tm_sec == requested.tm_sec &&
                          tm.tm_min == requested.tm_min && tm.tm_hour == requested.tm_hour &&
                          tm.tm_mday == requested.tm_mday && tm.tm_mon == requested.tm_mon &&
                          tm.tm_year == requested.tm_year;
    status->valid = !voltage_low && fields_valid && calendar_exact;
    status->unix_time = status->valid ? now : 0;
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
