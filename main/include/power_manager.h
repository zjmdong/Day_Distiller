#pragma once

#include "day_types.h"
#include "esp_err.h"

esp_err_t day_power_enter_deep_sleep(const day_config_t *cfg);
void day_power_init(void);
bool day_power_low_battery_latched(void);
void day_power_latch_low_battery(void);
esp_err_t day_power_enter_locked_sleep(void);
