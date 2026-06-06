#pragma once

#include <stdio.h>
#include "day_types.h"
#include "esp_err.h"

esp_err_t day_imu_init(uint32_t sample_rate_hz);
void day_imu_set_orientation(uint8_t orientation);
esp_err_t day_imu_read(day_imu_sample_t *sample);
esp_err_t day_imu_write_json_sample(FILE *file, const day_imu_sample_t *sample, bool first);
esp_err_t day_imu_configure_shake_wake(bool enabled);
esp_err_t day_imu_enter_sleep(void);
day_imu_status_t day_imu_get_status(void);
