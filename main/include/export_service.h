#pragma once

#include <stddef.h>
#include "esp_err.h"
#include "usb_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t day_export_list_record_dates(const day_usb_pagination_args_t *args,
                                       char *response, size_t response_len,
                                       char *reason, size_t reason_len);
esp_err_t day_export_begin(const day_usb_begin_export_args_t *args,
                           char *response, size_t response_len,
                           char *reason, size_t reason_len);
esp_err_t day_export_get_status(const day_usb_get_export_status_args_t *args,
                                char *response, size_t response_len,
                                char *reason, size_t reason_len);
esp_err_t day_export_validate_prepared(const char export_ids[][DAY_USB_EXPORT_ID_MAX], size_t count,
                                       char *reason, size_t reason_len);
size_t day_export_active_count(void);

#ifdef __cplusplus
}
#endif
