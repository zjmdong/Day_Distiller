#include "usb_link.h"

#include <inttypes.h>
#include <stddef.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "device_settings.h"
#include "device_status.h"
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
#include "led_status.h"
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
#define DAY_USB_HOST_LATCH_MAGIC 0x4444484cUL
#define DAY_USB_HOST_LATCH_SCHEMA 1U

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
// Keep the command protocol on CDC0. Windows usbser.sys can enumerate the
// second CDC function on affected hosts while aborting every read submitted
// to its data endpoint. CDC0 is also the protocol port in MSC mode, so using
// it in both modes removes that driver-dependent role switch.
static tinyusb_cdcacm_itf_t s_protocol_port = TINYUSB_CDC_ACM_0;
static bool s_tinyusb_started;
static bool s_maintenance_active;
static int64_t s_last_protocol_us;
static int64_t s_last_msc_activity_us;
static bool s_msc_ejected;
static bool s_msc_read_only;
static sdmmc_card_t *s_msc_card;
static tinyusb_msc_storage_handle_t s_msc_storage;
static bool s_msc_bus_initialized;
static day_usb_config_apply_callback_t s_config_apply_callback;

typedef struct {
    uint32_t magic;
    uint16_t schema;
    uint8_t host_seen;
    uint8_t reserved;
    uint32_t crc32;
} day_usb_host_latch_t;

static RTC_NOINIT_ATTR day_usb_host_latch_t s_host_latch;
static bool s_host_latch_initialized;

static uint32_t host_latch_crc(const day_usb_host_latch_t *latch)
{
    return day_usb_crc32(0, (const uint8_t *)latch,
                         offsetof(day_usb_host_latch_t, crc32));
}

static bool host_latch_valid(void)
{
    return s_host_latch.magic == DAY_USB_HOST_LATCH_MAGIC &&
           s_host_latch.schema == DAY_USB_HOST_LATCH_SCHEMA &&
           s_host_latch.host_seen <= 1 &&
           s_host_latch.crc32 == host_latch_crc(&s_host_latch);
}

static void store_host_latch(bool host_seen)
{
    day_usb_host_latch_t next = {
        .magic = DAY_USB_HOST_LATCH_MAGIC,
        .schema = DAY_USB_HOST_LATCH_SCHEMA,
        .host_seen = host_seen ? 1 : 0,
    };
    next.crc32 = host_latch_crc(&next);
    s_host_latch = next;
}

void day_usb_link_boot_init(void)
{
    if (s_host_latch_initialized) {
        return;
    }
    if (esp_reset_reason() == ESP_RST_POWERON || !host_latch_valid()) {
        store_host_latch(false);
    }
    s_host_latch_initialized = true;
}

bool day_usb_link_host_connection_latched(void)
{
#if CONFIG_DAY_USB_LINK_ENABLED
    day_usb_link_boot_init();
    return host_latch_valid() && s_host_latch.host_seen == 1;
#else
    return false;
#endif
}

static void latch_host_connection(void)
{
    day_usb_link_boot_init();
    if (!day_usb_link_host_connection_latched()) {
        store_host_latch(true);
        ESP_LOGI(TAG, "USB host connection latched until the next power-on reset");
    }
}

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
    .bcdDevice = 0x0201,
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
    .bcdDevice = 0x0201,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x03,
    .bNumConfigurations = 0x01,
};

static const uint8_t s_serial_fs_desc[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_SERIAL_TOTAL, 0, DAY_USB_SERIAL_DESC_LEN,
                          TUSB_DESC_CONFIG_ATT_REMOTE_WAKEUP, 100),
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC0, 5, EP_CDC0_NOTIF, 8, EP_CDC0_OUT, EP_CDC0_IN, 64),
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC1, 4, EP_CDC1_NOTIF, 8, EP_CDC1_OUT, EP_CDC1_IN, 64),
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

void day_usb_link_set_config_apply_callback(day_usb_config_apply_callback_t callback)
{
    s_config_apply_callback = callback;
}

static int usb_log_vprintf(const char *fmt, va_list args)
{
    if (!s_tinyusb_started || s_mode != DAY_USB_MODE_SERIAL || !tinyusb_cdcacm_initialized(TINYUSB_CDC_ACM_1)) {
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
    tinyusb_cdcacm_write_queue(TINYUSB_CDC_ACM_1, (const uint8_t *)buffer, out_len);
    tinyusb_cdcacm_write_flush(TINYUSB_CDC_ACM_1, 0);
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
    if (!data || len > DAY_USB_FRAME_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t *encoded = malloc(len * 2 + 2);
    if (!encoded) {
        return ESP_ERR_NO_MEM;
    }
    size_t encoded_len = 0;
    encoded[encoded_len++] = 0xC0;
    for (size_t i = 0; i < len; ++i) {
        if (data[i] == 0xC0) {
            encoded[encoded_len++] = 0xDB;
            encoded[encoded_len++] = 0xDC;
        } else if (data[i] == 0xDB) {
            encoded[encoded_len++] = 0xDB;
            encoded[encoded_len++] = 0xDD;
        } else {
            encoded[encoded_len++] = data[i];
        }
    }
    encoded[encoded_len++] = 0xC0;

    esp_err_t result = ESP_OK;
    size_t offset = 0;
    while (offset < encoded_len) {
        size_t queued = tinyusb_cdcacm_write_queue(
            s_protocol_port, encoded + offset, encoded_len - offset);
        offset += queued;
        if (offset < encoded_len && queued == 0) {
            result = tinyusb_cdcacm_write_flush(s_protocol_port, pdMS_TO_TICKS(100));
            if (result != ESP_OK) {
                break;
            }
        }
    }
    if (result == ESP_OK) {
        result = tinyusb_cdcacm_write_flush(s_protocol_port, pdMS_TO_TICKS(100));
    }
    free(encoded);
    return result;
}

static esp_err_t send_response(uint32_t seq, day_usb_command_t cmd, day_usb_status_t status, const char *json)
{
    size_t payload_len = json ? strlen(json) : 0;
    if (payload_len > DAY_USB_PAYLOAD_MAX) {
        return ESP_ERR_NO_MEM;
    }
    size_t frame_len = sizeof(day_usb_frame_header_t) + payload_len + 4;
    uint8_t *frame = malloc(frame_len);
    if (!frame) {
        return ESP_ERR_NO_MEM;
    }
    day_usb_frame_header_t hdr = {
        .magic = {DAY_USB_PROTO_MAGIC0, DAY_USB_PROTO_MAGIC1},
        .version = DAY_USB_PROTO_VERSION,
        .type = DAY_USB_FRAME_RESPONSE,
        .header_len = sizeof(day_usb_frame_header_t),
        .seq = seq,
        .cmd = (uint16_t)cmd,
        .status = (uint16_t)status,
        .payload_len = (uint32_t)payload_len,
    };
    memcpy(frame, &hdr, sizeof(hdr));
    if (hdr.payload_len) {
        memcpy(frame + sizeof(hdr), json, hdr.payload_len);
    }
    uint32_t crc = day_usb_crc32(0, frame, sizeof(hdr) + hdr.payload_len);
    put_u32_le(frame + sizeof(hdr) + hdr.payload_len, crc);
    esp_err_t result = send_slip_bytes(frame, frame_len);
    free(frame);
    return result;
}

static bool json_add_bool(cJSON *object, const char *name, bool value)
{
    return cJSON_AddBoolToObject(object, name, value) != NULL;
}

static bool json_add_number(cJSON *object, const char *name, double value)
{
    return cJSON_AddNumberToObject(object, name, value) != NULL;
}

static bool json_add_string(cJSON *object, const char *name, const char *value)
{
    return cJSON_AddStringToObject(object, name, value ? value : "") != NULL;
}

static bool json_add_null(cJSON *object, const char *name)
{
    return cJSON_AddNullToObject(object, name) != NULL;
}

static bool json_add_item(cJSON *object, const char *name, cJSON *item)
{
    if (!item) {
        return false;
    }
    if (!cJSON_AddItemToObject(object, name, item)) {
        cJSON_Delete(item);
        return false;
    }
    return true;
}

static char *json_print_limited(cJSON *root)
{
    if (!root) {
        return NULL;
    }
    char *payload = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!payload) {
        return NULL;
    }
    if (strlen(payload) > DAY_USB_PAYLOAD_MAX) {
        cJSON_free(payload);
        return NULL;
    }
    return payload;
}

static cJSON *make_storage_object(const day_device_status_t *snapshot)
{
    cJSON *storage = cJSON_CreateObject();
    if (!storage) {
        return NULL;
    }
    bool ok = true;
    if (s_mode == DAY_USB_MODE_MSC) {
        bool ready = s_msc_card != NULL;
        uint32_t sector_size = ready ? s_msc_card->csd.sector_size : 0;
        uint32_t sector_count = ready ? s_msc_card->csd.capacity : 0;
        ok = json_add_bool(storage, "ready", ready) &&
             json_add_bool(storage, "mounted", false) &&
             json_add_bool(storage, "usb_exposed", ready) &&
             json_add_bool(storage, "read_only", s_msc_read_only) &&
             json_add_bool(storage, "ejected", s_msc_ejected) &&
             json_add_number(storage, "sector_size", sector_size) &&
             json_add_number(storage, "sector_count", sector_count) &&
             json_add_number(storage, "total_bytes", (double)((uint64_t)sector_count * sector_size));
        if (snapshot) {
            ok = ok && json_add_bool(storage, "available", ready) &&
                 json_add_number(storage, "sample_age_ms", snapshot->storage.sample_age_ms) &&
                 json_add_number(storage, "last_error", ready ? ESP_OK : ESP_ERR_INVALID_STATE);
        }
    } else {
        day_storage_status_t status = snapshot ? snapshot->storage : day_storage_get_status();
        ok = json_add_bool(storage, "ready", status.mounted) &&
             json_add_bool(storage, "mounted", status.mounted) &&
             json_add_bool(storage, "usb_exposed", false) &&
             json_add_number(storage, "total_bytes", (double)status.total_bytes) &&
             json_add_number(storage, "free_bytes", (double)status.free_bytes) &&
             json_add_number(storage, "last_error", status.last_error);
        if (snapshot) {
            ok = ok && json_add_bool(storage, "available", status.available) &&
                 json_add_number(storage, "sample_age_ms", status.sample_age_ms);
        }
    }
    if (!ok) {
        cJSON_Delete(storage);
        return NULL;
    }
    return storage;
}

static bool add_capabilities(cJSON *root)
{
    static const char *const names[] = {
        "enter_msc", "exit_msc", "msc_rw", "msc_ro", "slip_crc32_json",
        "transactional_export_v2", "export_transactions", "export_manifest_v2",
        "list_record_dates", "get_export_status", "commit_export_delete",
        "abort_export", "exit_to_maintenance", "end_session",
        "device_status_v2", "battery_status", "rtc_status",
        "wifi_status", "power_status", "device_config_v1", "device_config_write",
        "config_secrets_over_usb", "led_settings",
        "led_preview",
    };
    cJSON *array = cJSON_CreateArray();
    if (!array) {
        return false;
    }
    for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); ++i) {
        cJSON *name = cJSON_CreateString(names[i]);
        if (!name || !cJSON_AddItemToArray(array, name)) {
            cJSON_Delete(name);
            cJSON_Delete(array);
            return false;
        }
    }
    return json_add_item(root, "capabilities", array);
}

static const char *charge_state_name(day_charge_state_t state)
{
    switch (state) {
    case DAY_CHARGE_NOT_CHARGING: return "not_charging";
    case DAY_CHARGE_ESTIMATED: return "estimated";
    default: return "unknown";
    }
}

static bool add_status_details(cJSON *root, const day_device_status_t *status)
{
    if (!json_add_number(root, "status_schema", status->schema_version) ||
        !json_add_number(root, "config_revision", status->config_revision) ||
        !json_add_number(root, "snapshot_monotonic_ms", (double)status->snapshot_monotonic_ms) ||
        !json_add_number(root, "last_record_error", status->last_record_error)) {
        return false;
    }

    cJSON *battery = cJSON_CreateObject();
    if (!battery) {
        return false;
    }
    if (!json_add_bool(battery, "available", status->battery.available) ||
        !(status->battery.available ? json_add_number(battery, "soc_percent", status->battery.soc_percent)
                                   : json_add_null(battery, "soc_percent")) ||
        !(status->battery.available ? json_add_number(battery, "voltage_v", status->battery.voltage_v)
                                   : json_add_null(battery, "voltage_v")) ||
        !json_add_string(battery, "charge_state", charge_state_name(status->battery.charge_state)) ||
        !json_add_number(battery, "confidence", status->battery.charge_confidence) ||
        !json_add_number(battery, "sample_age_ms", status->battery.sample_age_ms) ||
        !json_add_number(battery, "last_error", status->battery.last_error)) {
        cJSON_Delete(battery);
        return false;
    }
    if (!json_add_item(root, "battery", battery)) return false;

    cJSON *rtc = cJSON_CreateObject();
    if (!rtc) {
        return false;
    }
    if (!json_add_bool(rtc, "available", status->rtc.available) ||
        !json_add_bool(rtc, "valid", status->rtc.valid) ||
        !(status->rtc.valid ? json_add_number(rtc, "unix_time", (double)status->rtc.unix_time)
                            : json_add_null(rtc, "unix_time")) ||
        !(status->rtc.valid ? json_add_string(rtc, "iso8601", status->rtc.iso8601)
                            : json_add_null(rtc, "iso8601")) ||
        !json_add_number(rtc, "sample_age_ms", status->rtc.sample_age_ms) ||
        !json_add_number(rtc, "last_error", status->rtc.last_error)) {
        cJSON_Delete(rtc);
        return false;
    }
    if (!json_add_item(root, "rtc", rtc)) return false;

    cJSON *clock = cJSON_CreateObject();
    if (!clock) {
        return false;
    }
    if (!json_add_bool(clock, "system_valid", status->clock.system_valid) ||
        !json_add_string(clock, "timezone", status->clock.timezone) ||
        !json_add_string(clock, "source", status->clock.source) ||
        !json_add_string(clock, "last_sync_source", status->clock.last_sync_source) ||
        !(status->clock.last_sync_unix > 0
              ? json_add_number(clock, "last_sync_unix", (double)status->clock.last_sync_unix)
              : json_add_null(clock, "last_sync_unix"))) {
        cJSON_Delete(clock);
        return false;
    }
    if (!json_add_item(root, "clock", clock)) return false;

    cJSON *wifi = cJSON_CreateObject();
    if (!wifi) {
        return false;
    }
    if (!json_add_bool(wifi, "available", status->wifi.available) ||
        !json_add_bool(wifi, "configured", status->config.wifi_ssid[0] != '\0') ||
        !json_add_bool(wifi, "sta_connected", status->wifi.sta_connected) ||
        !json_add_bool(wifi, "ap_running", status->wifi.ap_running) ||
        !json_add_number(wifi, "ap_clients", status->wifi.ap_clients) ||
        !json_add_bool(wifi, "time_synced", status->wifi.time_synced) ||
        !json_add_string(wifi, "ssid", status->config.wifi_ssid) ||
        !json_add_string(wifi, "ip", status->wifi.ip_addr) ||
        !json_add_null(wifi, "rssi") ||
        !json_add_number(wifi, "last_error", status->wifi.last_error)) {
        cJSON_Delete(wifi);
        return false;
    }
    if (!json_add_item(root, "wifi", wifi)) return false;

    cJSON *power = cJSON_CreateObject();
    if (!power) {
        return false;
    }
    if (!json_add_string(power, "wake_reason", status->power.wake_reason) ||
        !json_add_string(power, "reset_reason", status->power.reset_reason) ||
        !json_add_bool(power, "low_battery_latched", status->power.low_battery_latched) ||
        !json_add_bool(power, "timer_wake_enabled", status->power.timer_wake_enabled) ||
        !json_add_number(power, "next_wake_sec", status->power.next_wake_sec)) {
        cJSON_Delete(power);
        return false;
    }
    if (!json_add_item(root, "power", power)) return false;

    char color[8];
    day_settings_format_recording_color(&status->config, color);
    cJSON *led = cJSON_CreateObject();
    if (!led) {
        return false;
    }
    if (!json_add_string(led, "mode", status->led.mode) ||
        !json_add_number(led, "brightness_percent", status->led.brightness_percent) ||
        !json_add_string(led, "recording_color", color)) {
        cJSON_Delete(led);
        return false;
    }
    if (!json_add_item(root, "led", led)) return false;

    return true;
}

static char *make_status_payload(bool detailed)
{
    day_device_status_t snapshot;
    day_device_status_t *status = NULL;
    if (detailed) {
        if (day_status_get_snapshot(&snapshot) != ESP_OK) {
            return NULL;
        }
        status = &snapshot;
    }
    cJSON *root = cJSON_CreateObject();
    cJSON *schemas = cJSON_CreateIntArray((const int[]){1, 2}, 2);
    char session_id[17];
    snprintf(session_id, sizeof(session_id), "%016" PRIx64, day_usb_session_id());
    bool recording = status ? status->recording_active
                            : (s_mode == DAY_USB_MODE_SERIAL ? day_recorder_is_active() : false);
    if (!root || !schemas ||
        !json_add_number(root, "protocol", DAY_USB_PROTO_VERSION) ||
        !json_add_string(root, "firmware_version", DAY_USB_FIRMWARE_VERSION) ||
        !json_add_string(root, "device", "Day Distiller") ||
        !json_add_string(root, "device_id", day_device_id()) ||
        !json_add_string(root, "mode", day_usb_link_mode_name()) ||
        !json_add_string(root, "runtime_state",
                         s_mode == DAY_USB_MODE_MSC ? "msc_read_only" :
                         (day_usb_link_maintenance_active() ? "serial_maintenance" : "normal_boot")) ||
        !json_add_string(root, "session_id", session_id) ||
        !json_add_bool(root, "maintenance", day_usb_link_maintenance_active()) ||
        !json_add_bool(root, "recording", recording) ||
        !json_add_bool(root, "usb_full_speed", true)) {
        cJSON_Delete(schemas);
        cJSON_Delete(root);
        return NULL;
    }
    if (!json_add_item(root, "metadata_schemas", schemas)) {
        cJSON_Delete(root);
        return NULL;
    }
    if (!json_add_number(root, "active_exports", day_export_active_count()) ||
        !json_add_item(root, "storage", make_storage_object(status)) ||
        (!detailed && !add_capabilities(root)) ||
        (detailed && !add_status_details(root, status))) {
        cJSON_Delete(root);
        return NULL;
    }
    return json_print_limited(root);
}

static bool add_config_group(cJSON *root, const char *name, cJSON *group)
{
    if (!group) {
        return false;
    }
    return json_add_item(root, name, group);
}

static char *make_config_payload(bool include_secrets)
{
    day_settings_snapshot_t settings;
    if (day_settings_get(&settings) != ESP_OK) {
        return NULL;
    }
    cJSON *root = cJSON_CreateObject();
    if (!root || !json_add_number(root, "schema_version", settings.schema_version) ||
        !json_add_number(root, "revision", settings.revision)) {
        cJSON_Delete(root);
        return NULL;
    }

    cJSON *group = cJSON_CreateObject();
    if (!group || !json_add_number(group, "record_framesize", settings.config.camera_record_framesize) ||
        !json_add_number(group, "jpeg_quality", settings.config.camera_jpeg_quality) ||
        !json_add_number(group, "record_fps", settings.config.camera_record_fps) ||
        !json_add_number(group, "preview_framesize", settings.config.camera_preview_framesize) ||
        !json_add_number(group, "preview_fps", settings.config.camera_preview_fps)) {
        cJSON_Delete(group); cJSON_Delete(root); return NULL;
    }
    if (!add_config_group(root, "video", group)) { cJSON_Delete(root); return NULL; }

    group = cJSON_CreateObject();
    if (!group || !json_add_string(group, "ssid", settings.config.wifi_ssid) ||
        !json_add_bool(group, "password_set", settings.config.wifi_password[0] != '\0') ||
        (include_secrets && !json_add_string(group, "password", settings.config.wifi_password))) {
        cJSON_Delete(group); cJSON_Delete(root); return NULL;
    }
    if (!add_config_group(root, "wifi", group)) { cJSON_Delete(root); return NULL; }

    group = cJSON_CreateObject();
    if (!group || !json_add_string(group, "timezone", settings.config.timezone) ||
        !json_add_string(group, "ntp_server", settings.config.ntp_server)) {
        cJSON_Delete(group); cJSON_Delete(root); return NULL;
    }
    if (!add_config_group(root, "time", group)) { cJSON_Delete(root); return NULL; }

    group = cJSON_CreateObject();
    if (!group || !json_add_number(group, "wake_interval_sec", settings.config.wake_interval_sec) ||
        !json_add_bool(group, "auto_record_enabled", settings.config.auto_record_enabled) ||
        !json_add_bool(group, "shake_trigger_enabled", settings.config.shake_trigger_enabled) ||
        !json_add_number(group, "low_battery_percent", settings.config.low_battery_percent)) {
        cJSON_Delete(group); cJSON_Delete(root); return NULL;
    }
    if (!add_config_group(root, "system", group)) { cJSON_Delete(root); return NULL; }

    char color[8];
    day_settings_format_recording_color(&settings.config, color);
    group = cJSON_CreateObject();
    if (!group || !json_add_number(group, "brightness_percent", settings.config.led_brightness_percent) ||
        !json_add_string(group, "recording_color", color)) {
        cJSON_Delete(group); cJSON_Delete(root); return NULL;
    }
    if (!add_config_group(root, "led", group)) { cJSON_Delete(root); return NULL; }

    group = cJSON_CreateObject();
    if (!group || !json_add_number(group, "sample_rate_hz", settings.config.audio_sample_rate_hz)) {
        cJSON_Delete(group); cJSON_Delete(root); return NULL;
    }
    if (!add_config_group(root, "audio", group)) { cJSON_Delete(root); return NULL; }

    group = cJSON_CreateObject();
    if (!group || !json_add_number(group, "sample_rate_hz", settings.config.imu_sample_rate_hz) ||
        !json_add_number(group, "orientation", settings.config.imu_orientation)) {
        cJSON_Delete(group); cJSON_Delete(root); return NULL;
    }
    if (!add_config_group(root, "imu", group)) { cJSON_Delete(root); return NULL; }
    return json_print_limited(root);
}

static char *make_set_config_payload(const day_settings_result_t *result, uint32_t groups)
{
    static const struct {
        uint32_t bit;
        const char *name;
    } group_names[] = {
        {DAY_USB_CONFIG_GROUP_VIDEO, "video"},
        {DAY_USB_CONFIG_GROUP_WIFI, "wifi"},
        {DAY_USB_CONFIG_GROUP_TIME, "time"},
        {DAY_USB_CONFIG_GROUP_SYSTEM, "system"},
        {DAY_USB_CONFIG_GROUP_LED, "led"},
        {DAY_USB_CONFIG_GROUP_AUDIO, "audio"},
        {DAY_USB_CONFIG_GROUP_IMU, "imu"},
    };
    cJSON *root = cJSON_CreateObject();
    cJSON *applied = cJSON_CreateArray();
    if (!root || !applied || !json_add_bool(root, "ok", true) ||
        !json_add_number(root, "revision", result->revision) ||
        !json_add_bool(root, "changed", result->changed) ||
        !json_add_bool(root, "reboot_required", false)) {
        cJSON_Delete(applied); cJSON_Delete(root); return NULL;
    }
    for (size_t i = 0; i < sizeof(group_names) / sizeof(group_names[0]); ++i) {
        if ((groups & group_names[i].bit) == 0) {
            continue;
        }
        cJSON *name = cJSON_CreateString(group_names[i].name);
        if (!name || !cJSON_AddItemToArray(applied, name)) {
            cJSON_Delete(name); cJSON_Delete(applied); cJSON_Delete(root); return NULL;
        }
    }
    if (!json_add_item(root, "applied", applied)) {
        cJSON_Delete(root); return NULL;
    }
    return json_print_limited(root);
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

static day_usb_status_t config_error_status(esp_err_t error)
{
    if (error == ESP_ERR_INVALID_ARG) {
        return DAY_USB_STATUS_INVALID_ARG;
    }
    if (error == ESP_ERR_INVALID_STATE) {
        return DAY_USB_STATUS_BAD_STATE;
    }
    if (error == ESP_ERR_TIMEOUT) {
        return DAY_USB_STATUS_TIMEOUT;
    }
    return DAY_USB_STATUS_STORAGE_ERROR;
}

static void process_request(const day_usb_frame_header_t *hdr, const uint8_t *payload)
{
    mark_protocol_activity();
    day_usb_command_t cmd = (day_usb_command_t)hdr->cmd;
    day_usb_status_t status = DAY_USB_STATUS_OK;
    char *response = NULL;
    char field[DAY_SETTINGS_FIELD_MAX] = "";
    char reason[64] = "invalid_argument";

    switch (cmd) {
    case DAY_USB_CMD_HELLO:
    case DAY_USB_CMD_PING:
        if (day_usb_validate_empty_args(payload, hdr->payload_len, reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        response = make_status_payload(false);
        if (!response) {
            status = DAY_USB_STATUS_STORAGE_ERROR;
        }
        break;
    case DAY_USB_CMD_GET_STATUS:
        if (day_usb_validate_empty_args(payload, hdr->payload_len, reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        response = make_status_payload(true);
        if (!response) {
            status = DAY_USB_STATUS_STORAGE_ERROR;
            snprintf(reason, sizeof(reason), "status_payload_unavailable");
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
        response = make_status_payload(false);
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
    case DAY_USB_CMD_GET_CONFIG: {
        if (s_mode != DAY_USB_MODE_SERIAL) {
            status = DAY_USB_STATUS_BAD_STATE;
            snprintf(reason, sizeof(reason), "serial_maintenance_required");
            break;
        }
        day_usb_get_config_args_t args;
        if (day_usb_parse_get_config_args(payload, hdr->payload_len, &args,
                                          field, sizeof(field), reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        response = make_config_payload(args.include_secrets);
        if (!response) {
            status = DAY_USB_STATUS_STORAGE_ERROR;
            snprintf(reason, sizeof(reason), "config_payload_unavailable");
        }
        break;
    }
    case DAY_USB_CMD_SET_CONFIG: {
        if (s_mode != DAY_USB_MODE_SERIAL) {
            status = DAY_USB_STATUS_BAD_STATE;
            snprintf(reason, sizeof(reason), "serial_maintenance_required");
            break;
        }
        if (day_recorder_is_active()) {
            status = DAY_USB_STATUS_BUSY;
            snprintf(reason, sizeof(reason), "recording_active");
            break;
        }
        if (!s_config_apply_callback) {
            status = DAY_USB_STATUS_BAD_STATE;
            snprintf(reason, sizeof(reason), "config_apply_unavailable");
            break;
        }
        day_settings_snapshot_t settings;
        esp_err_t result = day_settings_get(&settings);
        if (result != ESP_OK) {
            status = config_error_status(result);
            snprintf(reason, sizeof(reason), "settings_snapshot_failed");
            break;
        }
        day_usb_set_config_args_t args;
        if (day_usb_parse_set_config_args(payload, hdr->payload_len, &settings.config, &args,
                                          field, sizeof(field), reason, sizeof(reason)) != ESP_OK) {
            status = DAY_USB_STATUS_INVALID_ARG;
            break;
        }
        uint32_t expected_revision = args.expected_revision == UINT32_MAX
                                         ? settings.revision
                                         : args.expected_revision;
        day_settings_result_t applied = {0};
        result = s_config_apply_callback(&args.candidate, expected_revision, &applied);
        if (result != ESP_OK) {
            status = config_error_status(result);
            if (applied.field[0]) {
                strlcpy(field, applied.field, sizeof(field));
            }
            if (applied.reason[0]) {
                strlcpy(reason, applied.reason, sizeof(reason));
            }
            break;
        }
        response = make_set_config_payload(&applied, args.applied_groups);
        if (!response) {
            status = DAY_USB_STATUS_STORAGE_ERROR;
            snprintf(reason, sizeof(reason), "config_result_unavailable");
        }
        break;
    }
    case DAY_USB_CMD_PREVIEW_LED: {
        if (s_mode != DAY_USB_MODE_SERIAL) { status=DAY_USB_STATUS_BAD_STATE; snprintf(reason,sizeof(reason),"serial_maintenance_required"); break; }
        if (day_recorder_is_active()) { status=DAY_USB_STATUS_BUSY; snprintf(reason,sizeof(reason),"recording_active"); break; }
        day_usb_preview_led_args_t args;
        if (day_usb_parse_preview_led_args(payload, hdr->payload_len, &args, field, sizeof(field), reason, sizeof(reason)) != ESP_OK) { status=DAY_USB_STATUS_INVALID_ARG; break; }
        if (day_led_preview(args.red,args.green,args.blue,args.brightness_percent,args.duration_ms) != ESP_OK) { status=DAY_USB_STATUS_BAD_STATE; snprintf(reason,sizeof(reason),"led_unavailable"); break; }
        response=strdup("{\"previewing\":true}");
        if (!response) status=DAY_USB_STATUS_STORAGE_ERROR;
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
            response = make_status_payload(false);
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
        response = make_status_payload(false);
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
        response = make_status_payload(false);
        send_response(hdr->seq, cmd, status, response ? response : "{\"ended\":true}");
        free(response);
        tinyusb_cdcacm_write_flush(s_protocol_port, pdMS_TO_TICKS(200));
        day_usb_session_end();
        s_maintenance_active = false;
        (void)day_led_set_usb_handshake(false);
        return;
    default:
        status = DAY_USB_STATUS_UNSUPPORTED_CMD;
        break;
    }

    if (status == DAY_USB_STATUS_OK && cmd != DAY_USB_CMD_END_SESSION) {
        (void)day_led_set_usb_handshake(true);
    }
    if (!response && status != DAY_USB_STATUS_OK) {
        char fallback[256];
        snprintf(fallback, sizeof(fallback),
                 "{\"error\":\"%s\",\"field\":\"%s\",\"reason\":\"%s\","
                 "\"message\":\"request rejected\"}",
                 day_usb_status_name(status), field, reason);
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
    day_usb_rx_msg_t *msg = malloc(sizeof(*msg));
    if (!msg) {
        ESP_LOGE(TAG, "cannot allocate protocol receive buffer");
        s_protocol_task = NULL;
        vTaskDelete(NULL);
        return;
    }
    while (true) {
        if (xQueueReceive(s_rx_queue, msg, pdMS_TO_TICKS(1000)) == pdTRUE) {
            for (size_t i = 0; i < msg->len; ++i) {
                slip_decode_byte(msg->data[i]);
            }
        }
        int64_t now = esp_timer_get_time();
        if (s_maintenance_active && s_last_protocol_us > 0 &&
            now - s_last_protocol_us > DAY_USB_MAINTENANCE_TIMEOUT_US) {
            s_maintenance_active = false;
            (void)day_led_set_usb_handshake(false);
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

static void usb_device_event(tinyusb_event_t *event, void *arg)
{
    (void)arg;
    if (!event) return;
    if (event->id == TINYUSB_EVENT_ATTACHED) {
        latch_host_connection();
        (void)day_led_set_usb_enumerated(true);
    } else if (event->id == TINYUSB_EVENT_DETACHED) {
        (void)day_led_set_usb_handshake(false);
        /* Once a computer has been seen, detaching the cable must not resume
         * Wi-Fi, recording or sleep. Keep the slow green wait indication
         * latched until a genuine power-on reset clears the RTC latch. */
        (void)day_led_set_usb_enumerated(day_usb_link_host_connection_latched());
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
    tinyusb_config_t tusb_cfg = TINYUSB_DEFAULT_CONFIG(usb_device_event);
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
    s_protocol_port = TINYUSB_CDC_ACM_0;
    s_rx_queue = xQueueCreate(8, sizeof(day_usb_rx_msg_t));
    if (!s_rx_queue) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t ret = install_tinyusb(mode);
    if (ret != ESP_OK) {
        return ret;
    }
    if (mode == DAY_USB_MODE_SERIAL) {
        ret = init_cdc_port(TINYUSB_CDC_ACM_0, true);
        if (ret != ESP_OK) {
            return ret;
        }
        ret = init_cdc_port(TINYUSB_CDC_ACM_1, false);
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
    if (xTaskCreate(protocol_task, "day_usb_proto", 8192, NULL, 8, &s_protocol_task) != pdPASS) {
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

esp_err_t day_usb_link_stop(void)
{
#if CONFIG_DAY_USB_LINK_ENABLED
    if (!s_tinyusb_started) {
        return ESP_OK;
    }

    /* The TinyUSB task and CDC class instances must not survive the deep-sleep
     * boundary.  Leaving them active can race the USB PHY shutdown and turn an
     * otherwise normal sleep into an ESP_RST_PANIC reboot loop.
     */
    s_maintenance_active = false;
    s_last_protocol_us = 0;
    if (s_protocol_task) {
        vTaskDelete(s_protocol_task);
        s_protocol_task = NULL;
    }
    esp_log_set_vprintf(vprintf);
    if (s_mode == DAY_USB_MODE_SERIAL && tinyusb_cdcacm_initialized(TINYUSB_CDC_ACM_1)) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(tinyusb_cdcacm_deinit(TINYUSB_CDC_ACM_1));
    }
    if (tinyusb_cdcacm_initialized(TINYUSB_CDC_ACM_0)) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(tinyusb_cdcacm_deinit(TINYUSB_CDC_ACM_0));
    }
    esp_err_t ret = tinyusb_driver_uninstall();
    if (ret != ESP_OK) {
        return ret;
    }
    s_tinyusb_started = false;
    if (s_rx_queue) {
        vQueueDelete(s_rx_queue);
        s_rx_queue = NULL;
    }
    return ESP_OK;
#else
    return ESP_OK;
#endif
}

bool day_usb_link_maintenance_active(void)
{
#if CONFIG_DAY_USB_LINK_ENABLED
    if (day_usb_link_host_connection_latched()) {
        return true;
    }
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
