#pragma once

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    DAY_USB_LINK_ACCESS_RW = 0,
    DAY_USB_LINK_ACCESS_RO,
} day_usb_link_access_t;

bool day_usb_link_should_run_msc_mode(void);
esp_err_t day_usb_link_start_serial_mode(void);
void day_usb_link_run_msc_mode(void);
bool day_usb_link_maintenance_active(void);
const char *day_usb_link_mode_name(void);

#ifdef __cplusplus
}
#endif
