#pragma once

#include <stddef.h>
#include <stdint.h>
#include "driver/i2c_master.h"
#include "esp_err.h"

esp_err_t day_board_init(void);
i2c_master_bus_handle_t day_board_sensor_i2c_bus(void);
i2c_master_bus_handle_t day_board_camera_i2c_bus(void);

esp_err_t day_i2c_add_device(i2c_master_bus_handle_t bus, uint8_t address, uint32_t speed_hz,
                             i2c_master_dev_handle_t *out_dev);
esp_err_t day_i2c_read_reg(i2c_master_dev_handle_t dev, uint8_t reg, uint8_t *data, size_t len);
esp_err_t day_i2c_write_reg(i2c_master_dev_handle_t dev, uint8_t reg, uint8_t value);
esp_err_t day_i2c_write(i2c_master_dev_handle_t dev, const uint8_t *data, size_t len);
