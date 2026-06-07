#pragma once

#include <stddef.h>
#include "day_types.h"
#include "esp_err.h"

esp_err_t day_wifi_init(void);
esp_err_t day_wifi_sync_time(const day_config_t *cfg);
esp_err_t day_wifi_run_portal_window(uint32_t window_ms);
esp_err_t day_wifi_stop_portal(void);
esp_err_t day_wifi_restore_portal_ap_only(void);
esp_err_t day_wifi_scan_json(char *buffer, size_t len);
esp_err_t day_wifi_save_credentials_and_connect(day_config_t *cfg, const char *ssid, const char *password);
day_wifi_status_t day_wifi_get_status(void);
void day_wifi_deinit_for_sleep(void);
