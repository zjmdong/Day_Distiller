#pragma once

#include "day_types.h"
#include "esp_err.h"

typedef esp_err_t (*day_web_get_status_cb_t)(day_device_status_t *status);
typedef esp_err_t (*day_web_save_config_cb_t)(const day_config_t *config);
typedef esp_err_t (*day_web_record_cb_t)(void);

typedef struct {
    day_web_get_status_cb_t get_status;
    day_web_save_config_cb_t save_config;
    day_web_record_cb_t record_once;
    day_config_t *config;
} day_web_callbacks_t;

esp_err_t day_web_start(const day_web_callbacks_t *callbacks);
esp_err_t day_web_stop(void);
