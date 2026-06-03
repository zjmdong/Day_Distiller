#include "imu.h"

#include <math.h>
#include <string.h>
#include "board.h"
#include "day_pins.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"

#define LSM6DS3_ADDR_LOW 0x6A
#define LSM6DS3_ADDR_HIGH 0x6B
#define REG_WHO_AM_I 0x0F
#define REG_CTRL1_XL 0x10
#define REG_CTRL2_G 0x11
#define REG_CTRL3_C 0x12
#define REG_OUTX_L_G 0x22
#define REG_WAKE_UP_THS 0x5B
#define REG_MD1_CFG 0x5E
#define WHO_AM_I_LSM6DS3 0x69
#define WHO_AM_I_LSM6DSL_COMPAT 0x6A

static const char *TAG = "day_imu";

static i2c_master_dev_handle_t s_dev;
static day_imu_status_t s_status = {
    .last_error = ESP_ERR_INVALID_STATE,
};

static esp_err_t probe_addr(uint8_t addr)
{
    i2c_master_dev_handle_t dev = NULL;
    esp_err_t ret = day_i2c_add_device(day_board_sensor_i2c_bus(), addr, 400000, &dev);
    if (ret != ESP_OK) {
        return ret;
    }
    uint8_t who = 0;
    ret = day_i2c_read_reg(dev, REG_WHO_AM_I, &who, 1);
    if (ret == ESP_OK && (who == WHO_AM_I_LSM6DS3 || who == WHO_AM_I_LSM6DSL_COMPAT)) {
        s_dev = dev;
        ESP_LOGI(TAG, "LSM6DS IMU found at 0x%02x who=0x%02x", addr, who);
        return ESP_OK;
    }
    if (ret == ESP_OK) {
        ESP_LOGW(TAG, "unexpected WHO_AM_I at 0x%02x: 0x%02x", addr, who);
    }
    i2c_master_bus_rm_device(dev);
    return ESP_ERR_NOT_FOUND;
}

static uint8_t odr_bits(uint32_t rate_hz)
{
    if (rate_hz <= 13) {
        return 0x10;
    }
    if (rate_hz <= 26) {
        return 0x20;
    }
    if (rate_hz <= 52) {
        return 0x30;
    }
    if (rate_hz <= 104) {
        return 0x40;
    }
    if (rate_hz <= 208) {
        return 0x50;
    }
    if (rate_hz <= 416) {
        return 0x60;
    }
    return 0x70;
}

esp_err_t day_imu_init(uint32_t sample_rate_hz)
{
    if (!s_dev) {
        esp_err_t ret = probe_addr(LSM6DS3_ADDR_LOW);
        if (ret != ESP_OK) {
            ret = probe_addr(LSM6DS3_ADDR_HIGH);
        }
        if (ret != ESP_OK) {
            s_status.present = false;
            s_status.last_error = ret;
            return ret;
        }
    }

    uint8_t odr = odr_bits(sample_rate_hz);
    esp_err_t ret = day_i2c_write_reg(s_dev, REG_CTRL3_C, 0x44); /* BDU + auto increment */
    if (ret == ESP_OK) {
        ret = day_i2c_write_reg(s_dev, REG_CTRL1_XL, odr | 0x00); /* 2g */
    }
    if (ret == ESP_OK) {
        ret = day_i2c_write_reg(s_dev, REG_CTRL2_G, odr | 0x00); /* 245 dps */
    }
    s_status.present = ret == ESP_OK;
    s_status.last_error = ret;
    return ret;
}

esp_err_t day_imu_read(day_imu_sample_t *sample)
{
    if (!sample) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(sample, 0, sizeof(*sample));
    if (!s_dev) {
        s_status.last_error = ESP_ERR_INVALID_STATE;
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t raw[12] = {0};
    esp_err_t ret = day_i2c_read_reg(s_dev, REG_OUTX_L_G, raw, sizeof(raw));
    if (ret != ESP_OK) {
        s_status.last_error = ret;
        return ret;
    }
    sample->t_us = esp_timer_get_time();
    sample->gx = (int16_t)((raw[1] << 8) | raw[0]);
    sample->gy = (int16_t)((raw[3] << 8) | raw[2]);
    sample->gz = (int16_t)((raw[5] << 8) | raw[4]);
    sample->ax = (int16_t)((raw[7] << 8) | raw[6]);
    sample->ay = (int16_t)((raw[9] << 8) | raw[8]);
    sample->az = (int16_t)((raw[11] << 8) | raw[10]);

    float ax = (float)sample->ax;
    float ay = (float)sample->ay;
    float az = (float)sample->az;
    sample->roll_deg = atan2f(ay, az) * 57.2957795f;
    sample->pitch_deg = atan2f(-ax, sqrtf(ay * ay + az * az)) * 57.2957795f;
    sample->yaw_deg = 0.0f;

    s_status.present = true;
    s_status.last_sample = *sample;
    s_status.last_error = ESP_OK;
    return ESP_OK;
}

esp_err_t day_imu_write_json_sample(FILE *file, const day_imu_sample_t *sample, bool first)
{
    if (!file || !sample) {
        return ESP_ERR_INVALID_ARG;
    }
    fprintf(file,
            "%s{\"t_us\":%lld,\"ax\":%d,\"ay\":%d,\"az\":%d,\"gx\":%d,\"gy\":%d,\"gz\":%d,"
            "\"roll\":%.3f,\"pitch\":%.3f,\"yaw\":%.3f}",
            first ? "" : ",",
            (long long)sample->t_us,
            sample->ax, sample->ay, sample->az,
            sample->gx, sample->gy, sample->gz,
            sample->roll_deg, sample->pitch_deg, sample->yaw_deg);
    return ESP_OK;
}

esp_err_t day_imu_configure_shake_wake(bool enabled)
{
    if (!s_dev) {
        s_status.present = false;
        s_status.last_error = ESP_ERR_NOT_FOUND;
        return enabled ? ESP_ERR_NOT_FOUND : ESP_OK;
    }
    esp_err_t ret = day_i2c_write_reg(s_dev, REG_WAKE_UP_THS, enabled ? 0x08 : 0x00);
    if (ret == ESP_OK) {
        ret = day_i2c_write_reg(s_dev, REG_MD1_CFG, enabled ? 0x20 : 0x00);
    }
    gpio_wakeup_enable(DAY_PIN_IMU_INT, GPIO_INTR_HIGH_LEVEL);
    return ret;
}

esp_err_t day_imu_enter_sleep(void)
{
    if (!s_dev) {
        return ESP_OK;
    }
    esp_err_t ret = day_i2c_write_reg(s_dev, REG_CTRL1_XL, 0x00);
    if (ret == ESP_OK) {
        ret = day_i2c_write_reg(s_dev, REG_CTRL2_G, 0x00);
    }
    s_status.last_error = ret;
    return ret;
}

day_imu_status_t day_imu_get_status(void)
{
    return s_status;
}
