#include "usb_link.h"

#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "device_identity.h"
#include "day_pins.h"
#include "driver/sdspi_host.h"
#include "driver/spi_common.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_vfs_fat.h"
#include "export_service.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "recorder.h"
#include "sdmmc_cmd.h"
#include "storage_service.h"
#include "tinyusb.h"
#include "tinyusb_cdc_acm.h"
#include "tinyusb_default_config.h"
#include "tinyusb_msc.h"
#include "tusb.h"
#include "usb_protocol.h"
#include "usb_session.h"

#define DAY_USB_MAINTENANCE_TIMEOUT_US (300LL * 1000LL * 1000LL)
#define DAY_USB_MSC_IDLE_TIMEOUT_US (900LL * 1000LL * 1000LL)

typedef enum {
    DAY_USB_MODE_SERIAL = 0,
    DAY_USB_MODE_MSC,
} day_usb_mode_t;

typedef struct {
    uint8_t data[DAY_USB_FRAME_MAX];
    size_t len;
    bool escape;
} day_usb_slip_decoder_t;

typedef struct {
    uint8_t data[CONFIG_TINYUSB_CDC_RX_BUFSIZE];
    size_t len;
    int itf;
} day_usb_rx_msg_t;

static const char *TAG = "day_usb_link";

static QueueHandle_t s_rx_queue;
static TaskHandle_t s_protocol_task;
static day_usb_slip_decoder_t s_decoder;
static uint8_t s_cdc_rx_buf[CONFIG_TINYUSB_CDC_RX_BUFSIZE];
static day_usb_mode_t s_mode = DAY_USB_MODE_SERIAL;
static tinyusb_cdcacm_itf_t s_protocol_port = TINYUSB_CDC_ACM_1;
static bool s_tinyusb_started;
static bool s_maintenance_active;
static int64_t s_last_protocol_us;
static int64_t s_last_msc_activity_us;
static bool s_msc_ejected;
static bool s_msc_read_only;
static sdmmc_card_t *s_msc_card;
static tinyusb_msc_storage_handle_t s_msc_storage;
static bool s_msc_bus_initialized;

enum {
    ITF_NUM_CDC0 = 0,
    ITF_NUM_CDC0_DATA,
    ITF_NUM_CDC1,
    ITF_NUM_CDC1_DATA,
    ITF_NUM_SERIAL_TOTAL,
};

enum {
    ITF_NUM_MSC_CDC = 0,
    ITF_NUM_MSC_CDC_DATA,
    ITF_NUM_MSC_STORAGE,
    ITF_NUM_MSC_TOTAL,
};

enum {
    EP_CDC0_NOTIF = 0x81,
    EP_CDC0_OUT = 0x02,
    EP_CDC0_IN = 0x82,
    EP_CDC1_NOTIF = 0x83,
    EP_CDC1_OUT = 0x04,
    EP_CDC1_IN = 0x84,
    EP_MSC_OUT = 0x03,
    EP_MSC_IN = 0x83,
};

#define DAY_USB_SERIAL_DESC_LEN (TUD_CONFIG_DESC_LEN + 2 * TUD_CDC_DESC_LEN)
#define DAY_USB_MSC_DESC_LEN (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN + TUD_MSC_DESC_LEN)

static tusb_desc_device_t s_serial_device_desc = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = 0x303A,
    .idProduct = 0x4020,
    .bcdDevice = 0x0100,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x03,
    .bNumConfigurations = 0x01,
};

static tusb_desc_device_t s_msc_device_desc = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = 0x303A,
    .idProduct = 0x4021,
    .bcdDevice = 0x0100,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x03,
    .bNumConfigurations = 0x01,
};

static const uint8_t s_serial_fs_desc[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_SERIAL_TOTAL, 0, DAY_USB_SERIAL_DESC_LEN,
                          TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC0, 4, EP_CDC0_NOTIF, 8, EP_CDC0_OUT, EP_CDC0_IN, 64),
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC1, 5, EP_CDC1_NOTIF, 8, EP_CDC1_OUT, EP_CDC1_IN, 64),
};

static const uint8_t s_msc_fs_desc[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_MSC_TOTAL, 0, DAY_USB_MSC_DESC_LEN,
                          TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),
    TUD_CDC_DESCRIPTOR(ITF_NUM_MSC_CDC, 5, EP_CDC0_NOTIF, 8, EP_CDC0_OUT, EP_CDC0_IN, 64),
    TUD_MSC_DESCRIPTOR(ITF_NUM_MSC_STORAGE, 6, EP_MSC_OUT, EP_MSC_IN, 64),
};

static const char *s_string_desc[] = {
    (const char[]){0x09, 0x04},
    "Day Distiller",
    "Day Distiller USB Link",
    "DD-USB-LINK",
    "Day Distiller Log",
    "Day Distiller Link",
    "Day Distiller TF",
};

static void put_u32_le(uint8_t *dst, uint32_t value)
{
    dst[0] = (uint8_t)(value & 0xff);
    dst[1] = (uint8_t)((value >> 8) & 0xff);
    dst[2] = (uint8_t)((value >> 16) & 0xff);
    dst[3] = (uint8_t)((value >> 24) & 0xff);
}

static uint32_t get_u32_le(const uint8_t *src)
{
    return (uint32_t)src[0] | ((uint32_t)src[1] << 8) | ((uint32_t)src[2] << 16) | ((uint32_t)src[3] << 24);
}

bool day_usb_link_should_run_msc_mode(void)
{
    return day_usb_session_boot_target() == DAY_USB_BOOT_TARGET_MSC;
}

bool day_usb_link_should_resume_maintenance(void)
{
    return day_usb_session_boot_target() == DAY_USB_BOOT_TARGET_SERIAL_MAINTENANCE;
}

const char *day_usb_link_mode_name(void)
{
    return s_mode == DAY_USB_MODE_MSC ? "msc" : "serial";
}

static int usb_log_vprintf(const char *fmt, va_list args)
{
    if (!s_tinyusb_started || s_mode != DAY_USB_MODE_SERIAL || !tinyusb_cdcacm_initialized(TINYUSB_CDC_ACM_0)) {
        return 0;
    }
    char buffer[256];
    int written = vsnprintf(buffer, sizeof(buffer), fmt, args);
    if (written <= 0) {
        return written;
    }
    size_t out_len = (size_t)written;
    if (out_len >= sizeof(buffer)) {
        out_len = sizeof(buffer) - 1;
    }
    tinyusb_cdcacm_write_queue(TINYUSB_CDC_ACM_0, (const uint8_t *)buffer, out_len);
    tinyusb_cdcacm_write_flush(TINYUSB_CDC_ACM_0, 0);
    return written;
}

static void cdc_rx_callback(int itf, cdcacm_event_t *event)
{
    (void)event;
    size_t rx_size = 0;
    if (tinyusb_cdcacm_read((tinyusb_cdcacm_itf_t)itf, s_cdc_rx_buf, sizeof(s_cdc_rx_buf), &rx_size) != ESP_OK ||
        rx_size == 0) {
        return;
    }
    if ((tinyusb_cdcacm_itf_t)itf != s_protocol_port || !s_rx_queue) {
        return;
    }
    day_usb_rx_msg_t msg = {
        .len = rx_size,
        .itf = itf,
    };
    memcpy(msg.data, s_cdc_rx_buf, rx_size);
    (void)xQueueSend(s_rx_queue, &msg, 0);
}

static esp_err_t send_slip_bytes(const uint8_t *data, size_t len)
{
    static const uint8_t end = 0xC0;
    static const uint8_t esc = 0xDB;
    static const uint8_t esc_end = 0xDC;
    static const uint8_t esc_esc = 0xDD;

    tinyusb_cdcacm_write_queue(s_protocol_port, &end, 1);
    for (size_t i = 0; i < len; ++i) {
        if (data[i] == end) {
            tinyusb_cdcacm_write_queue(s_protocol_port, &esc, 1);
            tinyusb_cdcacm_write_queue(s_protocol_port, &esc_end, 1);
        } else if (data[i] == esc) {
            tinyusb_cdcacm_write_queue(s_protocol_port, &esc, 1);
            tinyusb_cdcacm_write_queue(s_protocol_port, &esc_esc, 1);
        } else {
            tinyusb_cdcacm_write_queue(s_protocol_port, &data[i], 1);
        }
    }
    tinyusb_cdcacm_write_queue(s_protocol_port, &end, 1);
    return tinyusb_cdcacm_write_flush(s_protocol_port, pdMS_TO_TICKS(100));
}

static esp_err_t send_response(uint32_t seq, day_usb_command_t cmd, day_usb_status_t status, const char *json)
{
    uint8_t frame[sizeof(day_usb_frame_header_t) + DAY_USB_PAYLOAD_MAX + 4];
    day_usb_frame_header_t hdr = {
        .magic = {DAY_USB_PROTO_MAGIC0, DAY_USB_PROTO_MAGIC1},
        .version = DAY_USB_PROTO_VERSION,
        .type = DAY_USB_FRAME_RESPONSE,
        .header_len = sizeof(day_usb_frame_header_t),
        .seq = seq,
        .cmd = (uint16_t)cmd,
        .status = (uint16_t)status,
        .payload_len = json ? (uint32_t)strlen(json) : 0,
    };
    if (hdr.payload_len > DAY_USB_PAYLOAD_MAX) {
        return ESP_ERR_NO_MEM;
    }
    memcpy(frame, &hdr, sizeof(hdr));
    if (hdr.payload_len) {
        memcpy(frame + sizeof(hdr), json, hdr.payload_len);
    }
    uint32_t crc = day_usb_crc32(0, frame, sizeof(hdr) + hdr.payload_len);
    put_u32_le(frame + sizeof(hdr) + hdr.payload_len, crc);
    return send_slip_bytes(frame, sizeof(hdr) + hdr.payload_len + 4);
}

static int storage_status_json(char *buffer, size_t len)
{
    if (s_mode == DAY_USB_MODE_MSC) {
        bool ready = s_msc_card != NULL;
        uint32_t sector_size = ready ? s_msc_card->csd.sector_size : 0;
        uint32_t sector_count = ready ? s_msc_card->csd.capacity : 0;
        uint64_t total_bytes = (uint64_t)sector_count * sector_size;
        return snprintf(buffer, len,
                        "{\"ready\":%s,\"mounted\":false,\"usb_exposed\":%s,"
                        "\"read_only\":%s,\"ejected\":%s,\"sector_size\":%" PRIu32 ","
                        "\"sector_count\":%" PRIu32 ",\"total_bytes\":%" PRIu64 "}",
                        ready ? "true" : "false",
                        ready ? "true" : "false",
                        s_msc_read_only ? "true" : "false",
                        s_msc_ejected ? "true" : "false",
                        sector_size,
                        sector_count,
                        total_bytes);
    } else {
        day_storage_status_t status = day_storage_get_status();
        return snprintf(buffer, len,
                        "{\"ready\":%s,\"mounted\":%s,\"usb_exposed\":false,"
                        "\"total_bytes\":%" PRIu64 ",\"free_bytes\":%" PRIu64 ","
                        "\"last_error\":%d}",
                        status.mounted ? "true" : "false",
                        status.mounted ? "true" : "false",
                        status.total_bytes,
                        status.free_bytes,
                        (int)status.last_error);
    }
}

static char *make_status_payload(void)
{
    char storage[384];
    int storage_len = storage_status_json(storage, sizeof(storage));
    if (storage_len < 0 || storage_len >= (int)sizeof(storage)) {
        return NULL;
    }
    char *payload = malloc(DAY_USB_PAYLOAD_MAX);
    if (!payload) {
        return NULL;
    }
    bool recording = s_mode == DAY_USB_MODE_SERIAL ? day_recorder_is_active() : false;
    int written = snprintf(payload, DAY_USB_PAYLOAD_MAX,
                           "{\"protocol\":%d,\"firmware_version\":\"%s\","
                           "\"device\":\"Day Distiller\",\"device_id\":\"%s\",\"mode\":\"%s\","
                           "\"runtime_state\":\"%s\",\"session_id\":\"%016" PRIx64 "\","
                           "\"maintenance\":%s,\"recording\":%s,"
                           "\"usb_full_speed\":true,\"metadata_schemas\":[1,2],\"active_exports\":%u,"
                           "\"storage\":%s,"
                           "\"capabilities\":[\"enter_msc\",\"exit_msc\",\"msc_rw\","
                           "\"msc_ro\",\"slip_crc32_json\",\"transactional_export_v2\","
                           "\"export_transactions\",\"export_manifest_v2\","
                           "\"list_record_dates\",\"get_export_status\","
                           "\"commit_export_delete\",\"abort_export\",\"exit_to_maintenance\","
                           "\"end_session\"]}",
                           DAY_USB_PROTO_VERSION,
                           DAY_USB_FIRMWARE_VERSION,
                           day_device_id(),
                           day_usb_link_mode_name(),
                           s_mode == DAY_USB_MODE_MSC ? "msc_read_only" :
                           (s_maintenance_active ? "serial_maintenance" : "normal_boot"),
                           day_usb_session_id(),
                           s_maintenance_active ? "true" : "false",
                           recording ? "true" : "false",
                           (unsigned)day_export_active_count(),
                           storage);
    if (written < 0 || written >= DAY_USB_PAYLOAD_MAX) {
        free(payload);
        return NULL;
    }
    return payload;
}

static void mark_protocol_activity(void)
{
    if (s_mode == DAY_USB_MODE_SERIAL) {
        day_usb_session_begin_maintenance();
    }
    s_maintenance_active = true;
    s_last_protocol_us = esp_timer_get_time();
}

static void restart_after_response(void)
{
    tinyusb_cdcacm_write_flush(s_protocol_port, pdMS_TO_TICKS(200));
    vTaskDelay(pdMS_TO_TICKS(250));
    esp_restart();
}

static day_usb_status_t export_error_status(esp_err_t error, const char *reason)
{
    if (error == ESP_ERR_INVALID_ARG || error == ESP_ERR_NOT_FOUND) {
        return DAY_USB_STATUS_INVALID_ARG;
    }
    if (reason && (strcmp(reason, "manifest_sha256_mismatch") == 0 ||
                   strcmp(reason, "record_count_mismatch") == 0)) {
        return DAY_USB_STATUS_INVALID_ARG;
    }
    if (reason && (strcmp(reason, "date_already_prepared") == 0 ||
                   strcmp(reason, "too_many_active_exports") == 0)) {
        return DAY_USB_STATUS_BUSY;
    }
    if (error == ESP_ERR_INVALID_STATE) {
        return DAY_USB_STATUS_BAD_STATE;
    }
    return DAY_USB_STATUS_STORAGE_ERROR;
}

static void process_request(const day_usb_frame_header_t *hdr, const uint8_t *payload)
{
    mark_protocol_activity();
    day_usb_command_t cmd = (day_usb_command_t)hdr->cmd;
    day_usb_status_t status = DAY_USB_STATUS_OK;
    char *response = NULL;
    char reason[64] = "invalid_argument";

    switch (cmd) {
    case DAY_USB_CMD_HELLO:
    case DAY_USB_CMD_GET_STATUS:
    case DAY_USB_CMD_PING:
        if (day_usb_validate_empty_args(payload, hdr->payload_len, reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        response = make_status_payload();
        if (!response) {
            status = DAY_USB_STATUS_STORAGE_ERROR;
        }
        break;
    case DAY_USB_CMD_ENTER_MSC: {
        if (s_mode != DAY_USB_MODE_SERIAL) {
            status = DAY_USB_STATUS_BAD_STATE;
            break;
        }
        if (day_recorder_is_active()) {
            status = DAY_USB_STATUS_BUSY;
            break;
        }
        day_usb_enter_msc_args_t args;
        if (day_usb_parse_enter_msc_args(payload, hdr->payload_len, &args,
                                         reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        if (args.export_id_count > 0 && args.access != DAY_USB_ACCESS_RO) {
            status = DAY_USB_STATUS_INVALID_ARG;
            snprintf(reason, sizeof(reason), "transactional_msc_must_be_read_only");
            break;
        }
        if (day_export_validate_prepared(args.export_ids, args.export_id_count,
                                         reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        const char *active_export = args.export_id_count > 0 ? args.export_ids[0] : NULL;
        if (day_usb_session_prepare_msc(args.access, active_export) != ESP_OK) {
            status = DAY_USB_STATUS_STORAGE_ERROR;
            break;
        }
        response = make_status_payload();
        send_response(hdr->seq, cmd, status, response ? response : "{\"accepted\":true}");
        free(response);
        day_storage_deinit();
        restart_after_response();
        return;
    }
    case DAY_USB_CMD_BEGIN_EXPORT: {
        if (s_mode != DAY_USB_MODE_SERIAL) {
            status = DAY_USB_STATUS_BAD_STATE;
            break;
        }
        if (day_recorder_is_active()) {
            status = DAY_USB_STATUS_BUSY;
            break;
        }
        day_usb_begin_export_args_t args;
        if (day_usb_parse_begin_export_args(payload, hdr->payload_len, &args,
                                            reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        response = malloc(DAY_USB_PAYLOAD_MAX + 1);
        if (!response) {
            status = DAY_USB_STATUS_STORAGE_ERROR;
            break;
        }
        esp_err_t result = day_export_begin(&args, response, DAY_USB_PAYLOAD_MAX + 1,
                                            reason, sizeof(reason));
        if (result != ESP_OK) {
            status = export_error_status(result, reason);
            free(response);
            response = NULL;
        }
        break;
    }
    case DAY_USB_CMD_COMMIT_EXPORT_DELETE: {
        if (s_mode != DAY_USB_MODE_SERIAL) {
            status = DAY_USB_STATUS_BAD_STATE;
            break;
        }
        if (day_recorder_is_active()) {
            status = DAY_USB_STATUS_BUSY;
            break;
        }
        day_usb_commit_export_args_t args;
        if (day_usb_parse_commit_export_args(payload, hdr->payload_len, &args,
                                             reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        response = malloc(DAY_USB_PAYLOAD_MAX + 1);
        if (!response) {
            status = DAY_USB_STATUS_STORAGE_ERROR;
            break;
        }
        esp_err_t result = day_export_commit_delete(&args, response, DAY_USB_PAYLOAD_MAX + 1,
                                                    reason, sizeof(reason));
        if (result != ESP_OK) {
            status = export_error_status(result, reason);
            free(response);
            response = NULL;
        }
        break;
    }
    case DAY_USB_CMD_ABORT_EXPORT: {
        if (s_mode != DAY_USB_MODE_SERIAL) {
            status = DAY_USB_STATUS_BAD_STATE;
            break;
        }
        day_usb_export_id_args_t args;
        if (day_usb_parse_export_id_args(payload, hdr->payload_len, &args,
                                         reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        response = malloc(DAY_USB_PAYLOAD_MAX + 1);
        if (!response) {
            status = DAY_USB_STATUS_STORAGE_ERROR;
            break;
        }
        esp_err_t result = day_export_abort(&args, response, DAY_USB_PAYLOAD_MAX + 1,
                                            reason, sizeof(reason));
        if (result != ESP_OK) {
            status = export_error_status(result, reason);
            free(response);
            response = NULL;
        }
        break;
    }
    case DAY_USB_CMD_GET_EXPORT_STATUS: {
        if (s_mode != DAY_USB_MODE_SERIAL) {
            status = DAY_USB_STATUS_BAD_STATE;
            break;
        }
        day_usb_get_export_status_args_t args;
        if (day_usb_parse_get_export_status_args(payload, hdr->payload_len, &args,
                                                 reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        response = malloc(DAY_USB_PAYLOAD_MAX + 1);
        if (!response) {
            status = DAY_USB_STATUS_STORAGE_ERROR;
            break;
        }
        esp_err_t result = day_export_get_status(&args, response, DAY_USB_PAYLOAD_MAX + 1,
                                                 reason, sizeof(reason));
        if (result != ESP_OK) {
            status = export_error_status(result, reason);
            free(response);
            response = NULL;
        }
        break;
    }
    case DAY_USB_CMD_LIST_RECORD_DATES: {
        if (s_mode != DAY_USB_MODE_SERIAL) {
            status = DAY_USB_STATUS_BAD_STATE;
            break;
        }
        day_usb_pagination_args_t args;
        if (day_usb_parse_pagination_args(payload, hdr->payload_len, &args,
                                          reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        response = malloc(DAY_USB_PAYLOAD_MAX + 1);
        if (!response) {
            status = DAY_USB_STATUS_STORAGE_ERROR;
            break;
        }
        esp_err_t result = day_export_list_record_dates(&args, response, DAY_USB_PAYLOAD_MAX + 1,
                                                        reason, sizeof(reason));
        if (result != ESP_OK) {
            status = export_error_status(result, reason);
            free(response);
            response = NULL;
        }
        break;
    }
    case DAY_USB_CMD_EXIT_MSC: {
        day_usb_exit_msc_args_t args;
        if (day_usb_parse_exit_msc_args(payload, hdr->payload_len, &args,
                                        reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        if (s_mode != DAY_USB_MODE_MSC) {
            if (args.next_mode == DAY_USB_NEXT_MODE_MAINTENANCE) {
                (void)day_usb_session_prepare_serial_maintenance();
            } else {
                day_usb_session_end();
                s_maintenance_active = false;
            }
            response = make_status_payload();
            break;
        }
        if (!s_msc_ejected && !args.force) {
            status = DAY_USB_STATUS_BAD_STATE;
            response = strdup("{\"error\":\"MSC volume must be ejected before EXIT_MSC\"}");
            break;
        }
        if (args.next_mode == DAY_USB_NEXT_MODE_MAINTENANCE) {
            (void)day_usb_session_prepare_serial_maintenance();
        } else {
            day_usb_session_end();
        }
        response = make_status_payload();
        send_response(hdr->seq, cmd, status, response ? response : "{\"accepted\":true}");
        free(response);
        restart_after_response();
        return;
    }
    case DAY_USB_CMD_END_SESSION:
        if (s_mode != DAY_USB_MODE_SERIAL) {
            status = DAY_USB_STATUS_BAD_STATE;
            break;
        }
        if (day_usb_validate_empty_args(payload, hdr->payload_len, reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        response = make_status_payload();
        send_response(hdr->seq, cmd, status, response ? response : "{\"ended\":true}");
        free(response);
        tinyusb_cdcacm_write_flush(s_protocol_port, pdMS_TO_TICKS(200));
        day_usb_session_end();
        s_maintenance_active = false;
        return;
    default:
        status = DAY_USB_STATUS_UNSUPPORTED_CMD;
        break;
    }

    if (!response && status != DAY_USB_STATUS_OK) {
        char fallback[192];
        snprintf(fallback, sizeof(fallback),
                 "{\"error\":\"%s\",\"reason\":\"%s\",\"message\":\"request rejected\"}",
                 day_usb_status_name(status), reason);
        send_response(hdr->seq, cmd, status, fallback);
    } else {
        send_response(hdr->seq, cmd, status, response ? response : "{}");
    }
    free(response);
}

static void process_frame(const uint8_t *frame, size_t len)
{
    if (len < sizeof(day_usb_frame_header_t) + 4) {
        return;
    }
    uint32_t expected_crc = get_u32_le(frame + len - 4);
    uint32_t actual_crc = day_usb_crc32(0, frame, len - 4);
    if (expected_crc != actual_crc) {
        return;
    }
    day_usb_frame_header_t hdr;
    memcpy(&hdr, frame, sizeof(hdr));
    if (hdr.magic[0] != DAY_USB_PROTO_MAGIC0 || hdr.magic[1] != DAY_USB_PROTO_MAGIC1 ||
        hdr.header_len != sizeof(day_usb_frame_header_t)) {
        return;
    }
    if (hdr.version != DAY_USB_PROTO_VERSION) {
        send_response(hdr.seq, (day_usb_command_t)hdr.cmd, DAY_USB_STATUS_UNSUPPORTED_VERSION,
                      "{\"error\":\"UNSUPPORTED_VERSION\"}");
        return;
    }
    if (hdr.type != DAY_USB_FRAME_REQUEST) {
        return;
    }
    if ((size_t)hdr.payload_len != len - sizeof(day_usb_frame_header_t) - 4) {
        send_response(hdr.seq, (day_usb_command_t)hdr.cmd, DAY_USB_STATUS_BAD_FRAME, "{\"error\":\"BAD_FRAME\"}");
        return;
    }
    process_request(&hdr, frame + sizeof(day_usb_frame_header_t));
}

static void slip_decode_byte(uint8_t byte)
{
    const uint8_t end = 0xC0;
    const uint8_t esc = 0xDB;
    const uint8_t esc_end = 0xDC;
    const uint8_t esc_esc = 0xDD;

    if (byte == end) {
        if (s_decoder.len > 0) {
            process_frame(s_decoder.data, s_decoder.len);
        }
        s_decoder.len = 0;
        s_decoder.escape = false;
        return;
    }
    if (byte == esc) {
        s_decoder.escape = true;
        return;
    }
    if (s_decoder.escape) {
        if (byte == esc_end) {
            byte = end;
        } else if (byte == esc_esc) {
            byte = esc;
        } else {
            s_decoder.len = 0;
            s_decoder.escape = false;
            return;
        }
        s_decoder.escape = false;
    }
    if (s_decoder.len >= sizeof(s_decoder.data)) {
        s_decoder.len = 0;
        s_decoder.escape = false;
        return;
    }
    s_decoder.data[s_decoder.len++] = byte;
}

static void protocol_task(void *arg)
{
    (void)arg;
    day_usb_rx_msg_t msg;
    while (true) {
        if (xQueueReceive(s_rx_queue, &msg, pdMS_TO_TICKS(1000)) == pdTRUE) {
            for (size_t i = 0; i < msg.len; ++i) {
                slip_decode_byte(msg.data[i]);
            }
        }
        int64_t now = esp_timer_get_time();
        if (s_maintenance_active && s_last_protocol_us > 0 &&
            now - s_last_protocol_us > DAY_USB_MAINTENANCE_TIMEOUT_US) {
            s_maintenance_active = false;
            if (s_mode == DAY_USB_MODE_SERIAL) {
                day_usb_session_end();
            }
        }
        if (s_mode == DAY_USB_MODE_MSC && s_msc_ejected && s_last_msc_activity_us > 0 &&
            now - s_last_msc_activity_us > DAY_USB_MSC_IDLE_TIMEOUT_US) {
            day_usb_session_end();
            esp_restart();
        }
    }
}

static esp_err_t init_cdc_port(tinyusb_cdcacm_itf_t port, bool rx_callback)
{
    tinyusb_config_cdcacm_t acm_cfg = {
        .cdc_port = port,
        .callback_rx = rx_callback ? cdc_rx_callback : NULL,
        .callback_rx_wanted_char = NULL,
        .callback_line_state_changed = NULL,
        .callback_line_coding_changed = NULL,
    };
    return tinyusb_cdcacm_init(&acm_cfg);
}

static esp_err_t install_tinyusb(day_usb_mode_t mode)
{
    if (s_tinyusb_started) {
        return ESP_OK;
    }
    esp_err_t identity_ret = day_device_identity_init();
    if (identity_ret != ESP_OK) {
        return identity_ret;
    }
    s_string_desc[3] = day_device_usb_serial();
    tinyusb_config_t tusb_cfg = TINYUSB_DEFAULT_CONFIG();
    tusb_cfg.descriptor.device = mode == DAY_USB_MODE_MSC ? &s_msc_device_desc : &s_serial_device_desc;
    tusb_cfg.descriptor.full_speed_config = mode == DAY_USB_MODE_MSC ? s_msc_fs_desc : s_serial_fs_desc;
    tusb_cfg.descriptor.string = s_string_desc;
    tusb_cfg.descriptor.string_count = sizeof(s_string_desc) / sizeof(s_string_desc[0]);
    esp_err_t ret = tinyusb_driver_install(&tusb_cfg);
    if (ret != ESP_OK) {
        return ret;
    }
    s_tinyusb_started = true;
    return ESP_OK;
}

static esp_err_t start_protocol(day_usb_mode_t mode)
{
    s_mode = mode;
    if (mode == DAY_USB_MODE_SERIAL && day_usb_link_should_resume_maintenance()) {
        s_maintenance_active = true;
        s_last_protocol_us = esp_timer_get_time();
    }
    s_protocol_port = mode == DAY_USB_MODE_MSC ? TINYUSB_CDC_ACM_0 : TINYUSB_CDC_ACM_1;
    s_rx_queue = xQueueCreate(8, sizeof(day_usb_rx_msg_t));
    if (!s_rx_queue) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t ret = install_tinyusb(mode);
    if (ret != ESP_OK) {
        return ret;
    }
    if (mode == DAY_USB_MODE_SERIAL) {
        ret = init_cdc_port(TINYUSB_CDC_ACM_0, false);
        if (ret != ESP_OK) {
            return ret;
        }
        ret = init_cdc_port(TINYUSB_CDC_ACM_1, true);
        if (ret != ESP_OK) {
            return ret;
        }
        esp_log_set_vprintf(usb_log_vprintf);
    } else {
        ret = init_cdc_port(TINYUSB_CDC_ACM_0, true);
        if (ret != ESP_OK) {
            return ret;
        }
    }
    if (xTaskCreate(protocol_task, "day_usb_proto", 4096, NULL, 8, &s_protocol_task) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t day_usb_link_start_serial_mode(void)
{
#if CONFIG_DAY_USB_LINK_ENABLED
    return start_protocol(DAY_USB_MODE_SERIAL);
#else
    return ESP_OK;
#endif
}

bool day_usb_link_maintenance_active(void)
{
#if CONFIG_DAY_USB_LINK_ENABLED
    if (!s_maintenance_active) {
        return false;
    }
    int64_t now = esp_timer_get_time();
    return s_last_protocol_us <= 0 || now - s_last_protocol_us <= DAY_USB_MAINTENANCE_TIMEOUT_US;
#else
    return false;
#endif
}

static esp_err_t msc_card_init(void)
{
    if (s_msc_card) {
        return ESP_OK;
    }
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.max_freq_khz = 20000;
    host.unaligned_multi_block_rw_max_chunk_size = 8;

    spi_bus_config_t bus_cfg = {
        .mosi_io_num = DAY_PIN_TF_MOSI,
        .miso_io_num = DAY_PIN_TF_MISO,
        .sclk_io_num = DAY_PIN_TF_SCK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 32 * 1024,
    };
    esp_err_t ret = spi_bus_initialize(host.slot, &bus_cfg, SDSPI_DEFAULT_DMA);
    if (ret == ESP_OK) {
        s_msc_bus_initialized = true;
    } else if (ret != ESP_ERR_INVALID_STATE) {
        return ret;
    }

    ret = sdspi_host_init();
    if (ret != ESP_OK) {
        return ret;
    }

    sdspi_device_config_t slot_config = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot_config.gpio_cs = DAY_PIN_TF_CS;
    slot_config.host_id = host.slot;

    sdspi_dev_handle_t handle = 0;
    ret = sdspi_host_init_device(&slot_config, &handle);
    if (ret != ESP_OK) {
        return ret;
    }
    host.slot = handle;

    s_msc_card = calloc(1, sizeof(sdmmc_card_t));
    if (!s_msc_card) {
        sdspi_host_remove_device(handle);
        return ESP_ERR_NO_MEM;
    }
    ret = sdmmc_card_init(&host, s_msc_card);
    if (ret != ESP_OK) {
        sdspi_host_remove_device(handle);
        free(s_msc_card);
        s_msc_card = NULL;
        return ret;
    }
    s_last_msc_activity_us = esp_timer_get_time();
    return ESP_OK;
}

static void msc_card_deinit(void)
{
    if (s_msc_card) {
        if (s_msc_card->host.deinit_p) {
            s_msc_card->host.deinit_p(s_msc_card->host.slot);
        }
        free(s_msc_card);
        s_msc_card = NULL;
    }
    if (s_msc_bus_initialized) {
        spi_bus_free(SDSPI_DEFAULT_HOST);
        s_msc_bus_initialized = false;
    }
}

static void msc_event_callback(tinyusb_msc_storage_handle_t handle, tinyusb_msc_event_t *event, void *arg)
{
    (void)handle;
    (void)arg;
    if (!event) {
        return;
    }
    s_last_msc_activity_us = esp_timer_get_time();
    if (event->id == TINYUSB_MSC_EVENT_MOUNT_COMPLETE) {
        s_msc_ejected = event->mount_point == TINYUSB_MSC_STORAGE_MOUNT_APP;
    }
}

static esp_err_t msc_storage_init(void)
{
    if (s_msc_storage) {
        return ESP_OK;
    }
    tinyusb_msc_driver_config_t driver_cfg = {
        .callback = msc_event_callback,
    };
    esp_err_t ret = tinyusb_msc_install_driver(&driver_cfg);
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        return ret;
    }

    tinyusb_msc_storage_config_t storage_cfg = {
        .medium.card = s_msc_card,
        .mount_point = TINYUSB_MSC_STORAGE_MOUNT_USB,
        .fat_fs = {
            .base_path = NULL,
            .config.max_files = 4,
            .do_not_format = true,
            .format_flags = 0,
        },
    };
    ret = tinyusb_msc_new_storage_sdmmc(&storage_cfg, &s_msc_storage);
    if (ret != ESP_OK) {
        (void)tinyusb_msc_uninstall_driver();
        s_msc_storage = NULL;
        return ret;
    }
    s_msc_ejected = false;
    s_last_msc_activity_us = esp_timer_get_time();
    return ESP_OK;
}

static void msc_storage_deinit(void)
{
    if (s_msc_storage) {
        (void)tinyusb_msc_delete_storage(s_msc_storage);
        s_msc_storage = NULL;
    }
    (void)tinyusb_msc_uninstall_driver();
}

void day_usb_link_run_msc_mode(void)
{
#if CONFIG_DAY_USB_LINK_ENABLED
    s_msc_read_only = day_usb_session_msc_access() == DAY_USB_ACCESS_RO;
    if (msc_card_init() != ESP_OK) {
        day_usb_session_end();
        esp_restart();
    }
    esp_err_t ret = msc_storage_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "MSC storage init failed: %s", esp_err_to_name(ret));
        msc_card_deinit();
        day_usb_session_end();
        esp_restart();
    }
    if (start_protocol(DAY_USB_MODE_MSC) != ESP_OK) {
        msc_storage_deinit();
        msc_card_deinit();
        day_usb_session_end();
        esp_restart();
    }
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
#else
    day_usb_session_end();
    esp_restart();
#endif
}

bool tud_msc_is_writable_cb(uint8_t lun)
{
    (void)lun;
    return s_mode == DAY_USB_MODE_MSC && s_msc_card && !s_msc_ejected && !s_msc_read_only;
}
