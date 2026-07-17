#pragma once

#include <stdint.h>
#include "day_types.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DAY_DEVICE_STATUS_SCHEMA_VERSION 2

typedef enum {
    DAY_STATUS_REFRESH_NONE = 0,
    DAY_STATUS_REFRESH_BATTERY = 1U << 0,
    DAY_STATUS_REFRESH_RTC = 1U << 1,
    DAY_STATUS_REFRESH_STORAGE = 1U << 2,
    DAY_STATUS_REFRESH_ALL = DAY_STATUS_REFRESH_BATTERY |
                             DAY_STATUS_REFRESH_RTC |
                             DAY_STATUS_REFRESH_STORAGE,
} day_status_refresh_flags_t;

esp_err_t day_status_service_init(const day_config_t *config, uint32_t config_revision);
esp_err_t day_status_get_snapshot(day_device_status_t *snapshot);
esp_err_t day_status_set_config(const day_config_t *config, uint32_t config_revision);
esp_err_t day_status_refresh(uint32_t flags);
void day_status_set_clock_state(const char *source, const char *last_sync_source,
                                time_t last_sync_unix);
void day_status_set_low_battery_latched(bool latched);

#ifdef __cplusplus
}
#endif
