#include "usb_protocol.h"

#include <stdio.h>
#include <string.h>
#include "cJSON.h"

_Static_assert(sizeof(day_usb_frame_header_t) == DAY_USB_FRAME_HEADER_LEN,
               "USB Link frame header must remain 20 bytes");

static void set_reason(char *reason, size_t len, const char *value)
{
    if (reason && len > 0) {
        snprintf(reason, len, "%s", value ? value : "invalid_argument");
    }
}

static bool valid_utf8(const uint8_t *data, size_t len)
{
    for (size_t i = 0; i < len;) {
        uint8_t c = data[i++];
        if (c <= 0x7f) {
            continue;
        }
        size_t continuation = 0;
        uint32_t codepoint = 0;
        if ((c & 0xe0) == 0xc0) {
            continuation = 1;
            codepoint = c & 0x1f;
        } else if ((c & 0xf0) == 0xe0) {
            continuation = 2;
            codepoint = c & 0x0f;
        } else if ((c & 0xf8) == 0xf0) {
            continuation = 3;
            codepoint = c & 0x07;
        } else {
            return false;
        }
        if (i + continuation > len) {
            return false;
        }
        for (size_t n = 0; n < continuation; ++n) {
            uint8_t next = data[i++];
            if ((next & 0xc0) != 0x80) {
                return false;
            }
            codepoint = (codepoint << 6) | (next & 0x3f);
        }
        if ((continuation == 1 && codepoint < 0x80) ||
            (continuation == 2 && codepoint < 0x800) ||
            (continuation == 3 && codepoint < 0x10000) ||
            codepoint > 0x10ffff || (codepoint >= 0xd800 && codepoint <= 0xdfff)) {
            return false;
        }
    }
    return true;
}

static cJSON *parse_object(const uint8_t *payload, size_t len, char *reason, size_t reason_len)
{
    if (len == 0) {
        return cJSON_CreateObject();
    }
    if (!payload || len > DAY_USB_PAYLOAD_MAX || memchr(payload, '\0', len) != NULL) {
        set_reason(reason, reason_len, "invalid_payload");
        return NULL;
    }
    if (!valid_utf8(payload, len)) {
        set_reason(reason, reason_len, "invalid_utf8");
        return NULL;
    }
    const char *end = NULL;
    cJSON *root = cJSON_ParseWithLengthOpts((const char *)payload, len, &end, false);
    if (!root || end != (const char *)payload + len || !cJSON_IsObject(root)) {
        cJSON_Delete(root);
        set_reason(reason, reason_len, "invalid_json_object");
        return NULL;
    }
    return root;
}

static bool field_allowed(const char *name, const char *const *allowed, size_t allowed_count)
{
    for (size_t i = 0; i < allowed_count; ++i) {
        if (strcmp(name, allowed[i]) == 0) {
            return true;
        }
    }
    return false;
}

static bool validate_fields(const cJSON *root, const char *const *allowed, size_t allowed_count,
                            char *reason, size_t reason_len)
{
    for (const cJSON *field = root->child; field; field = field->next) {
        if (!field->string || !field_allowed(field->string, allowed, allowed_count)) {
            set_reason(reason, reason_len, "unknown_field");
            return false;
        }
        for (const cJSON *other = field->next; other; other = other->next) {
            if (other->string && strcmp(field->string, other->string) == 0) {
                set_reason(reason, reason_len, "duplicate_field");
                return false;
            }
        }
    }
    return true;
}

const char *day_usb_status_name(day_usb_status_t status)
{
    switch (status) {
    case DAY_USB_STATUS_OK: return "OK";
    case DAY_USB_STATUS_BAD_FRAME: return "BAD_FRAME";
    case DAY_USB_STATUS_UNSUPPORTED_VERSION: return "UNSUPPORTED_VERSION";
    case DAY_USB_STATUS_UNSUPPORTED_CMD: return "UNSUPPORTED_CMD";
    case DAY_USB_STATUS_INVALID_ARG: return "INVALID_ARG";
    case DAY_USB_STATUS_BUSY: return "BUSY";
    case DAY_USB_STATUS_STORAGE_ERROR: return "STORAGE_ERROR";
    case DAY_USB_STATUS_BAD_STATE: return "BAD_STATE";
    case DAY_USB_STATUS_TIMEOUT: return "TIMEOUT";
    default: return "UNKNOWN";
    }
}

uint32_t day_usb_crc32(uint32_t crc, const uint8_t *data, size_t len)
{
    crc = ~crc;
    for (size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit) {
            uint32_t mask = -(crc & 1U);
            crc = (crc >> 1) ^ (0xEDB88320U & mask);
        }
    }
    return ~crc;
}

esp_err_t day_usb_validate_empty_args(const uint8_t *payload, size_t len, char *reason, size_t reason_len)
{
    cJSON *root = parse_object(payload, len, reason, reason_len);
    if (!root) {
        return ESP_ERR_INVALID_ARG;
    }
    bool valid = validate_fields(root, NULL, 0, reason, reason_len);
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_ARG;
}

esp_err_t day_usb_parse_enter_msc_args(const uint8_t *payload, size_t len,
                                       day_usb_enter_msc_args_t *args, char *reason, size_t reason_len)
{
    if (!args) {
        return ESP_ERR_INVALID_ARG;
    }
    args->access = DAY_USB_ACCESS_RW;
    cJSON *root = parse_object(payload, len, reason, reason_len);
    if (!root) {
        return ESP_ERR_INVALID_ARG;
    }
    static const char *const allowed[] = {"access"};
    if (!validate_fields(root, allowed, 1, reason, reason_len)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    const cJSON *access = cJSON_GetObjectItemCaseSensitive(root, "access");
    if (access) {
        if (!cJSON_IsString(access) || !access->valuestring) {
            set_reason(reason, reason_len, "access_must_be_string");
            cJSON_Delete(root);
            return ESP_ERR_INVALID_ARG;
        }
        if (strcmp(access->valuestring, "ro") == 0) {
            args->access = DAY_USB_ACCESS_RO;
        } else if (strcmp(access->valuestring, "rw") != 0) {
            set_reason(reason, reason_len, "invalid_access");
            cJSON_Delete(root);
            return ESP_ERR_INVALID_ARG;
        }
    }
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t day_usb_parse_exit_msc_args(const uint8_t *payload, size_t len,
                                      day_usb_exit_msc_args_t *args, char *reason, size_t reason_len)
{
    if (!args) {
        return ESP_ERR_INVALID_ARG;
    }
    args->force = false;
    args->next_mode = DAY_USB_NEXT_MODE_NORMAL;
    cJSON *root = parse_object(payload, len, reason, reason_len);
    if (!root) {
        return ESP_ERR_INVALID_ARG;
    }
    static const char *const allowed[] = {"force", "next_mode"};
    if (!validate_fields(root, allowed, 2, reason, reason_len)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    const cJSON *force = cJSON_GetObjectItemCaseSensitive(root, "force");
    if (force) {
        if (!cJSON_IsBool(force)) {
            set_reason(reason, reason_len, "force_must_be_boolean");
            cJSON_Delete(root);
            return ESP_ERR_INVALID_ARG;
        }
        args->force = cJSON_IsTrue(force);
    }
    const cJSON *next_mode = cJSON_GetObjectItemCaseSensitive(root, "next_mode");
    if (next_mode) {
        if (!cJSON_IsString(next_mode) || !next_mode->valuestring) {
            set_reason(reason, reason_len, "next_mode_must_be_string");
            cJSON_Delete(root);
            return ESP_ERR_INVALID_ARG;
        }
        if (strcmp(next_mode->valuestring, "maintenance") == 0) {
            args->next_mode = DAY_USB_NEXT_MODE_MAINTENANCE;
        } else if (strcmp(next_mode->valuestring, "normal") != 0) {
            set_reason(reason, reason_len, "invalid_next_mode");
            cJSON_Delete(root);
            return ESP_ERR_INVALID_ARG;
        }
    }
    cJSON_Delete(root);
    return ESP_OK;
}
