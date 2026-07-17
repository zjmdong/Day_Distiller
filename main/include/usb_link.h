#pragma once

#include <stdbool.h>
#include "esp_err.h"
#include "usb_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef day_usb_access_t day_usb_link_access_t;

#define DAY_USB_LINK_ACCESS_RW DAY_USB_ACCESS_RW
#define DAY_USB_LINK_ACCESS_RO DAY_USB_ACCESS_RO

bool day_usb_link_should_run_msc_mode(void);
esp_err_t day_usb_link_start_serial_mode(void);
void day_usb_link_run_msc_mode(void);
bool day_usb_link_maintenance_active(void);
const char *day_usb_link_mode_name(void);

#ifdef __cplusplus
}
#endif
