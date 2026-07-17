#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DAY_DEVICE_ID_MAX 16

esp_err_t day_device_identity_init(void);
const char *day_device_id(void);
const char *day_device_usb_serial(void);

#ifdef __cplusplus
}
#endif
