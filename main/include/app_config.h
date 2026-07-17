#pragma once

#include "day_types.h"
#include "esp_err.h"

void day_config_defaults(day_config_t *cfg);
uint32_t day_config_camera_max_fps(int framesize);
void day_config_normalize(day_config_t *cfg);
bool day_config_record_framesize_valid(int framesize);
bool day_config_record_fps_valid(uint32_t fps);
