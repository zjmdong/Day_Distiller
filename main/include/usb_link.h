#pragma once

#include <stdbool.h>
#include "device_settings.h"
#include "esp_err.h"
#include "usb_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef day_usb_access_t day_usb_link_access_t;

#define DAY_USB_LINK_ACCESS_RW DAY_USB_ACCESS_RW
#define DAY_USB_LINK_ACCESS_RO DAY_USB_ACCESS_RO

typedef esp_err_t (*day_usb_config_apply_callback_t)(const day_config_t *candidate,
                                                     uint32_t expected_revision,
                                                     day_settings_result_t *result);

bool day_usb_link_should_run_msc_mode(void);
bool day_usb_link_should_resume_maintenance(void);
void day_usb_link_boot_init(void);
esp_err_t day_usb_link_start_serial_mode(void);
esp_err_t day_usb_link_stop(void);
void day_usb_link_run_msc_mode(void);
bool day_usb_link_maintenance_active(void);
bool day_usb_link_host_connection_latched(void);
const char *day_usb_link_mode_name(void);
void day_usb_link_set_config_apply_callback(day_usb_config_apply_callback_t callback);

#ifdef __cplusplus
}
#endif
