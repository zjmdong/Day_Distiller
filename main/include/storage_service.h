#pragma once

#include <stddef.h>
#include "day_types.h"
#include "esp_err.h"

esp_err_t day_storage_init(void);
esp_err_t day_storage_refresh_status(day_storage_status_t *status);
day_storage_status_t day_storage_get_status(void);
esp_err_t day_storage_make_record_paths(day_record_paths_t *paths, time_t record_time);
esp_err_t day_storage_files_json(char *buffer, size_t len);
void day_storage_deinit(void);
