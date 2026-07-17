#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "day_types.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DAY_SETTINGS_SCHEMA_VERSION 1
#define DAY_SETTINGS_NVS_SCHEMA_VERSION 2
#define DAY_SETTINGS_ANY_REVISION UINT32_MAX
#define DAY_SETTINGS_FIELD_MAX 48
#define DAY_SETTINGS_REASON_MAX 80

typedef struct {
    uint16_t schema_version;
    uint32_t revision;
    day_config_t config;
} day_settings_snapshot_t;

typedef struct {
    uint32_t revision;
    bool changed;
    char field[DAY_SETTINGS_FIELD_MAX];
    char reason[DAY_SETTINGS_REASON_MAX];
} day_settings_result_t;

esp_err_t day_settings_init(void);
esp_err_t day_settings_get(day_settings_snapshot_t *snapshot);
esp_err_t day_settings_get_config(day_config_t *config, uint32_t *revision);
esp_err_t day_settings_validate(const day_config_t *config,
                                char *field, size_t field_len,
                                char *reason, size_t reason_len);
esp_err_t day_settings_replace(const day_config_t *candidate,
                               uint32_t expected_revision,
                               day_settings_result_t *result);
bool day_settings_recording_fields_changed(const day_config_t *before,
                                           const day_config_t *after);
void day_settings_format_recording_color(const day_config_t *config, char out[8]);

#ifdef __cplusplus
}
#endif
