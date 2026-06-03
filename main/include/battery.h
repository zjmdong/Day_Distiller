#pragma once

#include "day_types.h"
#include "esp_err.h"

esp_err_t day_battery_init(void);
esp_err_t day_battery_read(day_battery_status_t *status);
day_battery_status_t day_battery_get_last(void);
