#include "usb_protocol.h"

#include <stdio.h>
#include <string.h>
#include <ctype.h>
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

static bool valid_calendar_date(const char *value)
{
    if (!value || strlen(value) != 10 || value[4] != '-' || value[7] != '-') {
        return false;
    }
    for (size_t i = 0; i < 10; ++i) {
        if (i != 4 && i != 7 && !isdigit((unsigned char)value[i])) {
            return false;
        }
    }
    int year = (value[0] - '0') * 1000 + (value[1] - '0') * 100 +
               (value[2] - '0') * 10 + value[3] - '0';
    int month = (value[5] - '0') * 10 + value[6] - '0';
    int day = (value[8] - '0') * 10 + value[9] - '0';
    static const uint8_t days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    if (year < 2020 || year > 2099 || month < 1 || month > 12 || day < 1) {
        return false;
    }
    int max_day = days[month - 1];
    if (month == 2 && ((year % 4 == 0 && year % 100 != 0) || year % 400 == 0)) {
        max_day = 29;
    }
    return day <= max_day;
}

bool day_usb_export_id_valid(const char *value)
{
    if (!value) {
        return false;
    }
    size_t len = strnlen(value, DAY_USB_EXPORT_ID_MAX);
    if (len < 8 || len >= DAY_USB_EXPORT_ID_MAX || strncmp(value, "exp-", 4) != 0) {
        return false;
    }
    for (size_t i = 4; i < len; ++i) {
        unsigned char c = (unsigned char)value[i];
        if (!isalnum(c) && c != '-') {
            return false;
        }
    }
    return true;
}

static bool client_request_id_valid(const char *value)
{
    if (!value) {
        return false;
    }
    size_t len = strnlen(value, DAY_USB_CLIENT_REQUEST_ID_MAX + 1);
    if (len == 0 || len > DAY_USB_CLIENT_REQUEST_ID_MAX) {
        return false;
    }
    for (size_t i = 0; i < len; ++i) {
        unsigned char c = (unsigned char)value[i];
        if (!isalnum(c) && c != '-' && c != '_' && c != '.' && c != ':') {
            return false;
        }
    }
    return true;
}

static bool parse_u32_field(const cJSON *root, const char *name, uint32_t default_value,
                            uint32_t *out, char *reason, size_t reason_len)
{
    const cJSON *field = cJSON_GetObjectItemCaseSensitive(root, name);
    if (!field) {
        *out = default_value;
        return true;
    }
    if (!cJSON_IsNumber(field) || field->valuedouble < 0 || field->valuedouble > UINT32_MAX) {
        set_reason(reason, reason_len, "pagination_must_be_unsigned_integer");
        return false;
    }
    uint32_t value = (uint32_t)field->valuedouble;
    if ((double)value != field->valuedouble) {
        set_reason(reason, reason_len, "pagination_must_be_unsigned_integer");
        return false;
    }
    *out = value;
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
    args->export_id_count = 0;
    cJSON *root = parse_object(payload, len, reason, reason_len);
    if (!root) {
        return ESP_ERR_INVALID_ARG;
    }
    static const char *const allowed[] = {"access", "export_ids"};
    if (!validate_fields(root, allowed, 2, reason, reason_len)) {
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
    const cJSON *export_ids = cJSON_GetObjectItemCaseSensitive(root, "export_ids");
    if (export_ids) {
        if (!cJSON_IsArray(export_ids) || cJSON_GetArraySize(export_ids) > DAY_USB_EXPORT_IDS_MAX) {
            set_reason(reason, reason_len, "invalid_export_ids");
            cJSON_Delete(root);
            return ESP_ERR_INVALID_ARG;
        }
        const cJSON *item = NULL;
        cJSON_ArrayForEach(item, export_ids) {
            if (!cJSON_IsString(item) || !day_usb_export_id_valid(item->valuestring)) {
                set_reason(reason, reason_len, "invalid_export_id");
                cJSON_Delete(root);
                return ESP_ERR_INVALID_ARG;
            }
            for (size_t i = 0; i < args->export_id_count; ++i) {
                if (strcmp(args->export_ids[i], item->valuestring) == 0) {
                    set_reason(reason, reason_len, "duplicate_export_id");
                    cJSON_Delete(root);
                    return ESP_ERR_INVALID_ARG;
                }
            }
            strlcpy(args->export_ids[args->export_id_count++], item->valuestring,
                    sizeof(args->export_ids[0]));
        }
    }
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t day_usb_parse_begin_export_args(const uint8_t *payload, size_t len,
                                          day_usb_begin_export_args_t *args, char *reason, size_t reason_len)
{
    if (!args) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(args, 0, sizeof(*args));
    cJSON *root = parse_object(payload, len, reason, reason_len);
    if (!root) {
        return ESP_ERR_INVALID_ARG;
    }
    static const char *const allowed[] = {"date", "client_request_id"};
    if (!validate_fields(root, allowed, 2, reason, reason_len)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    const cJSON *date = cJSON_GetObjectItemCaseSensitive(root, "date");
    const cJSON *request_id = cJSON_GetObjectItemCaseSensitive(root, "client_request_id");
    if (!cJSON_IsString(date) || !valid_calendar_date(date->valuestring)) {
        set_reason(reason, reason_len, "invalid_date");
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    if (!cJSON_IsString(request_id) || !client_request_id_valid(request_id->valuestring)) {
        set_reason(reason, reason_len, "invalid_client_request_id");
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    strlcpy(args->date, date->valuestring, sizeof(args->date));
    strlcpy(args->client_request_id, request_id->valuestring, sizeof(args->client_request_id));
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t day_usb_parse_pagination_args(const uint8_t *payload, size_t len,
                                        day_usb_pagination_args_t *args, char *reason, size_t reason_len)
{
    if (!args) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *root = parse_object(payload, len, reason, reason_len);
    if (!root) {
        return ESP_ERR_INVALID_ARG;
    }
    static const char *const allowed[] = {"cursor", "limit"};
    bool valid = validate_fields(root, allowed, 2, reason, reason_len) &&
                 parse_u32_field(root, "cursor", 0, &args->cursor, reason, reason_len) &&
                 parse_u32_field(root, "limit", 20, &args->limit, reason, reason_len);
    if (valid && (args->limit < 1 || args->limit > 20)) {
        set_reason(reason, reason_len, "invalid_pagination_limit");
        valid = false;
    }
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_ARG;
}

esp_err_t day_usb_parse_get_export_status_args(const uint8_t *payload, size_t len,
                                               day_usb_get_export_status_args_t *args,
                                               char *reason, size_t reason_len)
{
    if (!args) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(args, 0, sizeof(*args));
    args->limit = 20;
    cJSON *root = parse_object(payload, len, reason, reason_len);
    if (!root) {
        return ESP_ERR_INVALID_ARG;
    }
    static const char *const allowed[] = {"export_id", "cursor", "limit"};
    if (!validate_fields(root, allowed, 3, reason, reason_len)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    const cJSON *export_id = cJSON_GetObjectItemCaseSensitive(root, "export_id");
    if (export_id) {
        if (!cJSON_IsString(export_id) || !day_usb_export_id_valid(export_id->valuestring) ||
            cJSON_GetObjectItemCaseSensitive(root, "cursor") ||
            cJSON_GetObjectItemCaseSensitive(root, "limit")) {
            set_reason(reason, reason_len, "invalid_export_status_query");
            cJSON_Delete(root);
            return ESP_ERR_INVALID_ARG;
        }
        args->has_export_id = true;
        strlcpy(args->export_id, export_id->valuestring, sizeof(args->export_id));
    } else if (!parse_u32_field(root, "cursor", 0, &args->cursor, reason, reason_len) ||
               !parse_u32_field(root, "limit", 20, &args->limit, reason, reason_len) ||
               args->limit < 1 || args->limit > 20) {
        set_reason(reason, reason_len, "invalid_pagination_limit");
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    cJSON_Delete(root);
    return ESP_OK;
}

static bool parse_required_export_id(const cJSON *root, char *out, size_t out_len,
                                     char *reason, size_t reason_len)
{
    const cJSON *export_id = cJSON_GetObjectItemCaseSensitive(root, "export_id");
    if (!cJSON_IsString(export_id) || !day_usb_export_id_valid(export_id->valuestring)) {
        set_reason(reason, reason_len, "invalid_export_id");
        return false;
    }
    strlcpy(out, export_id->valuestring, out_len);
    return true;
}

esp_err_t day_usb_parse_commit_export_args(const uint8_t *payload, size_t len,
                                           day_usb_commit_export_args_t *args,
                                           char *reason, size_t reason_len)
{
    if (!args) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(args, 0, sizeof(*args));
    cJSON *root = parse_object(payload, len, reason, reason_len);
    if (!root) {
        return ESP_ERR_INVALID_ARG;
    }
    static const char *const allowed[] = {
        "export_id", "manifest_sha256", "confirm_record_count"
    };
    if (!validate_fields(root, allowed, 3, reason, reason_len) ||
        !parse_required_export_id(root, args->export_id, sizeof(args->export_id),
                                  reason, reason_len)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    const cJSON *sha = cJSON_GetObjectItemCaseSensitive(root, "manifest_sha256");
    if (!cJSON_IsString(sha) || !sha->valuestring || strlen(sha->valuestring) != 64) {
        set_reason(reason, reason_len, "invalid_manifest_sha256");
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    for (size_t i = 0; i < 64; ++i) {
        unsigned char value = (unsigned char)sha->valuestring[i];
        if (!isxdigit(value)) {
            set_reason(reason, reason_len, "invalid_manifest_sha256");
            cJSON_Delete(root);
            return ESP_ERR_INVALID_ARG;
        }
        args->manifest_sha256[i] = (char)tolower(value);
    }
    args->manifest_sha256[64] = '\0';
    const cJSON *count = cJSON_GetObjectItemCaseSensitive(root, "confirm_record_count");
    if (!cJSON_IsNumber(count) || count->valuedouble < 0 || count->valuedouble > UINT32_MAX) {
        set_reason(reason, reason_len, "invalid_confirm_record_count");
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    args->confirm_record_count = (uint32_t)count->valuedouble;
    if ((double)args->confirm_record_count != count->valuedouble) {
        set_reason(reason, reason_len, "invalid_confirm_record_count");
        cJSON_Delete(root);
        return ESP_ERR_INVALID_ARG;
    }
    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t day_usb_parse_export_id_args(const uint8_t *payload, size_t len,
                                       day_usb_export_id_args_t *args,
                                       char *reason, size_t reason_len)
{
    if (!args) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(args, 0, sizeof(*args));
    cJSON *root = parse_object(payload, len, reason, reason_len);
    if (!root) {
        return ESP_ERR_INVALID_ARG;
    }
    static const char *const allowed[] = {"export_id"};
    bool valid = validate_fields(root, allowed, 1, reason, reason_len) &&
                 parse_required_export_id(root, args->export_id, sizeof(args->export_id),
                                          reason, reason_len);
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_ARG;
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
