#pragma once

#include "day_types.h"
#include "esp_err.h"

esp_err_t day_recorder_record_once(const day_config_t *cfg, day_record_paths_t *out_paths);
bool day_recorder_is_active(void);
esp_err_t day_recorder_get_last_error(void);
