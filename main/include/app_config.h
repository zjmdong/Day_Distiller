#pragma once

#include "day_types.h"
#include "esp_err.h"

void day_config_defaults(day_config_t *cfg);
void day_config_normalize(day_config_t *cfg);
esp_err_t day_config_load(day_config_t *cfg);
esp_err_t day_config_save(const day_config_t *cfg);
