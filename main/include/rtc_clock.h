#pragma once

#include <time.h>
#include "day_types.h"
#include "esp_err.h"

esp_err_t day_rtc_init(void);
esp_err_t day_rtc_read(day_rtc_status_t *status);
esp_err_t day_rtc_write_time(time_t unix_time);
day_rtc_status_t day_rtc_get_last(void);
