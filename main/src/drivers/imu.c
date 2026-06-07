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
#define GYRO_DPS_PER_LSB 0.00875f
#define RAD_TO_DEG 57.2957795f
#define DEG_TO_RAD 0.0174532925f
#define IMU_ACCEL_1G_LSB 16384.0f
#define MADGWICK_BETA_MOVING 0.08f
#define MADGWICK_BETA_STILL 0.22f

static const char *TAG = "day_imu";

static i2c_master_dev_handle_t s_dev;
static uint8_t s_orientation;
static int64_t s_last_motion_us;
static float s_roll_deg;
static float s_pitch_deg;
static float s_yaw_deg;
static float s_q0 = 1.0f;
static float s_q1;
static float s_q2;
static float s_q3;
static float s_gyro_bias_x_dps;
static float s_gyro_bias_y_dps;
static float s_gyro_bias_z_dps;
static bool s_quat_initialized;
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
    if (ret == ESP_OK) {
        s_last_motion_us = 0;
        s_roll_deg = 0.0f;
        s_pitch_deg = 0.0f;
        s_yaw_deg = 0.0f;
        s_q0 = 1.0f;
        s_q1 = 0.0f;
        s_q2 = 0.0f;
        s_q3 = 0.0f;
        s_gyro_bias_x_dps = 0.0f;
        s_gyro_bias_y_dps = 0.0f;
        s_gyro_bias_z_dps = 0.0f;
        s_quat_initialized = false;
    }
    return ret;
}

void day_imu_set_orientation(uint8_t orientation)
{
    s_orientation = orientation <= 5 ? orientation : 0;
    s_last_motion_us = 0;
    s_roll_deg = 0.0f;
    s_pitch_deg = 0.0f;
    s_yaw_deg = 0.0f;
    s_q0 = 1.0f;
    s_q1 = 0.0f;
    s_q2 = 0.0f;
    s_q3 = 0.0f;
    s_gyro_bias_x_dps = 0.0f;
    s_gyro_bias_y_dps = 0.0f;
    s_gyro_bias_z_dps = 0.0f;
    s_quat_initialized = false;
}

static void map_device_axes(float ax, float ay, float az, float *out_x, float *out_y, float *out_z)
{
    /*
     * Hardware default from the provided front-side photo:
     * +X points to the device right edge, +Y points to the device top edge,
     * +Z points out of the front face.
     */
    switch (s_orientation) {
    case 1: /* Rotate +90 degrees around sensor Z. */
        *out_x = ay;
        *out_y = -ax;
        *out_z = az;
        break;
    case 2: /* Rotate -90 degrees around sensor Z. */
        *out_x = -ay;
        *out_y = ax;
        *out_z = az;
        break;
    case 3: /* Rotate 180 degrees around sensor Z. */
        *out_x = -ax;
        *out_y = -ay;
        *out_z = az;
        break;
    case 4: /* Flip device X. */
        *out_x = -ax;
        *out_y = ay;
        *out_z = az;
        break;
    case 5: /* Flip device Y. */
        *out_x = ax;
        *out_y = -ay;
        *out_z = az;
        break;
    default:
        *out_x = ax;
        *out_y = ay;
        *out_z = az;
        break;
    }
}

static float wrap_degrees(float value)
{
    while (value > 180.0f) {
        value -= 360.0f;
    }
    while (value < -180.0f) {
        value += 360.0f;
    }
    return value;
}

static float gyro_deadband(float dps)
{
    return fabsf(dps) < 0.03f ? 0.0f : dps;
}

static bool imu_is_stationary(float ax, float ay, float az, float gx_dps, float gy_dps, float gz_dps)
{
    float accel_norm = sqrtf(ax * ax + ay * ay + az * az);
    float gyro_norm = sqrtf(gx_dps * gx_dps + gy_dps * gy_dps + gz_dps * gz_dps);
    return fabsf(accel_norm - IMU_ACCEL_1G_LSB) < 1800.0f && gyro_norm < 2.5f;
}

static void update_gyro_bias(bool stationary, float gx_dps, float gy_dps, float gz_dps)
{
    if (!stationary) {
        return;
    }
    const float alpha = 0.01f;
    s_gyro_bias_x_dps += (gx_dps - s_gyro_bias_x_dps) * alpha;
    s_gyro_bias_y_dps += (gy_dps - s_gyro_bias_y_dps) * alpha;
    s_gyro_bias_z_dps += (gz_dps - s_gyro_bias_z_dps) * alpha;
}

static void normalize_quaternion(void)
{
    float norm = sqrtf(s_q0 * s_q0 + s_q1 * s_q1 + s_q2 * s_q2 + s_q3 * s_q3);
    if (norm <= 0.0f) {
        s_q0 = 1.0f;
        s_q1 = 0.0f;
        s_q2 = 0.0f;
        s_q3 = 0.0f;
        return;
    }
    norm = 1.0f / norm;
    s_q0 *= norm;
    s_q1 *= norm;
    s_q2 *= norm;
    s_q3 *= norm;
}

static void set_quaternion_from_euler(float roll, float pitch, float yaw)
{
    float cr = cosf(roll * 0.5f);
    float sr = sinf(roll * 0.5f);
    float cp = cosf(pitch * 0.5f);
    float sp = sinf(pitch * 0.5f);
    float cy = cosf(yaw * 0.5f);
    float sy = sinf(yaw * 0.5f);
    s_q0 = cr * cp * cy + sr * sp * sy;
    s_q1 = sr * cp * cy - cr * sp * sy;
    s_q2 = cr * sp * cy + sr * cp * sy;
    s_q3 = cr * cp * sy - sr * sp * cy;
    normalize_quaternion();
    s_quat_initialized = true;
}

static void init_quaternion_from_accel(float ax, float ay, float az, float yaw_deg)
{
    float roll = atan2f(ay, az);
    float pitch = atan2f(-ax, sqrtf(ay * ay + az * az));
    set_quaternion_from_euler(roll, pitch, yaw_deg * DEG_TO_RAD);
}

static void update_euler_from_quaternion(void)
{
    float sinr_cosp = 2.0f * (s_q0 * s_q1 + s_q2 * s_q3);
    float cosr_cosp = 1.0f - 2.0f * (s_q1 * s_q1 + s_q2 * s_q2);
    float sinp = 2.0f * (s_q0 * s_q2 - s_q3 * s_q1);
    float siny_cosp = 2.0f * (s_q0 * s_q3 + s_q1 * s_q2);
    float cosy_cosp = 1.0f - 2.0f * (s_q2 * s_q2 + s_q3 * s_q3);

    s_roll_deg = atan2f(sinr_cosp, cosr_cosp) * RAD_TO_DEG;
    if (sinp > 1.0f) {
        sinp = 1.0f;
    } else if (sinp < -1.0f) {
        sinp = -1.0f;
    }
    s_pitch_deg = asinf(sinp) * RAD_TO_DEG;
    s_yaw_deg = wrap_degrees(atan2f(siny_cosp, cosy_cosp) * RAD_TO_DEG);
}

static void madgwick_update_imu(float gx, float gy, float gz, float ax, float ay, float az, float dt, float beta)
{
    float q0 = s_q0;
    float q1 = s_q1;
    float q2 = s_q2;
    float q3 = s_q3;
    float q_dot0 = 0.5f * (-q1 * gx - q2 * gy - q3 * gz);
    float q_dot1 = 0.5f * (q0 * gx + q2 * gz - q3 * gy);
    float q_dot2 = 0.5f * (q0 * gy - q1 * gz + q3 * gx);
    float q_dot3 = 0.5f * (q0 * gz + q1 * gy - q2 * gx);

    float accel_norm = sqrtf(ax * ax + ay * ay + az * az);
    if (accel_norm > 1.0f) {
        accel_norm = 1.0f / accel_norm;
        ax *= accel_norm;
        ay *= accel_norm;
        az *= accel_norm;

        float _2q0 = 2.0f * q0;
        float _2q1 = 2.0f * q1;
        float _2q2 = 2.0f * q2;
        float _2q3 = 2.0f * q3;
        float _4q0 = 4.0f * q0;
        float _4q1 = 4.0f * q1;
        float _4q2 = 4.0f * q2;
        float _8q1 = 8.0f * q1;
        float _8q2 = 8.0f * q2;
        float q0q0 = q0 * q0;
        float q1q1 = q1 * q1;
        float q2q2 = q2 * q2;
        float q3q3 = q3 * q3;

        float s0 = _4q0 * q2q2 + _2q2 * ax + _4q0 * q1q1 - _2q1 * ay;
        float s1 = _4q1 * q3q3 - _2q3 * ax + 4.0f * q0q0 * q1 - _2q0 * ay -
                   _4q1 + _8q1 * q1q1 + _8q1 * q2q2 + _4q1 * az;
        float s2 = 4.0f * q0q0 * q2 + _2q0 * ax + _4q2 * q3q3 - _2q3 * ay -
                   _4q2 + _8q2 * q1q1 + _8q2 * q2q2 + _4q2 * az;
        float s3 = 4.0f * q1q1 * q3 - _2q1 * ax + 4.0f * q2q2 * q3 - _2q2 * ay;
        float step_norm = sqrtf(s0 * s0 + s1 * s1 + s2 * s2 + s3 * s3);
        if (step_norm > 0.0f) {
            step_norm = 1.0f / step_norm;
            q_dot0 -= beta * s0 * step_norm;
            q_dot1 -= beta * s1 * step_norm;
            q_dot2 -= beta * s2 * step_norm;
            q_dot3 -= beta * s3 * step_norm;
        }
    }

    s_q0 += q_dot0 * dt;
    s_q1 += q_dot1 * dt;
    s_q2 += q_dot2 * dt;
    s_q3 += q_dot3 * dt;
    normalize_quaternion();
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

    float ax = 0.0f;
    float ay = 0.0f;
    float az = 0.0f;
    float gx = 0.0f;
    float gy = 0.0f;
    float gz = 0.0f;
    map_device_axes((float)sample->ax, (float)sample->ay, (float)sample->az, &ax, &ay, &az);
    map_device_axes((float)sample->gx, (float)sample->gy, (float)sample->gz, &gx, &gy, &gz);

    int64_t now_us = sample->t_us;
    float dt = s_last_motion_us > 0 ? (float)(now_us - s_last_motion_us) / 1000000.0f : 0.0f;
    if (!s_quat_initialized) {
        init_quaternion_from_accel(ax, ay, az, 0.0f);
    } else if (dt <= 0.0f || dt > 0.25f) {
        init_quaternion_from_accel(ax, ay, az, s_yaw_deg);
    } else {
        float gx_raw_dps = gx * GYRO_DPS_PER_LSB;
        float gy_raw_dps = gy * GYRO_DPS_PER_LSB;
        float gz_raw_dps = gz * GYRO_DPS_PER_LSB;
        bool stationary = imu_is_stationary(ax, ay, az, gx_raw_dps, gy_raw_dps, gz_raw_dps);
        update_gyro_bias(stationary, gx_raw_dps, gy_raw_dps, gz_raw_dps);
        float gx_dps = gyro_deadband(gx_raw_dps - s_gyro_bias_x_dps);
        float gy_dps = gyro_deadband(gy_raw_dps - s_gyro_bias_y_dps);
        float gz_dps = gyro_deadband(gz_raw_dps - s_gyro_bias_z_dps);
        float beta = stationary ? MADGWICK_BETA_STILL : MADGWICK_BETA_MOVING;
        madgwick_update_imu(gx_dps * DEG_TO_RAD, gy_dps * DEG_TO_RAD, gz_dps * DEG_TO_RAD,
                            ax, ay, az, dt, beta);
    }
    s_last_motion_us = now_us;
    update_euler_from_quaternion();
    sample->roll_deg = s_roll_deg;
    sample->pitch_deg = s_pitch_deg;
    sample->yaw_deg = s_yaw_deg;
    sample->q0 = s_q0;
    sample->q1 = s_q1;
    sample->q2 = s_q2;
    sample->q3 = s_q3;

    s_status.present = true;
    s_status.last_sample = *sample;
    s_status.last_error = ESP_OK;
    return ESP_OK;
}

esp_err_t day_imu_write_json_sample(FILE *file, const day_imu_sample_t *sample, uint32_t id, bool first)
{
    if (!file || !sample) {
        return ESP_ERR_INVALID_ARG;
    }
    fprintf(file,
            "%s    {\"id\":%lu,\"time_us\":%lld,\"time_s\":%.6f,"
            "\"raw\":{\"ax\":%d,\"ay\":%d,\"az\":%d,\"gx\":%d,\"gy\":%d,\"gz\":%d},"
            "\"pose\":{\"roll\":%.3f,\"pitch\":%.3f,\"yaw\":%.3f},"
            "\"quat\":{\"q0\":%.6f,\"q1\":%.6f,\"q2\":%.6f,\"q3\":%.6f}}",
            first ? "" : ",\n",
            (unsigned long)id,
            (long long)sample->t_us,
            (double)sample->t_us / 1000000.0,
            sample->ax, sample->ay, sample->az,
            sample->gx, sample->gy, sample->gz,
            sample->roll_deg, sample->pitch_deg, sample->yaw_deg,
            sample->q0, sample->q1, sample->q2, sample->q3);
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
