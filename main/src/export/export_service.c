#include "export_service.h"

#include <dirent.h>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <unistd.h>
#include "cJSON.h"
#include "day_pins.h"
#include "device_identity.h"
#include "esp_log.h"
#include "esp_random.h"
#include "psa/crypto.h"
#include "storage_service.h"

#define DAY_EXPORT_DIR DAY_SD_MOUNT_POINT "/EXPORTS"
#define DAY_EXPORT_MAX_ACTIVE 32
#define DAY_EXPORT_JSON_MAX (512U * 1024U)
#define DAY_RECORD_NAME_LEN 22

static const char *TAG = "day_export";
static const char *const s_record_files[] = {"meta.json", "video.avi", "audio.wav", "imu.json"};

typedef struct {
    char name[DAY_RECORD_NAME_LEN + 1];
    char record_id[64];
    uint64_t file_sizes[4];
    uint64_t total_bytes;
} record_info_t;

typedef struct {
    char date[11];
    uint32_t record_count;
    uint64_t total_bytes;
} date_summary_t;

typedef struct {
    char export_id[DAY_USB_EXPORT_ID_MAX];
    char client_request_id[DAY_USB_CLIENT_REQUEST_ID_MAX + 1];
    char date[11];
    char state[16];
    char manifest_sha256[65];
    char last_error[64];
    uint32_t record_count;
    uint32_t deleted_count;
    uint64_t total_bytes;
} transaction_info_t;

static void set_reason(char *reason, size_t len, const char *value)
{
    if (reason && len > 0) {
        snprintf(reason, len, "%s", value ? value : "storage_error");
    }
}

static bool regular_file_size(const char *path, uint64_t *size)
{
    struct stat st;
    if (stat(path, &st) != 0 || !S_ISREG(st.st_mode)) {
        return false;
    }
    if (size) {
        *size = (uint64_t)st.st_size;
    }
    return true;
}

static bool record_name_parse(const char *name, char date[11])
{
    if (!name || strlen(name) != DAY_RECORD_NAME_LEN || strncmp(name, "REC_", 4) != 0 ||
        name[8] != '_' || name[15] != '_') {
        return false;
    }
    for (size_t i = 4; i < DAY_RECORD_NAME_LEN; ++i) {
        if (i != 8 && i != 15 && (name[i] < '0' || name[i] > '9')) {
            return false;
        }
    }
    int year = 2000 + (name[9] - '0') * 10 + name[10] - '0';
    int month = (name[11] - '0') * 10 + name[12] - '0';
    int day = (name[13] - '0') * 10 + name[14] - '0';
    int hour = (name[16] - '0') * 10 + name[17] - '0';
    int minute = (name[18] - '0') * 10 + name[19] - '0';
    int second = (name[20] - '0') * 10 + name[21] - '0';
    static const uint8_t days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    if (month < 1 || month > 12 || day < 1 || hour > 23 || minute > 59 || second > 59) {
        return false;
    }
    int max_day = days[month - 1];
    if (month == 2 && year % 4 == 0) {
        max_day = 29;
    }
    if (day > max_day) {
        return false;
    }
    snprintf(date, 11, "20%c%c-%c%c-%c%c",
             name[9], name[10], name[11], name[12], name[13], name[14]);
    return true;
}

static bool load_record_identity(const char *meta_path, const char *record_name,
                                 uint64_t meta_size, char record_id[64])
{
    int fallback = snprintf(record_id, 64, "%s-%s", day_device_id(), record_name);
    if (fallback < 0 || fallback >= 64 || meta_size == 0 || meta_size > 64U * 1024U) {
        return fallback > 0 && fallback < 64;
    }
    FILE *file = fopen(meta_path, "rb");
    if (!file) {
        return true;
    }
    char *text = malloc((size_t)meta_size + 1);
    if (!text) {
        fclose(file);
        return true;
    }
    bool read_ok = fread(text, 1, (size_t)meta_size, file) == (size_t)meta_size;
    if (fclose(file) != 0) {
        read_ok = false;
    }
    if (!read_ok) {
        free(text);
        return true;
    }
    text[meta_size] = '\0';
    const char *end = NULL;
    cJSON *meta = cJSON_ParseWithLengthOpts(text, (size_t)meta_size, &end, false);
    bool parsed_exactly = meta && end == text + meta_size && cJSON_IsObject(meta);
    free(text);
    if (!parsed_exactly) {
        cJSON_Delete(meta);
        return true;
    }
    const cJSON *schema = cJSON_GetObjectItemCaseSensitive(meta, "schema_version");
    if (!cJSON_IsNumber(schema) || schema->valuedouble != 2) {
        cJSON_Delete(meta);
        return true;
    }
    const cJSON *state = cJSON_GetObjectItemCaseSensitive(meta, "record_state");
    const cJSON *device = cJSON_GetObjectItemCaseSensitive(meta, "device_id");
    const cJSON *identity = cJSON_GetObjectItemCaseSensitive(meta, "record_id");
    size_t device_len = strlen(day_device_id());
    bool valid = cJSON_IsString(state) && strcmp(state->valuestring, "complete") == 0 &&
                 cJSON_IsString(device) && strcmp(device->valuestring, day_device_id()) == 0 &&
                 cJSON_IsString(identity) && identity->valuestring &&
                 strlen(identity->valuestring) < 64 &&
                 strncmp(identity->valuestring, day_device_id(), device_len) == 0 &&
                 identity->valuestring[device_len] == '-';
    if (valid) {
        strlcpy(record_id, identity->valuestring, 64);
    }
    cJSON_Delete(meta);
    return valid;
}

static bool load_complete_record(const char *name, record_info_t *record, char date[11])
{
    if (!record_name_parse(name, date)) {
        return false;
    }
    char dir_path[128];
    int written = snprintf(dir_path, sizeof(dir_path), DAY_SD_MOUNT_POINT "/%s", name);
    if (written < 0 || written >= (int)sizeof(dir_path)) {
        return false;
    }
    struct stat dir_stat;
    if (stat(dir_path, &dir_stat) != 0 || !S_ISDIR(dir_stat.st_mode)) {
        return false;
    }
    memset(record, 0, sizeof(*record));
    strlcpy(record->name, name, sizeof(record->name));
    for (size_t i = 0; i < 4; ++i) {
        char file_path[160];
        written = snprintf(file_path, sizeof(file_path), "%s/%s", dir_path, s_record_files[i]);
        if (written < 0 || written >= (int)sizeof(file_path) ||
            !regular_file_size(file_path, &record->file_sizes[i])) {
            return false;
        }
        record->total_bytes += record->file_sizes[i];
    }
    char meta_path[160];
    written = snprintf(meta_path, sizeof(meta_path), "%s/meta.json", dir_path);
    return written > 0 && written < (int)sizeof(meta_path) &&
           load_record_identity(meta_path, name, record->file_sizes[0], record->record_id);
}

static int compare_dates_desc(const void *left, const void *right)
{
    return strcmp(((const date_summary_t *)right)->date, ((const date_summary_t *)left)->date);
}

static int compare_records(const void *left, const void *right)
{
    return strcmp(((const record_info_t *)left)->name, ((const record_info_t *)right)->name);
}

static int compare_transactions(const void *left, const void *right)
{
    return strcmp(((const transaction_info_t *)left)->export_id,
                  ((const transaction_info_t *)right)->export_id);
}

static esp_err_t collect_dates(date_summary_t **out_items, size_t *out_count)
{
    *out_items = NULL;
    *out_count = 0;
    DIR *dir = opendir(DAY_SD_MOUNT_POINT);
    if (!dir) {
        return ESP_ERR_INVALID_STATE;
    }
    date_summary_t *items = NULL;
    size_t count = 0;
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        record_info_t record;
        char date[11];
        if (!load_complete_record(entry->d_name, &record, date)) {
            continue;
        }
        size_t index = 0;
        while (index < count && strcmp(items[index].date, date) != 0) {
            ++index;
        }
        if (index == count) {
            date_summary_t *next = realloc(items, (count + 1) * sizeof(*items));
            if (!next) {
                free(items);
                closedir(dir);
                return ESP_ERR_NO_MEM;
            }
            items = next;
            memset(&items[count], 0, sizeof(items[count]));
            strlcpy(items[count].date, date, sizeof(items[count].date));
            ++count;
        }
        items[index].record_count++;
        items[index].total_bytes += record.total_bytes;
    }
    closedir(dir);
    qsort(items, count, sizeof(*items), compare_dates_desc);
    *out_items = items;
    *out_count = count;
    return ESP_OK;
}

static esp_err_t collect_records(const char *wanted_date, record_info_t **out_records, size_t *out_count,
                                 uint64_t *out_total)
{
    *out_records = NULL;
    *out_count = 0;
    *out_total = 0;
    DIR *dir = opendir(DAY_SD_MOUNT_POINT);
    if (!dir) {
        return ESP_ERR_INVALID_STATE;
    }
    record_info_t *records = NULL;
    size_t count = 0;
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        record_info_t record;
        char date[11];
        if (!load_complete_record(entry->d_name, &record, date) || strcmp(date, wanted_date) != 0) {
            continue;
        }
        record_info_t *next = realloc(records, (count + 1) * sizeof(*records));
        if (!next) {
            free(records);
            closedir(dir);
            return ESP_ERR_NO_MEM;
        }
        records = next;
        records[count++] = record;
        *out_total += record.total_bytes;
    }
    closedir(dir);
    qsort(records, count, sizeof(*records), compare_records);
    *out_records = records;
    *out_count = count;
    return ESP_OK;
}

static esp_err_t ensure_export_dir(void)
{
    struct stat st;
    if (stat(DAY_EXPORT_DIR, &st) == 0) {
        return S_ISDIR(st.st_mode) ? ESP_OK : ESP_FAIL;
    }
    return mkdir(DAY_EXPORT_DIR, 0775) == 0 ? ESP_OK : ESP_FAIL;
}

static esp_err_t atomic_write(const char *path, const char *data, size_t len)
{
    char tmp[160];
    char backup[160];
    if (snprintf(tmp, sizeof(tmp), "%s.tmp", path) >= (int)sizeof(tmp) ||
        snprintf(backup, sizeof(backup), "%s.bak", path) >= (int)sizeof(backup)) {
        return ESP_ERR_INVALID_SIZE;
    }
    (void)unlink(tmp);
    FILE *file = fopen(tmp, "wb");
    if (!file) {
        return ESP_FAIL;
    }
    esp_err_t result = ESP_OK;
    if (fwrite(data, 1, len, file) != len || fflush(file) != 0 || fsync(fileno(file)) != 0) {
        result = ESP_FAIL;
    }
    if (fclose(file) != 0) {
        result = ESP_FAIL;
    }
    if (result != ESP_OK) {
        (void)unlink(tmp);
        return result;
    }
    bool had_original = access(path, F_OK) == 0;
    if (had_original) {
        (void)unlink(backup);
        if (rename(path, backup) != 0) {
            (void)unlink(tmp);
            return ESP_FAIL;
        }
    }
    if (rename(tmp, path) != 0) {
        if (had_original) {
            (void)rename(backup, path);
        }
        (void)unlink(tmp);
        return ESP_FAIL;
    }
    if (had_original) {
        (void)unlink(backup);
    }
    return ESP_OK;
}

static cJSON *read_json_file(const char *path)
{
    struct stat st;
    if (stat(path, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size <= 0 || st.st_size > DAY_EXPORT_JSON_MAX) {
        return NULL;
    }
    FILE *file = fopen(path, "rb");
    if (!file) {
        return NULL;
    }
    size_t len = (size_t)st.st_size;
    char *data = malloc(len + 1);
    if (!data) {
        fclose(file);
        return NULL;
    }
    bool ok = fread(data, 1, len, file) == len;
    if (fclose(file) != 0) {
        ok = false;
    }
    if (!ok) {
        free(data);
        return NULL;
    }
    data[len] = '\0';
    const char *end = NULL;
    cJSON *json = cJSON_ParseWithLengthOpts(data, len, &end, false);
    if (!json || end != data + len || !cJSON_IsObject(json)) {
        cJSON_Delete(json);
        json = NULL;
    }
    free(data);
    return json;
}

static cJSON *read_json_with_backup(const char *path)
{
    cJSON *json = read_json_file(path);
    if (json) {
        return json;
    }
    char backup[160];
    if (snprintf(backup, sizeof(backup), "%s.bak", path) >= (int)sizeof(backup)) {
        return NULL;
    }
    return read_json_file(backup);
}

static bool copy_json_string(const cJSON *root, const char *name, char *out, size_t out_len)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, name);
    if (!cJSON_IsString(item) || !item->valuestring || strlen(item->valuestring) >= out_len) {
        return false;
    }
    strlcpy(out, item->valuestring, out_len);
    return true;
}

static bool copy_json_u32(const cJSON *root, const char *name, uint32_t *out)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, name);
    if (!cJSON_IsNumber(item) || item->valuedouble < 0 || item->valuedouble > UINT32_MAX) {
        return false;
    }
    *out = (uint32_t)item->valuedouble;
    return (double)*out == item->valuedouble;
}

static bool copy_json_u64(const cJSON *root, const char *name, uint64_t *out)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, name);
    if (!cJSON_IsNumber(item) || item->valuedouble < 0 || item->valuedouble > 9007199254740991.0) {
        return false;
    }
    *out = (uint64_t)item->valuedouble;
    return (double)*out == item->valuedouble;
}

static bool transaction_paths(const char *export_id, char manifest[128], char state[128])
{
    if (!day_usb_export_id_valid(export_id)) {
        return false;
    }
    return snprintf(manifest, 128, DAY_EXPORT_DIR "/%s.json", export_id) < 128 &&
           snprintf(state, 128, DAY_EXPORT_DIR "/%s.state", export_id) < 128;
}

static esp_err_t load_transaction(const char *export_id, transaction_info_t *info)
{
    char manifest_path[128];
    char state_path[128];
    if (!info || !transaction_paths(export_id, manifest_path, state_path)) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *manifest = read_json_with_backup(manifest_path);
    cJSON *state = read_json_with_backup(state_path);
    if (!manifest || !state) {
        cJSON_Delete(manifest);
        cJSON_Delete(state);
        return ESP_ERR_NOT_FOUND;
    }
    memset(info, 0, sizeof(*info));
    uint32_t manifest_count = 0;
    bool ok = copy_json_string(manifest, "export_id", info->export_id, sizeof(info->export_id)) &&
              strcmp(info->export_id, export_id) == 0 &&
              copy_json_string(manifest, "client_request_id", info->client_request_id,
                               sizeof(info->client_request_id)) &&
              copy_json_string(manifest, "date", info->date, sizeof(info->date)) &&
              copy_json_u32(manifest, "record_count", &manifest_count) &&
              copy_json_u64(manifest, "total_bytes", &info->total_bytes) &&
              copy_json_string(state, "export_id", info->export_id, sizeof(info->export_id)) &&
              strcmp(info->export_id, export_id) == 0 &&
              copy_json_string(state, "state", info->state, sizeof(info->state)) &&
              copy_json_string(state, "manifest_sha256", info->manifest_sha256,
                               sizeof(info->manifest_sha256)) &&
              copy_json_u32(state, "record_count", &info->record_count) &&
              copy_json_u32(state, "deleted_count", &info->deleted_count) &&
              info->record_count == manifest_count;
    const cJSON *last_error = cJSON_GetObjectItemCaseSensitive(state, "last_error");
    if (ok && cJSON_IsString(last_error) && last_error->valuestring) {
        strlcpy(info->last_error, last_error->valuestring, sizeof(info->last_error));
    }
    cJSON_Delete(manifest);
    cJSON_Delete(state);
    return ok ? ESP_OK : ESP_ERR_INVALID_CRC;
}

static bool export_id_from_filename(const char *name, char export_id[DAY_USB_EXPORT_ID_MAX])
{
    size_t len = name ? strlen(name) : 0;
    if (len <= 5 || strcmp(name + len - 5, ".json") != 0 || len - 5 >= DAY_USB_EXPORT_ID_MAX) {
        return false;
    }
    memcpy(export_id, name, len - 5);
    export_id[len - 5] = '\0';
    return day_usb_export_id_valid(export_id);
}

static esp_err_t sha256_text(const char *data, size_t len, char hex[65])
{
    uint8_t digest[32];
    size_t digest_len = 0;
    psa_status_t status = psa_crypto_init();
    if (status == PSA_SUCCESS) {
        status = psa_hash_compute(PSA_ALG_SHA_256, (const uint8_t *)data, len,
                                  digest, sizeof(digest), &digest_len);
    }
    if (status != PSA_SUCCESS || digest_len != sizeof(digest)) {
        return ESP_FAIL;
    }
    for (size_t i = 0; i < sizeof(digest); ++i) {
        snprintf(hex + i * 2, 3, "%02x", digest[i]);
    }
    hex[64] = '\0';
    return ESP_OK;
}

static int64_t utc_now_ms(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

static esp_err_t json_to_response(cJSON *json, char *response, size_t response_len)
{
    char *text = cJSON_PrintUnformatted(json);
    if (!text) {
        return ESP_ERR_NO_MEM;
    }
    size_t len = strlen(text);
    esp_err_t result = len < response_len && len <= DAY_USB_PAYLOAD_MAX ? ESP_OK : ESP_ERR_NO_MEM;
    if (result == ESP_OK) {
        memcpy(response, text, len + 1);
    }
    free(text);
    return result;
}

static esp_err_t transaction_response(const transaction_info_t *info, char *response, size_t response_len)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) {
        return ESP_ERR_NO_MEM;
    }
    cJSON_AddStringToObject(root, "export_id", info->export_id);
    cJSON_AddStringToObject(root, "date", info->date);
    cJSON_AddStringToObject(root, "state", info->state);
    cJSON_AddNumberToObject(root, "record_count", info->record_count);
    cJSON_AddNumberToObject(root, "deleted_count", info->deleted_count);
    cJSON_AddNumberToObject(root, "total_bytes", (double)info->total_bytes);
    char path[64];
    snprintf(path, sizeof(path), "/EXPORTS/%s.json", info->export_id);
    cJSON_AddStringToObject(root, "manifest_path", path);
    cJSON_AddStringToObject(root, "manifest_sha256", info->manifest_sha256);
    cJSON_AddStringToObject(root, "last_error", info->last_error);
    esp_err_t result = json_to_response(root, response, response_len);
    cJSON_Delete(root);
    return result;
}

static bool json_fields_are_exact(const cJSON *object, const char *const *allowed, size_t allowed_count)
{
    if (!cJSON_IsObject(object)) {
        return false;
    }
    size_t count = 0;
    for (const cJSON *item = object->child; item; item = item->next) {
        if (!item->string) {
            return false;
        }
        bool known = false;
        for (size_t i = 0; i < allowed_count; ++i) {
            if (strcmp(item->string, allowed[i]) == 0) {
                known = true;
                break;
            }
        }
        if (!known) {
            return false;
        }
        for (const cJSON *other = item->next; other; other = other->next) {
            if (other->string && strcmp(item->string, other->string) == 0) {
                return false;
            }
        }
        ++count;
    }
    return count == allowed_count;
}

static cJSON *read_json_text_file(const char *path, char **out_text, size_t *out_len)
{
    *out_text = NULL;
    *out_len = 0;
    struct stat st;
    if (stat(path, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size <= 0 ||
        st.st_size > DAY_EXPORT_JSON_MAX) {
        return NULL;
    }
    FILE *file = fopen(path, "rb");
    if (!file) {
        return NULL;
    }
    size_t len = (size_t)st.st_size;
    char *text = malloc(len + 1);
    if (!text) {
        fclose(file);
        return NULL;
    }
    bool ok = fread(text, 1, len, file) == len;
    if (fclose(file) != 0) {
        ok = false;
    }
    if (!ok) {
        free(text);
        return NULL;
    }
    text[len] = '\0';
    const char *end = NULL;
    cJSON *json = cJSON_ParseWithLengthOpts(text, len, &end, false);
    if (!json || end != text + len || !cJSON_IsObject(json)) {
        cJSON_Delete(json);
        free(text);
        return NULL;
    }
    *out_text = text;
    *out_len = len;
    return json;
}

static cJSON *read_json_text_with_backup(const char *path, char **out_text, size_t *out_len)
{
    cJSON *json = read_json_text_file(path, out_text, out_len);
    if (json) {
        return json;
    }
    char backup[160];
    if (snprintf(backup, sizeof(backup), "%s.bak", path) >= (int)sizeof(backup)) {
        return NULL;
    }
    return read_json_text_file(backup, out_text, out_len);
}

static int record_file_index(const char *name)
{
    if (!name) {
        return -1;
    }
    for (size_t i = 0; i < 4; ++i) {
        if (strcmp(name, s_record_files[i]) == 0) {
            return (int)i;
        }
    }
    return -1;
}

static esp_err_t parse_verified_manifest(const char *export_id, const char *expected_sha,
                                         uint32_t expected_count, record_info_t **out_records,
                                         size_t *out_count, char *reason, size_t reason_len)
{
    *out_records = NULL;
    *out_count = 0;
    char manifest_path[128];
    char state_path[128];
    if (!transaction_paths(export_id, manifest_path, state_path)) {
        set_reason(reason, reason_len, "invalid_export_id");
        return ESP_ERR_INVALID_ARG;
    }
    char *text = NULL;
    size_t text_len = 0;
    cJSON *manifest = read_json_text_with_backup(manifest_path, &text, &text_len);
    if (!manifest) {
        set_reason(reason, reason_len, "invalid_export_manifest");
        return ESP_ERR_INVALID_CRC;
    }
    char actual_sha[65];
    esp_err_t result = sha256_text(text, text_len, actual_sha);
    free(text);
    if (result != ESP_OK || strcmp(actual_sha, expected_sha) != 0) {
        cJSON_Delete(manifest);
        set_reason(reason, reason_len, "manifest_sha256_mismatch");
        return ESP_ERR_INVALID_CRC;
    }
    static const char *const manifest_fields[] = {
        "schema_version", "export_id", "client_request_id", "device_id", "date",
        "created_at_utc_ms", "state", "record_count", "total_bytes", "records"
    };
    const cJSON *schema = cJSON_GetObjectItemCaseSensitive(manifest, "schema_version");
    const cJSON *manifest_export_id = cJSON_GetObjectItemCaseSensitive(manifest, "export_id");
    const cJSON *device_id = cJSON_GetObjectItemCaseSensitive(manifest, "device_id");
    const cJSON *date = cJSON_GetObjectItemCaseSensitive(manifest, "date");
    const cJSON *manifest_state = cJSON_GetObjectItemCaseSensitive(manifest, "state");
    const cJSON *records_json = cJSON_GetObjectItemCaseSensitive(manifest, "records");
    uint32_t record_count = 0;
    uint64_t declared_total = 0;
    bool valid = json_fields_are_exact(manifest, manifest_fields,
                                       sizeof(manifest_fields) / sizeof(manifest_fields[0])) &&
                 cJSON_IsNumber(schema) && schema->valuedouble == 2 &&
                 cJSON_IsString(manifest_export_id) &&
                 strcmp(manifest_export_id->valuestring, export_id) == 0 &&
                 cJSON_IsString(device_id) && strcmp(device_id->valuestring, day_device_id()) == 0 &&
                 cJSON_IsString(date) && strlen(date->valuestring) == 10 &&
                 cJSON_IsString(manifest_state) && strcmp(manifest_state->valuestring, "prepared") == 0 &&
                 copy_json_u32(manifest, "record_count", &record_count) &&
                 copy_json_u64(manifest, "total_bytes", &declared_total) &&
                 record_count == expected_count && record_count > 0 &&
                 cJSON_IsArray(records_json) && cJSON_GetArraySize(records_json) == (int)record_count;
    if (!valid || record_count > DAY_EXPORT_JSON_MAX / sizeof(record_info_t)) {
        cJSON_Delete(manifest);
        set_reason(reason, reason_len, "invalid_export_manifest");
        return ESP_ERR_INVALID_CRC;
    }
    record_info_t *records = calloc(record_count, sizeof(*records));
    if (!records) {
        cJSON_Delete(manifest);
        return ESP_ERR_NO_MEM;
    }
    static const char *const record_fields[] = {"name", "record_id", "files"};
    static const char *const file_fields[] = {"name", "size"};
    uint64_t calculated_total = 0;
    for (uint32_t i = 0; valid && i < record_count; ++i) {
        const cJSON *record = cJSON_GetArrayItem(records_json, (int)i);
        const cJSON *name = cJSON_GetObjectItemCaseSensitive(record, "name");
        const cJSON *record_id = cJSON_GetObjectItemCaseSensitive(record, "record_id");
        const cJSON *files = cJSON_GetObjectItemCaseSensitive(record, "files");
        char record_date[11];
        valid = json_fields_are_exact(record, record_fields,
                                      sizeof(record_fields) / sizeof(record_fields[0])) &&
                cJSON_IsString(name) && record_name_parse(name->valuestring, record_date) &&
                strcmp(record_date, date->valuestring) == 0 &&
                cJSON_IsString(record_id) && record_id->valuestring && record_id->valuestring[0] != '\0' &&
                cJSON_IsArray(files) && cJSON_GetArraySize(files) == 4;
        if (!valid) {
            break;
        }
        for (uint32_t previous = 0; previous < i; ++previous) {
            if (strcmp(records[previous].name, name->valuestring) == 0) {
                valid = false;
                break;
            }
        }
        strlcpy(records[i].name, name->valuestring, sizeof(records[i].name));
        bool seen[4] = {false, false, false, false};
        for (int file_number = 0; valid && file_number < 4; ++file_number) {
            const cJSON *file = cJSON_GetArrayItem(files, file_number);
            const cJSON *file_name = cJSON_GetObjectItemCaseSensitive(file, "name");
            uint64_t size = 0;
            int index = cJSON_IsString(file_name) ? record_file_index(file_name->valuestring) : -1;
            valid = json_fields_are_exact(file, file_fields,
                                          sizeof(file_fields) / sizeof(file_fields[0])) &&
                    index >= 0 && !seen[index] && copy_json_u64(file, "size", &size);
            if (valid) {
                seen[index] = true;
                records[i].file_sizes[index] = size;
                if (UINT64_MAX - records[i].total_bytes < size ||
                    UINT64_MAX - calculated_total < size) {
                    valid = false;
                } else {
                    records[i].total_bytes += size;
                    calculated_total += size;
                }
            }
        }
    }
    cJSON_Delete(manifest);
    if (!valid || calculated_total != declared_total) {
        free(records);
        set_reason(reason, reason_len, "invalid_export_manifest");
        return ESP_ERR_INVALID_CRC;
    }
    *out_records = records;
    *out_count = record_count;
    return ESP_OK;
}

static esp_err_t write_transaction_state(const transaction_info_t *info, const char *state_path)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) {
        return ESP_ERR_NO_MEM;
    }
    cJSON_AddNumberToObject(root, "schema_version", 1);
    cJSON_AddStringToObject(root, "export_id", info->export_id);
    cJSON_AddStringToObject(root, "state", info->state);
    cJSON_AddNumberToObject(root, "record_count", info->record_count);
    cJSON_AddNumberToObject(root, "deleted_count", info->deleted_count);
    cJSON_AddStringToObject(root, "manifest_sha256", info->manifest_sha256);
    cJSON_AddStringToObject(root, "last_error", info->last_error);
    cJSON_AddNumberToObject(root, "updated_at_utc_ms", (double)utc_now_ms());
    char *text = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!text) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t result = atomic_write(state_path, text, strlen(text));
    free(text);
    return result;
}

static esp_err_t check_record_directory(const record_info_t *record, bool allow_missing,
                                        bool require_absent, char *reason, size_t reason_len)
{
    char directory[128];
    if (snprintf(directory, sizeof(directory), DAY_SD_MOUNT_POINT "/%s", record->name) >=
        (int)sizeof(directory)) {
        set_reason(reason, reason_len, "record_path_too_long");
        return ESP_ERR_INVALID_SIZE;
    }
    struct stat st;
    if (stat(directory, &st) != 0) {
        if (errno == ENOENT && (allow_missing || require_absent)) {
            return ESP_OK;
        }
        set_reason(reason, reason_len, "manifest_record_missing");
        return ESP_FAIL;
    }
    if (require_absent || !S_ISDIR(st.st_mode)) {
        set_reason(reason, reason_len, require_absent ? "deleted_record_reappeared" : "record_not_directory");
        return ESP_FAIL;
    }
    DIR *dir = opendir(directory);
    if (!dir) {
        set_reason(reason, reason_len, "cannot_open_record_directory");
        return ESP_FAIL;
    }
    bool valid = true;
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) {
            continue;
        }
        if (record_file_index(entry->d_name) < 0) {
            valid = false;
            break;
        }
    }
    closedir(dir);
    if (!valid) {
        set_reason(reason, reason_len, "record_contains_unlisted_file");
        return ESP_FAIL;
    }
    for (size_t i = 0; i < 4; ++i) {
        char path[160];
        if (snprintf(path, sizeof(path), "%s/%s", directory, s_record_files[i]) >= (int)sizeof(path)) {
            set_reason(reason, reason_len, "record_path_too_long");
            return ESP_ERR_INVALID_SIZE;
        }
        struct stat file_stat;
        if (stat(path, &file_stat) != 0) {
            if (allow_missing && errno == ENOENT) {
                continue;
            }
            set_reason(reason, reason_len, "manifest_file_missing_or_invalid");
            return ESP_FAIL;
        }
        if (!S_ISREG(file_stat.st_mode)) {
            set_reason(reason, reason_len, "manifest_file_missing_or_invalid");
            return ESP_FAIL;
        }
        if ((uint64_t)file_stat.st_size != record->file_sizes[i]) {
            set_reason(reason, reason_len, "manifest_file_size_changed");
            return ESP_FAIL;
        }
    }
    return ESP_OK;
}

static esp_err_t preflight_records(const record_info_t *records, size_t count,
                                   uint32_t deleted_count, bool resuming,
                                   char *reason, size_t reason_len)
{
    if (deleted_count > count) {
        set_reason(reason, reason_len, "invalid_deleted_count");
        return ESP_ERR_INVALID_CRC;
    }
    for (size_t i = 0; i < count; ++i) {
        bool require_absent = resuming && i < deleted_count;
        bool allow_missing = resuming && i == deleted_count;
        esp_err_t result = check_record_directory(&records[i], allow_missing, require_absent,
                                                  reason, reason_len);
        if (result != ESP_OK) {
            return result;
        }
    }
    return ESP_OK;
}

static esp_err_t delete_one_record(const record_info_t *record, char *reason, size_t reason_len)
{
    char directory[128];
    if (snprintf(directory, sizeof(directory), DAY_SD_MOUNT_POINT "/%s", record->name) >=
        (int)sizeof(directory)) {
        set_reason(reason, reason_len, "record_path_too_long");
        return ESP_ERR_INVALID_SIZE;
    }
    struct stat directory_stat;
    if (stat(directory, &directory_stat) != 0) {
        if (errno == ENOENT) {
            return ESP_OK;
        }
        set_reason(reason, reason_len, "cannot_stat_record_directory");
        return ESP_FAIL;
    }
    if (!S_ISDIR(directory_stat.st_mode)) {
        set_reason(reason, reason_len, "record_not_directory");
        return ESP_FAIL;
    }
    for (size_t i = 0; i < 4; ++i) {
        char path[160];
        if (snprintf(path, sizeof(path), "%s/%s", directory, s_record_files[i]) >= (int)sizeof(path)) {
            set_reason(reason, reason_len, "record_path_too_long");
            return ESP_ERR_INVALID_SIZE;
        }
        struct stat st;
        if (stat(path, &st) != 0) {
            if (errno == ENOENT) {
                continue;
            }
            set_reason(reason, reason_len, "cannot_stat_manifest_file");
            return ESP_FAIL;
        }
        if (!S_ISREG(st.st_mode) || (uint64_t)st.st_size != record->file_sizes[i]) {
            set_reason(reason, reason_len, "manifest_file_changed_during_commit");
            return ESP_FAIL;
        }
        if (unlink(path) != 0) {
            set_reason(reason, reason_len, "cannot_delete_manifest_file");
            return ESP_FAIL;
        }
    }
    if (rmdir(directory) != 0 && errno != ENOENT) {
        set_reason(reason, reason_len, "cannot_delete_record_directory");
        return ESP_FAIL;
    }
    return ESP_OK;
}

esp_err_t day_export_list_record_dates(const day_usb_pagination_args_t *args,
                                       char *response, size_t response_len,
                                       char *reason, size_t reason_len)
{
    if (!args || !response || response_len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    date_summary_t *items = NULL;
    size_t count = 0;
    esp_err_t result = collect_dates(&items, &count);
    if (result != ESP_OK) {
        set_reason(reason, reason_len, "storage_not_mounted");
        return result;
    }
    size_t cursor = args->cursor < count ? args->cursor : count;
    size_t page_count = args->limit;
    if (page_count > 15) {
        page_count = 15;
    }
    if (page_count > count - cursor) {
        page_count = count - cursor;
    }
    cJSON *root = cJSON_CreateObject();
    if (!root) {
        free(items);
        return ESP_ERR_NO_MEM;
    }
    cJSON *array = cJSON_AddArrayToObject(root, "items");
    if (!array) {
        cJSON_Delete(root);
        free(items);
        return ESP_ERR_NO_MEM;
    }
    for (size_t i = 0; i < page_count; ++i) {
        const date_summary_t *item = &items[cursor + i];
        cJSON *entry = cJSON_CreateObject();
        cJSON_AddStringToObject(entry, "date", item->date);
        cJSON_AddNumberToObject(entry, "record_count", item->record_count);
        cJSON_AddNumberToObject(entry, "total_bytes", (double)item->total_bytes);
        cJSON_AddItemToArray(array, entry);
    }
    if (cursor + page_count < count) {
        cJSON_AddNumberToObject(root, "next_cursor", cursor + page_count);
    } else {
        cJSON_AddNullToObject(root, "next_cursor");
    }
    result = json_to_response(root, response, response_len);
    cJSON_Delete(root);
    free(items);
    if (result != ESP_OK) {
        set_reason(reason, reason_len, "response_too_large");
    }
    return result;
}

static esp_err_t scan_transactions(const day_usb_begin_export_args_t *args,
                                   transaction_info_t *idempotent, size_t *active_count,
                                   bool *date_prepared)
{
    *active_count = 0;
    *date_prepared = false;
    DIR *dir = opendir(DAY_EXPORT_DIR);
    if (!dir) {
        return errno == ENOENT ? ESP_OK : ESP_FAIL;
    }
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        char export_id[DAY_USB_EXPORT_ID_MAX];
        if (!export_id_from_filename(entry->d_name, export_id)) {
            continue;
        }
        transaction_info_t info;
        if (load_transaction(export_id, &info) != ESP_OK) {
            continue;
        }
        bool active = strcmp(info.state, "prepared") == 0 || strcmp(info.state, "committing") == 0;
        if (active) {
            (*active_count)++;
        }
        if (strcmp(info.client_request_id, args->client_request_id) == 0) {
            *idempotent = info;
            closedir(dir);
            return ESP_OK;
        }
        if (active && strcmp(info.date, args->date) == 0) {
            *date_prepared = true;
        }
    }
    closedir(dir);
    return ESP_OK;
}

static esp_err_t create_manifest_json(const day_usb_begin_export_args_t *args,
                                      const char *export_id, const record_info_t *records,
                                      size_t record_count, uint64_t total_bytes,
                                      char **out_text)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) {
        return ESP_ERR_NO_MEM;
    }
    cJSON_AddNumberToObject(root, "schema_version", 2);
    cJSON_AddStringToObject(root, "export_id", export_id);
    cJSON_AddStringToObject(root, "client_request_id", args->client_request_id);
    cJSON_AddStringToObject(root, "device_id", day_device_id());
    cJSON_AddStringToObject(root, "date", args->date);
    cJSON_AddNumberToObject(root, "created_at_utc_ms", (double)utc_now_ms());
    cJSON_AddStringToObject(root, "state", "prepared");
    cJSON_AddNumberToObject(root, "record_count", record_count);
    cJSON_AddNumberToObject(root, "total_bytes", (double)total_bytes);
    cJSON *record_array = cJSON_AddArrayToObject(root, "records");
    for (size_t i = 0; i < record_count; ++i) {
        cJSON *record = cJSON_CreateObject();
        cJSON_AddStringToObject(record, "name", records[i].name);
        cJSON_AddStringToObject(record, "record_id", records[i].record_id);
        cJSON *files = cJSON_AddArrayToObject(record, "files");
        for (size_t file_index = 0; file_index < 4; ++file_index) {
            cJSON *file = cJSON_CreateObject();
            cJSON_AddStringToObject(file, "name", s_record_files[file_index]);
            cJSON_AddNumberToObject(file, "size", (double)records[i].file_sizes[file_index]);
            cJSON_AddItemToArray(files, file);
        }
        cJSON_AddItemToArray(record_array, record);
    }
    *out_text = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return *out_text ? ESP_OK : ESP_ERR_NO_MEM;
}

static esp_err_t write_initial_state(const transaction_info_t *info, const char *state_path)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) {
        return ESP_ERR_NO_MEM;
    }
    cJSON_AddNumberToObject(root, "schema_version", 1);
    cJSON_AddStringToObject(root, "export_id", info->export_id);
    cJSON_AddStringToObject(root, "state", "prepared");
    cJSON_AddNumberToObject(root, "record_count", info->record_count);
    cJSON_AddNumberToObject(root, "deleted_count", 0);
    cJSON_AddStringToObject(root, "manifest_sha256", info->manifest_sha256);
    cJSON_AddStringToObject(root, "last_error", "");
    cJSON_AddNumberToObject(root, "updated_at_utc_ms", (double)utc_now_ms());
    char *text = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (!text) {
        return ESP_ERR_NO_MEM;
    }
    esp_err_t result = atomic_write(state_path, text, strlen(text));
    free(text);
    return result;
}

esp_err_t day_export_begin(const day_usb_begin_export_args_t *args,
                           char *response, size_t response_len,
                           char *reason, size_t reason_len)
{
    if (!args || !response || response_len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    day_storage_status_t storage = day_storage_get_status();
    if (!storage.mounted) {
        set_reason(reason, reason_len, "storage_not_mounted");
        return ESP_ERR_INVALID_STATE;
    }
    if (ensure_export_dir() != ESP_OK) {
        set_reason(reason, reason_len, "cannot_create_export_directory");
        return ESP_FAIL;
    }
    transaction_info_t idempotent = {0};
    size_t active_count = 0;
    bool date_prepared = false;
    esp_err_t result = scan_transactions(args, &idempotent, &active_count, &date_prepared);
    if (result != ESP_OK) {
        set_reason(reason, reason_len, "cannot_scan_export_transactions");
        return result;
    }
    if (idempotent.export_id[0]) {
        if (strcmp(idempotent.date, args->date) != 0) {
            set_reason(reason, reason_len, "client_request_id_date_mismatch");
            return ESP_ERR_INVALID_ARG;
        }
        return transaction_response(&idempotent, response, response_len);
    }
    if (date_prepared) {
        set_reason(reason, reason_len, "date_already_prepared");
        return ESP_ERR_INVALID_STATE;
    }
    if (active_count >= DAY_EXPORT_MAX_ACTIVE) {
        set_reason(reason, reason_len, "too_many_active_exports");
        return ESP_ERR_NO_MEM;
    }
    record_info_t *records = NULL;
    size_t record_count = 0;
    uint64_t total_bytes = 0;
    result = collect_records(args->date, &records, &record_count, &total_bytes);
    if (result != ESP_OK) {
        set_reason(reason, reason_len, "cannot_scan_records");
        return result;
    }
    if (record_count == 0) {
        free(records);
        set_reason(reason, reason_len, "no_complete_records_for_date");
        return ESP_ERR_INVALID_ARG;
    }
    char export_id[DAY_USB_EXPORT_ID_MAX];
    char manifest_path[128];
    char state_path[128];
    do {
        snprintf(export_id, sizeof(export_id), "exp-%s-%08" PRIx32 "%08" PRIx32,
                 day_device_id(), esp_random(), esp_random());
        if (!transaction_paths(export_id, manifest_path, state_path)) {
            free(records);
            return ESP_FAIL;
        }
    } while (access(manifest_path, F_OK) == 0 || access(state_path, F_OK) == 0);

    char *manifest_text = NULL;
    result = create_manifest_json(args, export_id, records, record_count, total_bytes, &manifest_text);
    free(records);
    if (result != ESP_OK) {
        set_reason(reason, reason_len, "cannot_build_manifest");
        return result;
    }
    transaction_info_t info = {0};
    strlcpy(info.export_id, export_id, sizeof(info.export_id));
    strlcpy(info.client_request_id, args->client_request_id, sizeof(info.client_request_id));
    strlcpy(info.date, args->date, sizeof(info.date));
    strlcpy(info.state, "prepared", sizeof(info.state));
    info.record_count = (uint32_t)record_count;
    info.total_bytes = total_bytes;
    result = sha256_text(manifest_text, strlen(manifest_text), info.manifest_sha256);
    if (result == ESP_OK) {
        result = atomic_write(manifest_path, manifest_text, strlen(manifest_text));
    }
    free(manifest_text);
    if (result == ESP_OK) {
        result = write_initial_state(&info, state_path);
    }
    if (result != ESP_OK) {
        (void)unlink(state_path);
        (void)unlink(manifest_path);
        set_reason(reason, reason_len, "cannot_persist_export_transaction");
        return result;
    }
    ESP_LOGI(TAG, "prepared export %s date=%s records=%lu", export_id, args->date,
             (unsigned long)record_count);
    return transaction_response(&info, response, response_len);
}

esp_err_t day_export_get_status(const day_usb_get_export_status_args_t *args,
                                char *response, size_t response_len,
                                char *reason, size_t reason_len)
{
    if (!args || !response || response_len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!day_storage_get_status().mounted) {
        set_reason(reason, reason_len, "storage_not_mounted");
        return ESP_ERR_INVALID_STATE;
    }
    if (args->has_export_id) {
        transaction_info_t info;
        esp_err_t result = load_transaction(args->export_id, &info);
        if (result != ESP_OK) {
            set_reason(reason, reason_len, result == ESP_ERR_NOT_FOUND ? "export_not_found" : "invalid_export_metadata");
            return result;
        }
        return transaction_response(&info, response, response_len);
    }
    transaction_info_t *items = NULL;
    size_t count = 0;
    DIR *dir = opendir(DAY_EXPORT_DIR);
    if (dir) {
        struct dirent *entry;
        while ((entry = readdir(dir)) != NULL) {
            char export_id[DAY_USB_EXPORT_ID_MAX];
            transaction_info_t info;
            if (!export_id_from_filename(entry->d_name, export_id) ||
                load_transaction(export_id, &info) != ESP_OK ||
                (strcmp(info.state, "prepared") != 0 && strcmp(info.state, "committing") != 0)) {
                continue;
            }
            transaction_info_t *next = realloc(items, (count + 1) * sizeof(*items));
            if (!next) {
                free(items);
                closedir(dir);
                return ESP_ERR_NO_MEM;
            }
            items = next;
            items[count++] = info;
        }
        closedir(dir);
    }
    qsort(items, count, sizeof(*items), compare_transactions);
    size_t cursor = args->cursor < count ? args->cursor : count;
    size_t page_count = args->limit > 8 ? 8 : args->limit;
    if (page_count > count - cursor) {
        page_count = count - cursor;
    }
    cJSON *root = cJSON_CreateObject();
    if (!root) {
        free(items);
        return ESP_ERR_NO_MEM;
    }
    cJSON *array = cJSON_AddArrayToObject(root, "items");
    if (!array) {
        cJSON_Delete(root);
        free(items);
        return ESP_ERR_NO_MEM;
    }
    for (size_t i = 0; i < page_count; ++i) {
        transaction_info_t *info = &items[cursor + i];
        cJSON *entry = cJSON_CreateObject();
        cJSON_AddStringToObject(entry, "export_id", info->export_id);
        cJSON_AddStringToObject(entry, "date", info->date);
        cJSON_AddStringToObject(entry, "state", info->state);
        cJSON_AddNumberToObject(entry, "record_count", info->record_count);
        cJSON_AddNumberToObject(entry, "deleted_count", info->deleted_count);
        cJSON_AddItemToArray(array, entry);
    }
    if (cursor + page_count < count) {
        cJSON_AddNumberToObject(root, "next_cursor", cursor + page_count);
    } else {
        cJSON_AddNullToObject(root, "next_cursor");
    }
    esp_err_t result = json_to_response(root, response, response_len);
    cJSON_Delete(root);
    free(items);
    return result;
}

esp_err_t day_export_validate_prepared(const char export_ids[][DAY_USB_EXPORT_ID_MAX], size_t count,
                                       char *reason, size_t reason_len)
{
    for (size_t i = 0; i < count; ++i) {
        transaction_info_t info;
        esp_err_t result = load_transaction(export_ids[i], &info);
        if (result != ESP_OK || strcmp(info.state, "prepared") != 0) {
            set_reason(reason, reason_len, result == ESP_ERR_NOT_FOUND ? "export_not_found" : "export_not_prepared");
            return ESP_ERR_INVALID_ARG;
        }
    }
    return ESP_OK;
}

size_t day_export_active_count(void)
{
    size_t count = 0;
    DIR *dir = opendir(DAY_EXPORT_DIR);
    if (!dir) {
        return 0;
    }
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        char export_id[DAY_USB_EXPORT_ID_MAX];
        transaction_info_t info;
        if (export_id_from_filename(entry->d_name, export_id) &&
            load_transaction(export_id, &info) == ESP_OK &&
            (strcmp(info.state, "prepared") == 0 || strcmp(info.state, "committing") == 0)) {
            ++count;
        }
    }
    closedir(dir);
    return count;
}

esp_err_t day_export_commit_delete(const day_usb_commit_export_args_t *args,
                                   char *response, size_t response_len,
                                   char *reason, size_t reason_len)
{
    if (!args || !response || response_len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    day_storage_status_t storage = day_storage_get_status();
    if (!storage.mounted) {
        set_reason(reason, reason_len, "storage_not_mounted");
        return ESP_ERR_INVALID_STATE;
    }
    char manifest_path[128];
    char state_path[128];
    if (!transaction_paths(args->export_id, manifest_path, state_path)) {
        set_reason(reason, reason_len, "invalid_export_id");
        return ESP_ERR_INVALID_ARG;
    }
    transaction_info_t info;
    esp_err_t result = load_transaction(args->export_id, &info);
    if (result != ESP_OK) {
        set_reason(reason, reason_len,
                   result == ESP_ERR_NOT_FOUND ? "export_not_found" : "invalid_export_metadata");
        return result;
    }
    if (strcmp(info.manifest_sha256, args->manifest_sha256) != 0) {
        set_reason(reason, reason_len, "manifest_sha256_mismatch");
        return ESP_ERR_INVALID_CRC;
    }
    if (info.record_count != args->confirm_record_count) {
        set_reason(reason, reason_len, "record_count_mismatch");
        return ESP_ERR_INVALID_ARG;
    }
    record_info_t *records = NULL;
    size_t record_count = 0;
    result = parse_verified_manifest(args->export_id, args->manifest_sha256,
                                     args->confirm_record_count, &records, &record_count,
                                     reason, reason_len);
    if (result != ESP_OK) {
        return result;
    }
    if (strcmp(info.state, "committed") == 0) {
        free(records);
        return transaction_response(&info, response, response_len);
    }
    bool resuming = strcmp(info.state, "committing") == 0;
    if (!resuming && strcmp(info.state, "prepared") != 0) {
        free(records);
        set_reason(reason, reason_len,
                   strcmp(info.state, "aborted") == 0 ? "export_aborted" : "invalid_export_state");
        return ESP_ERR_INVALID_STATE;
    }
    result = preflight_records(records, record_count, info.deleted_count, resuming,
                               reason, reason_len);
    if (result != ESP_OK) {
        if (resuming) {
            strlcpy(info.last_error, reason && reason[0] ? reason : "commit_preflight_failed",
                    sizeof(info.last_error));
            (void)write_transaction_state(&info, state_path);
        }
        free(records);
        return result;
    }
    if (!resuming) {
        strlcpy(info.state, "committing", sizeof(info.state));
        info.deleted_count = 0;
        info.last_error[0] = '\0';
        result = write_transaction_state(&info, state_path);
        if (result != ESP_OK) {
            free(records);
            set_reason(reason, reason_len, "cannot_persist_committing_state");
            return result;
        }
    }
    for (size_t i = info.deleted_count; i < record_count; ++i) {
        result = delete_one_record(&records[i], reason, reason_len);
        if (result != ESP_OK) {
            strlcpy(info.last_error, reason && reason[0] ? reason : "record_delete_failed",
                    sizeof(info.last_error));
            (void)write_transaction_state(&info, state_path);
            free(records);
            return result;
        }
        info.deleted_count = (uint32_t)(i + 1);
        info.last_error[0] = '\0';
        result = write_transaction_state(&info, state_path);
        if (result != ESP_OK) {
            free(records);
            set_reason(reason, reason_len, "cannot_persist_delete_progress");
            return result;
        }
    }
    free(records);
    strlcpy(info.state, "committed", sizeof(info.state));
    info.last_error[0] = '\0';
    result = write_transaction_state(&info, state_path);
    if (result != ESP_OK) {
        set_reason(reason, reason_len, "cannot_persist_committed_state");
        return result;
    }
    ESP_LOGI(TAG, "committed export %s records=%lu", info.export_id,
             (unsigned long)info.record_count);
    return transaction_response(&info, response, response_len);
}

esp_err_t day_export_abort(const day_usb_export_id_args_t *args,
                           char *response, size_t response_len,
                           char *reason, size_t reason_len)
{
    if (!args || !response || response_len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!day_storage_get_status().mounted) {
        set_reason(reason, reason_len, "storage_not_mounted");
        return ESP_ERR_INVALID_STATE;
    }
    char manifest_path[128];
    char state_path[128];
    if (!transaction_paths(args->export_id, manifest_path, state_path)) {
        set_reason(reason, reason_len, "invalid_export_id");
        return ESP_ERR_INVALID_ARG;
    }
    transaction_info_t info;
    esp_err_t result = load_transaction(args->export_id, &info);
    if (result != ESP_OK) {
        set_reason(reason, reason_len,
                   result == ESP_ERR_NOT_FOUND ? "export_not_found" : "invalid_export_metadata");
        return result;
    }
    if (strcmp(info.state, "committing") == 0) {
        set_reason(reason, reason_len, "commit_recovery_required");
        return ESP_ERR_INVALID_STATE;
    }
    if (strcmp(info.state, "prepared") == 0) {
        strlcpy(info.state, "aborted", sizeof(info.state));
        info.last_error[0] = '\0';
        result = write_transaction_state(&info, state_path);
        if (result != ESP_OK) {
            set_reason(reason, reason_len, "cannot_persist_aborted_state");
            return result;
        }
    } else if (strcmp(info.state, "aborted") != 0 && strcmp(info.state, "committed") != 0) {
        set_reason(reason, reason_len, "invalid_export_state");
        return ESP_ERR_INVALID_STATE;
    }
    return transaction_response(&info, response, response_len);
}
