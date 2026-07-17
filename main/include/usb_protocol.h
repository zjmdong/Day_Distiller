#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DAY_USB_PROTO_VERSION 1
#define DAY_USB_PROTO_MAGIC0 'D'
#define DAY_USB_PROTO_MAGIC1 'D'
#define DAY_USB_FRAME_MAX 2048
#define DAY_USB_PAYLOAD_MAX 1600
#define DAY_USB_FRAME_HEADER_LEN 20
#define DAY_USB_FIRMWARE_VERSION "2.0.0"
#define DAY_USB_EXPORT_ID_MAX 40
#define DAY_USB_CLIENT_REQUEST_ID_MAX 64
#define DAY_USB_EXPORT_IDS_MAX 32

typedef enum {
    DAY_USB_FRAME_REQUEST = 1,
    DAY_USB_FRAME_RESPONSE = 2,
    DAY_USB_FRAME_EVENT = 3,
} day_usb_frame_type_t;

typedef enum {
    DAY_USB_CMD_HELLO = 1,
    DAY_USB_CMD_PING = 2,
    DAY_USB_CMD_GET_STATUS = 3,
    DAY_USB_CMD_ENTER_MSC = 4,
    DAY_USB_CMD_EXIT_MSC = 5,
    DAY_USB_CMD_BEGIN_EXPORT = 6,
    DAY_USB_CMD_COMMIT_EXPORT_DELETE = 7,
    DAY_USB_CMD_ABORT_EXPORT = 8,
    DAY_USB_CMD_END_SESSION = 9,
    DAY_USB_CMD_GET_EXPORT_STATUS = 10,
    DAY_USB_CMD_LIST_RECORD_DATES = 11,
} day_usb_command_t;

typedef enum {
    DAY_USB_STATUS_OK = 0,
    DAY_USB_STATUS_BAD_FRAME = 1,
    DAY_USB_STATUS_UNSUPPORTED_VERSION = 2,
    DAY_USB_STATUS_UNSUPPORTED_CMD = 3,
    DAY_USB_STATUS_INVALID_ARG = 4,
    DAY_USB_STATUS_BUSY = 5,
    DAY_USB_STATUS_STORAGE_ERROR = 6,
    DAY_USB_STATUS_BAD_STATE = 7,
    DAY_USB_STATUS_TIMEOUT = 8,
} day_usb_status_t;

typedef struct __attribute__((packed)) {
    uint8_t magic[2];
    uint8_t version;
    uint8_t type;
    uint8_t flags;
    uint8_t reserved;
    uint16_t header_len;
    uint32_t seq;
    uint16_t cmd;
    uint16_t status;
    uint32_t payload_len;
} day_usb_frame_header_t;

typedef enum {
    DAY_USB_ACCESS_RW = 0,
    DAY_USB_ACCESS_RO,
} day_usb_access_t;

typedef enum {
    DAY_USB_NEXT_MODE_NORMAL = 0,
    DAY_USB_NEXT_MODE_MAINTENANCE,
} day_usb_next_mode_t;

typedef struct {
    day_usb_access_t access;
    size_t export_id_count;
    char export_ids[DAY_USB_EXPORT_IDS_MAX][DAY_USB_EXPORT_ID_MAX];
} day_usb_enter_msc_args_t;

typedef struct {
    bool force;
    day_usb_next_mode_t next_mode;
} day_usb_exit_msc_args_t;

typedef struct {
    char date[11];
    char client_request_id[DAY_USB_CLIENT_REQUEST_ID_MAX + 1];
} day_usb_begin_export_args_t;

typedef struct {
    uint32_t cursor;
    uint32_t limit;
} day_usb_pagination_args_t;

typedef struct {
    bool has_export_id;
    char export_id[DAY_USB_EXPORT_ID_MAX];
    uint32_t cursor;
    uint32_t limit;
} day_usb_get_export_status_args_t;

typedef struct {
    char export_id[DAY_USB_EXPORT_ID_MAX];
    char manifest_sha256[65];
    uint32_t confirm_record_count;
} day_usb_commit_export_args_t;

typedef struct {
    char export_id[DAY_USB_EXPORT_ID_MAX];
} day_usb_export_id_args_t;

const char *day_usb_status_name(day_usb_status_t status);
uint32_t day_usb_crc32(uint32_t crc, const uint8_t *data, size_t len);
esp_err_t day_usb_validate_empty_args(const uint8_t *payload, size_t len, char *reason, size_t reason_len);
esp_err_t day_usb_parse_enter_msc_args(const uint8_t *payload, size_t len,
                                       day_usb_enter_msc_args_t *args, char *reason, size_t reason_len);
esp_err_t day_usb_parse_exit_msc_args(const uint8_t *payload, size_t len,
                                      day_usb_exit_msc_args_t *args, char *reason, size_t reason_len);
bool day_usb_export_id_valid(const char *value);
esp_err_t day_usb_parse_begin_export_args(const uint8_t *payload, size_t len,
                                          day_usb_begin_export_args_t *args, char *reason, size_t reason_len);
esp_err_t day_usb_parse_pagination_args(const uint8_t *payload, size_t len,
                                        day_usb_pagination_args_t *args, char *reason, size_t reason_len);
esp_err_t day_usb_parse_get_export_status_args(const uint8_t *payload, size_t len,
                                               day_usb_get_export_status_args_t *args,
                                               char *reason, size_t reason_len);
esp_err_t day_usb_parse_commit_export_args(const uint8_t *payload, size_t len,
                                           day_usb_commit_export_args_t *args,
                                           char *reason, size_t reason_len);
esp_err_t day_usb_parse_export_id_args(const uint8_t *payload, size_t len,
                                       day_usb_export_id_args_t *args,
                                       char *reason, size_t reason_len);

#ifdef __cplusplus
}
#endif
