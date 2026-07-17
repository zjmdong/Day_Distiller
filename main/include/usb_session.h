#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "usb_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DAY_USB_BOOT_INTENT_SCHEMA 1
#define DAY_USB_ACTIVE_EXPORT_ID_MAX 40

typedef enum {
    DAY_USB_BOOT_TARGET_NORMAL = 0,
    DAY_USB_BOOT_TARGET_MSC = 1,
    DAY_USB_BOOT_TARGET_SERIAL_MAINTENANCE = 2,
} day_usb_boot_target_t;

bool day_usb_session_intent_valid(void);
day_usb_boot_target_t day_usb_session_boot_target(void);
day_usb_access_t day_usb_session_msc_access(void);
uint64_t day_usb_session_id(void);
void day_usb_session_begin_maintenance(void);
esp_err_t day_usb_session_prepare_msc(day_usb_access_t access, const char *active_export_id);
esp_err_t day_usb_session_prepare_serial_maintenance(void);
void day_usb_session_end(void);

#ifdef __cplusplus
}
#endif
