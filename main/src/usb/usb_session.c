#include "usb_session.h"

#include <stddef.h>
#include <string.h>
#include "esp_attr.h"
#include "esp_random.h"

#define DAY_USB_BOOT_INTENT_MAGIC 0x44445553UL

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint16_t schema_version;
    uint8_t target_mode;
    uint8_t msc_access;
    uint64_t session_id;
    char active_export_id[DAY_USB_ACTIVE_EXPORT_ID_MAX];
    uint32_t crc32;
} day_usb_boot_intent_t;

_Static_assert(sizeof(day_usb_boot_intent_t) == 60, "RTC boot intent layout changed");

static RTC_NOINIT_ATTR day_usb_boot_intent_t s_boot_intent;

static uint32_t intent_crc(const day_usb_boot_intent_t *intent)
{
    return day_usb_crc32(0, (const uint8_t *)intent, offsetof(day_usb_boot_intent_t, crc32));
}

static bool target_valid(uint8_t target)
{
    return target == DAY_USB_BOOT_TARGET_MSC ||
           target == DAY_USB_BOOT_TARGET_SERIAL_MAINTENANCE;
}

bool day_usb_session_intent_valid(void)
{
    return s_boot_intent.magic == DAY_USB_BOOT_INTENT_MAGIC &&
           s_boot_intent.schema_version == DAY_USB_BOOT_INTENT_SCHEMA &&
           target_valid(s_boot_intent.target_mode) &&
           (s_boot_intent.msc_access == DAY_USB_ACCESS_RW ||
            s_boot_intent.msc_access == DAY_USB_ACCESS_RO) &&
           s_boot_intent.session_id != 0 &&
           memchr(s_boot_intent.active_export_id, '\0', sizeof(s_boot_intent.active_export_id)) != NULL &&
           s_boot_intent.crc32 == intent_crc(&s_boot_intent);
}

day_usb_boot_target_t day_usb_session_boot_target(void)
{
    return day_usb_session_intent_valid() ? (day_usb_boot_target_t)s_boot_intent.target_mode
                                          : DAY_USB_BOOT_TARGET_NORMAL;
}

day_usb_access_t day_usb_session_msc_access(void)
{
    return day_usb_session_intent_valid() ? (day_usb_access_t)s_boot_intent.msc_access
                                          : DAY_USB_ACCESS_RW;
}

uint64_t day_usb_session_id(void)
{
    return day_usb_session_intent_valid() ? s_boot_intent.session_id : 0;
}

static uint64_t new_session_id(void)
{
    uint64_t id = 0;
    while (id == 0) {
        esp_fill_random(&id, sizeof(id));
    }
    return id;
}

static void store_intent(day_usb_boot_target_t target, day_usb_access_t access,
                         uint64_t session_id, const char *active_export_id)
{
    day_usb_boot_intent_t next = {
        .magic = DAY_USB_BOOT_INTENT_MAGIC,
        .schema_version = DAY_USB_BOOT_INTENT_SCHEMA,
        .target_mode = (uint8_t)target,
        .msc_access = (uint8_t)access,
        .session_id = session_id ? session_id : new_session_id(),
    };
    if (active_export_id) {
        strlcpy(next.active_export_id, active_export_id, sizeof(next.active_export_id));
    }
    next.crc32 = intent_crc(&next);
    s_boot_intent = next;
}

void day_usb_session_begin_maintenance(void)
{
    if (day_usb_session_intent_valid()) {
        if (s_boot_intent.target_mode == DAY_USB_BOOT_TARGET_MSC) {
            return;
        }
        store_intent(DAY_USB_BOOT_TARGET_SERIAL_MAINTENANCE,
                     (day_usb_access_t)s_boot_intent.msc_access,
                     s_boot_intent.session_id, s_boot_intent.active_export_id);
        return;
    }
    store_intent(DAY_USB_BOOT_TARGET_SERIAL_MAINTENANCE, DAY_USB_ACCESS_RO, 0, NULL);
}

esp_err_t day_usb_session_prepare_msc(day_usb_access_t access, const char *active_export_id)
{
    if (access != DAY_USB_ACCESS_RW && access != DAY_USB_ACCESS_RO) {
        return ESP_ERR_INVALID_ARG;
    }
    if (active_export_id && strnlen(active_export_id, DAY_USB_ACTIVE_EXPORT_ID_MAX) >= DAY_USB_ACTIVE_EXPORT_ID_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    uint64_t session_id = day_usb_session_id();
    store_intent(DAY_USB_BOOT_TARGET_MSC, access, session_id, active_export_id);
    return ESP_OK;
}

esp_err_t day_usb_session_prepare_serial_maintenance(void)
{
    uint64_t session_id = day_usb_session_id();
    day_usb_access_t access = day_usb_session_msc_access();
    char active_export_id[DAY_USB_ACTIVE_EXPORT_ID_MAX] = {0};
    if (day_usb_session_intent_valid()) {
        strlcpy(active_export_id, s_boot_intent.active_export_id, sizeof(active_export_id));
    }
    store_intent(DAY_USB_BOOT_TARGET_SERIAL_MAINTENANCE, access, session_id, active_export_id);
    return ESP_OK;
}

void day_usb_session_end(void)
{
    memset(&s_boot_intent, 0, sizeof(s_boot_intent));
}
