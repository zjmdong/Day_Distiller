#include "board.h"

#include <stdio.h>
#include "day_pins.h"
#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_log.h"

static const char *TAG = "day_board";

static i2c_master_bus_handle_t s_sensor_bus;

static esp_err_t init_i2c_bus(i2c_port_num_t port, gpio_num_t sda, gpio_num_t scl,
                              i2c_master_bus_handle_t *out_bus)
{
    if (*out_bus) {
        return ESP_OK;
    }

    i2c_master_bus_config_t cfg = {
        .i2c_port = port,
        .sda_io_num = sda,
        .scl_io_num = scl,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .trans_queue_depth = 0,
        .flags.enable_internal_pullup = true,
    };
    esp_err_t ret = i2c_new_master_bus(&cfg, out_bus);
    if (ret == ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "I2C%d already initialized by another module", port);
        return ESP_OK;
    }
    return ret;
}

static void log_sensor_i2c_devices(void)
{
    char found[160] = {0};
    size_t used = 0;
    for (uint8_t addr = 0x08; addr < 0x78; ++addr) {
        if (i2c_master_probe(s_sensor_bus, addr, 50) == ESP_OK) {
            int written = snprintf(found + used, sizeof(found) - used, "%s0x%02x",
                                   used == 0 ? "" : ",", addr);
            if (written < 0 || (size_t)written >= sizeof(found) - used) {
                break;
            }
            used += written;
        }
    }
    ESP_LOGI(TAG, "I2C%d devices: %s", DAY_I2C_SENSOR_PORT, used == 0 ? "none" : found);
}

esp_err_t day_board_init(void)
{
    /* The module has no routed PWDN/power-enable signal. Keep the documented
     * sensor RESET asserted until an explicit record or preview request lets
     * esp-camera take ownership of the pin.
     */
    gpio_deep_sleep_hold_dis();
    ESP_ERROR_CHECK_WITHOUT_ABORT(gpio_hold_dis(DAY_PIN_CAM_RST));
    gpio_config_t camera_reset_cfg = {
        .pin_bit_mask = 1ULL << DAY_PIN_CAM_RST,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_RETURN_ON_ERROR(gpio_config(&camera_reset_cfg), TAG, "camera reset config failed");
    ESP_RETURN_ON_ERROR(gpio_set_level(DAY_PIN_CAM_RST, 0), TAG, "camera reset assert failed");

    ESP_RETURN_ON_ERROR(init_i2c_bus(DAY_I2C_SENSOR_PORT, DAY_PIN_I2C0_SDA, DAY_PIN_I2C0_SCL, &s_sensor_bus),
                        TAG, "sensor I2C init failed");
    log_sensor_i2c_devices();

    gpio_config_t imu_int_cfg = {
        .pin_bit_mask = 1ULL << DAY_PIN_IMU_INT,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK_WITHOUT_ABORT(gpio_config(&imu_int_cfg));
    return ESP_OK;
}

i2c_master_bus_handle_t day_board_sensor_i2c_bus(void)
{
    return s_sensor_bus;
}

i2c_master_bus_handle_t day_board_camera_i2c_bus(void)
{
    return NULL;
}

esp_err_t day_i2c_add_device(i2c_master_bus_handle_t bus, uint8_t address, uint32_t speed_hz,
                             i2c_master_dev_handle_t *out_dev)
{
    if (!bus || !out_dev) {
        return ESP_ERR_INVALID_ARG;
    }
    i2c_device_config_t dev_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = address,
        .scl_speed_hz = speed_hz,
    };
    return i2c_master_bus_add_device(bus, &dev_cfg, out_dev);
}

esp_err_t day_i2c_read_reg(i2c_master_dev_handle_t dev, uint8_t reg, uint8_t *data, size_t len)
{
    if (!dev || !data || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    return i2c_master_transmit_receive(dev, &reg, 1, data, len, 100);
}

esp_err_t day_i2c_write_reg(i2c_master_dev_handle_t dev, uint8_t reg, uint8_t value)
{
    uint8_t buf[2] = {reg, value};
    return day_i2c_write(dev, buf, sizeof(buf));
}

esp_err_t day_i2c_write(i2c_master_dev_handle_t dev, const uint8_t *data, size_t len)
{
    if (!dev || !data || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    return i2c_master_transmit(dev, data, len, 100);
}
