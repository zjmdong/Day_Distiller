#include "battery.h"

#include <string.h>
#include "board.h"
#include "esp_log.h"

#define MAX17048_ADDR 0x36
#define REG_VCELL 0x02
#define REG_SOC 0x04
#define REG_MODE 0x06
#define REG_VERSION 0x08

static const char *TAG = "day_battery";

static i2c_master_dev_handle_t s_dev;
static day_battery_status_t s_last = {
    .charge_state = DAY_CHARGE_UNKNOWN,
    .last_error = ESP_ERR_INVALID_STATE,
};
static float s_soc_history[6];
static float s_voltage_history[6];
static uint8_t s_history_count;

static esp_err_t read_be16(uint8_t reg, uint16_t *out)
{
    uint8_t raw[2] = {0};
    esp_err_t ret = day_i2c_read_reg(s_dev, reg, raw, sizeof(raw));
    if (ret != ESP_OK) {
        return ret;
    }
    *out = ((uint16_t)raw[0] << 8) | raw[1];
    return ESP_OK;
}

static void update_charge_estimate(day_battery_status_t *status)
{
    uint8_t idx = s_history_count % (uint8_t)(sizeof(s_soc_history) / sizeof(s_soc_history[0]));
    s_soc_history[idx] = status->soc_percent;
    s_voltage_history[idx] = status->voltage_v;
    if (s_history_count < sizeof(s_soc_history) / sizeof(s_soc_history[0])) {
        s_history_count++;
        status->charge_state = DAY_CHARGE_UNKNOWN;
        status->charge_confidence = 20;
        return;
    }

    float soc_delta = s_soc_history[idx] - s_soc_history[(idx + 1) % 6];
    float voltage_delta = s_voltage_history[idx] - s_voltage_history[(idx + 1) % 6];
    if (soc_delta > 0.15f || voltage_delta > 0.015f) {
        status->charge_state = DAY_CHARGE_ESTIMATED;
        status->charge_confidence = 70;
    } else {
        status->charge_state = DAY_CHARGE_NOT_CHARGING;
        status->charge_confidence = 60;
    }
    s_history_count++;
}

esp_err_t day_battery_init(void)
{
    if (s_dev) {
        return ESP_OK;
    }
    esp_err_t ret = day_i2c_add_device(day_board_sensor_i2c_bus(), MAX17048_ADDR, 400000, &s_dev);
    if (ret != ESP_OK) {
        s_last.last_error = ret;
        return ret;
    }

    uint16_t version = 0;
    ret = read_be16(REG_VERSION, &version);
    s_last.last_error = ret;
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "MAX17048 version 0x%04x", version);
    }
    return ret;
}

esp_err_t day_battery_read(day_battery_status_t *status)
{
    if (!status) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(status, 0, sizeof(*status));
    if (!s_dev) {
        status->available = false;
        status->last_error = ESP_ERR_INVALID_STATE;
        s_last = *status;
        return status->last_error;
    }

    uint16_t vcell = 0;
    uint16_t soc = 0;
    esp_err_t ret = read_be16(REG_VCELL, &vcell);
    if (ret == ESP_OK) {
        ret = read_be16(REG_SOC, &soc);
    }
    if (ret != ESP_OK) {
        status->available = false;
        status->last_error = ret;
        s_last = *status;
        return ret;
    }

    status->voltage_v = (float)(vcell >> 4) * 0.00125f;
    status->soc_percent = (float)(soc >> 8) + ((float)(soc & 0xff) / 256.0f);
    if (status->soc_percent > 100.0f) {
        status->soc_percent = 100.0f;
    }
    update_charge_estimate(status);
    status->available = true;
    status->last_error = ESP_OK;
    s_last = *status;
    return ESP_OK;
}

day_battery_status_t day_battery_get_last(void)
{
    return s_last;
}
