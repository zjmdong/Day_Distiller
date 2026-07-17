#include "storage_service.h"

#include <dirent.h>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "day_pins.h"
#include "driver/sdspi_host.h"
#include "driver/spi_common.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_vfs_fat.h"
#include "nvs.h"
#include "sdmmc_cmd.h"

#define DAY_RECORDING_DIR DAY_SD_MOUNT_POINT "/.recording"
#define DAY_RECORD_COUNTER_NS "day_record"
#define DAY_RECORD_COUNTER_KEY "next_id"
#define DAY_RECORD_SEQUENCE_MODULUS 10000U
#define DAY_RECORD_COUNTER_MAX 9007199254740000ULL

static const char *TAG = "day_storage";

static sdmmc_card_t *s_card;
static bool s_bus_initialized;
static sdmmc_host_t s_host;
static bool s_partial_records_scanned;
static day_storage_status_t s_status = {
    .last_error = ESP_ERR_INVALID_STATE,
};

esp_err_t day_storage_init(void)
{
    if (s_status.mounted) {
        return ESP_OK;
    }

    s_host = (sdmmc_host_t)SDSPI_HOST_DEFAULT();
    s_host.max_freq_khz = 20000;
    s_host.unaligned_multi_block_rw_max_chunk_size = 8;

    spi_bus_config_t bus_cfg = {
        .mosi_io_num = DAY_PIN_TF_MOSI,
        .miso_io_num = DAY_PIN_TF_MISO,
        .sclk_io_num = DAY_PIN_TF_SCK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 32 * 1024,
    };

    esp_err_t ret = spi_bus_initialize(s_host.slot, &bus_cfg, SDSPI_DEFAULT_DMA);
    if (ret == ESP_OK) {
        s_bus_initialized = true;
    } else if (ret != ESP_ERR_INVALID_STATE) {
        s_status.last_error = ret;
        return ret;
    }

    sdspi_device_config_t slot_config = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot_config.gpio_cs = DAY_PIN_TF_CS;
    slot_config.host_id = s_host.slot;

    esp_vfs_fat_sdmmc_mount_config_t mount_config = {
        .format_if_mount_failed = false,
        .max_files = 8,
        .allocation_unit_size = 16 * 1024,
    };

    ret = esp_vfs_fat_sdspi_mount(DAY_SD_MOUNT_POINT, &s_host, &slot_config, &mount_config, &s_card);
    s_status.mounted = ret == ESP_OK;
    s_status.available = ret == ESP_OK;
    s_status.last_error = ret;
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "TF card mounted");
        day_storage_refresh_status(&s_status);
        struct stat recording_dir;
        if (stat(DAY_RECORDING_DIR, &recording_dir) == 0 && S_ISDIR(recording_dir.st_mode) &&
            !s_partial_records_scanned) {
            DIR *dir = opendir(DAY_RECORDING_DIR);
            if (dir) {
                struct dirent *entry;
                while ((entry = readdir(dir)) != NULL) {
                    if (entry->d_name[0] != '.') {
                        ESP_LOGW(TAG, "preserving incomplete recording for diagnostics: %s",
                                 entry->d_name);
                    }
                }
                closedir(dir);
            }
            s_partial_records_scanned = true;
        }
    } else {
        ESP_LOGW(TAG, "TF card mount failed: %s", esp_err_to_name(ret));
    }
    return ret;
}

esp_err_t day_storage_refresh_status(day_storage_status_t *status)
{
    if (!status) {
        return ESP_ERR_INVALID_ARG;
    }
    *status = s_status;
    if (!s_status.mounted) {
        return s_status.last_error;
    }
    esp_err_t ret = esp_vfs_fat_info(DAY_SD_MOUNT_POINT, &status->total_bytes, &status->free_bytes);
    status->mounted = ret == ESP_OK;
    status->available = ret == ESP_OK;
    status->last_error = ret;
    s_status = *status;
    return ret;
}

day_storage_status_t day_storage_get_status(void)
{
    return s_status;
}

esp_err_t day_storage_require_free_bytes(uint64_t required_bytes)
{
    day_storage_status_t status;
    esp_err_t result = day_storage_refresh_status(&status);
    if (result != ESP_OK) {
        return result;
    }
    if (status.free_bytes < required_bytes) {
        ESP_LOGE(TAG, "insufficient TF space: free=%" PRIu64 " required=%" PRIu64,
                 status.free_bytes, required_bytes);
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

static esp_err_t allocate_record_counter(uint64_t *counter)
{
    nvs_handle_t nvs;
    esp_err_t result = nvs_open(DAY_RECORD_COUNTER_NS, NVS_READWRITE, &nvs);
    if (result != ESP_OK) {
        return result;
    }
    uint64_t current = 0;
    result = nvs_get_u64(nvs, DAY_RECORD_COUNTER_KEY, &current);
    if (result == ESP_ERR_NVS_NOT_FOUND) {
        current = ((uint64_t)esp_random() << 20) | (esp_random() & 0x000fffffU);
        if (current == 0 || current >= DAY_RECORD_COUNTER_MAX) {
            current = 1;
        }
        result = ESP_OK;
    }
    if (result == ESP_OK && current >= DAY_RECORD_COUNTER_MAX) {
        result = ESP_ERR_INVALID_STATE;
    }
    if (result == ESP_OK) {
        result = nvs_set_u64(nvs, DAY_RECORD_COUNTER_KEY, current + 1);
    }
    if (result == ESP_OK) {
        result = nvs_commit(nvs);
    }
    nvs_close(nvs);
    if (result == ESP_OK) {
        *counter = current;
    }
    return result;
}

static esp_err_t ensure_recording_directory(void)
{
    struct stat st;
    if (stat(DAY_RECORDING_DIR, &st) == 0) {
        return S_ISDIR(st.st_mode) ? ESP_OK : ESP_FAIL;
    }
    if (errno != ENOENT || mkdir(DAY_RECORDING_DIR, 0775) != 0) {
        return ESP_FAIL;
    }
    return ESP_OK;
}

static esp_err_t path_exists(const char *path, bool *exists)
{
    struct stat st;
    if (stat(path, &st) == 0) {
        *exists = true;
        return ESP_OK;
    }
    if (errno == ENOENT) {
        *exists = false;
        return ESP_OK;
    }
    return ESP_FAIL;
}

static esp_err_t set_record_file_paths(day_record_paths_t *paths, const char *directory)
{
    int video = snprintf(paths->video_path, sizeof(paths->video_path), "%s/video.avi", directory);
    int audio = snprintf(paths->audio_path, sizeof(paths->audio_path), "%s/audio.wav", directory);
    int imu = snprintf(paths->imu_path, sizeof(paths->imu_path), "%s/imu.json", directory);
    int meta = snprintf(paths->meta_path, sizeof(paths->meta_path), "%s/meta.json", directory);
    if (video < 0 || video >= (int)sizeof(paths->video_path) ||
        audio < 0 || audio >= (int)sizeof(paths->audio_path) ||
        imu < 0 || imu >= (int)sizeof(paths->imu_path) ||
        meta < 0 || meta >= (int)sizeof(paths->meta_path)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

esp_err_t day_storage_make_record_paths(day_record_paths_t *paths, time_t record_time)
{
    if (!paths) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_status.mounted) {
        return ESP_ERR_INVALID_STATE;
    }
    memset(paths, 0, sizeof(*paths));
    esp_err_t result = ensure_recording_directory();
    if (result != ESP_OK) {
        return result;
    }
    result = allocate_record_counter(&paths->record_counter);
    if (result != ESP_OK) {
        return result;
    }

    struct tm tm;
    localtime_r(&record_time, &tm);
    if (tm.tm_year < 120 || tm.tm_year > 199) {
        time_t fallback = 1625097600 + (time_t)(paths->record_counter % 31536000ULL);
        localtime_r(&fallback, &tm);
        paths->record_time = fallback;
    } else {
        paths->record_time = record_time;
    }

    bool found = false;
    uint32_t initial_sequence = (uint32_t)(paths->record_counter % DAY_RECORD_SEQUENCE_MODULUS);
    for (uint32_t attempt = 0; attempt < DAY_RECORD_SEQUENCE_MODULUS; ++attempt) {
        paths->sequence = (initial_sequence + attempt) % DAY_RECORD_SEQUENCE_MODULUS;
        int name_len = snprintf(paths->record_name, sizeof(paths->record_name),
                                "REC_%04lu_%02d%02d%02d_%02d%02d%02d",
                                (unsigned long)paths->sequence,
                                (tm.tm_year + 1900) % 100, tm.tm_mon + 1, tm.tm_mday,
                                tm.tm_hour, tm.tm_min, tm.tm_sec);
        int final_len = snprintf(paths->final_dir_path, sizeof(paths->final_dir_path),
                                 DAY_SD_MOUNT_POINT "/%s", paths->record_name);
        int partial_len = snprintf(paths->dir_path, sizeof(paths->dir_path),
                                   DAY_RECORDING_DIR "/%s.partial", paths->record_name);
        if (name_len != 22 || final_len < 0 || final_len >= (int)sizeof(paths->final_dir_path) ||
            partial_len < 0 || partial_len >= (int)sizeof(paths->dir_path)) {
            return ESP_ERR_INVALID_SIZE;
        }
        bool final_exists = false;
        bool partial_exists = false;
        result = path_exists(paths->final_dir_path, &final_exists);
        if (result == ESP_OK) {
            result = path_exists(paths->dir_path, &partial_exists);
        }
        if (result != ESP_OK) {
            return result;
        }
        if (!final_exists && !partial_exists) {
            found = true;
            break;
        }
    }
    if (!found) {
        ESP_LOGE(TAG, "all four-digit record sequences collide for selected timestamp");
        return ESP_ERR_NOT_FOUND;
    }
    if (mkdir(paths->dir_path, 0775) != 0) {
        ESP_LOGW(TAG, "partial record mkdir failed for %s: errno=%d", paths->dir_path, errno);
        return ESP_FAIL;
    }
    result = set_record_file_paths(paths, paths->dir_path);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "record file path construction failed; preserving %s", paths->dir_path);
    }
    return result;
}

esp_err_t day_storage_finalize_record(day_record_paths_t *paths)
{
    if (!paths || !s_status.mounted || paths->dir_path[0] == '\0' ||
        paths->final_dir_path[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    const char *files[] = {paths->meta_path, paths->video_path, paths->audio_path, paths->imu_path};
    for (size_t i = 0; i < sizeof(files) / sizeof(files[0]); ++i) {
        struct stat st;
        if (stat(files[i], &st) != 0 || !S_ISREG(st.st_mode)) {
            ESP_LOGE(TAG, "cannot finalize record with missing file: %s", files[i]);
            return ESP_ERR_INVALID_STATE;
        }
    }
    bool final_exists = false;
    esp_err_t result = path_exists(paths->final_dir_path, &final_exists);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "cannot verify final record path absence: errno=%d", errno);
        return result;
    }
    if (final_exists) {
        ESP_LOGE(TAG, "refusing to overwrite existing record: %s", paths->final_dir_path);
        return ESP_ERR_INVALID_STATE;
    }
    if (rename(paths->dir_path, paths->final_dir_path) != 0) {
        ESP_LOGE(TAG, "record directory rename failed: errno=%d", errno);
        return ESP_FAIL;
    }
    strlcpy(paths->dir_path, paths->final_dir_path, sizeof(paths->dir_path));
    result = set_record_file_paths(paths, paths->dir_path);
    if (result == ESP_OK) {
        ESP_LOGI(TAG, "record atomically completed: %s", paths->record_name);
    }
    return result;
}

esp_err_t day_storage_files_json(char *buffer, size_t len)
{
    if (!buffer || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    size_t used = 0;
    int written = snprintf(buffer, len, "{\"mounted\":%s,\"records\":[", s_status.mounted ? "true" : "false");
    if (written < 0 || (size_t)written >= len) {
        return ESP_ERR_NO_MEM;
    }
    used = (size_t)written;
    DIR *dir = s_status.mounted ? opendir(DAY_SD_MOUNT_POINT) : NULL;
    if (dir) {
        bool first = true;
        struct dirent *ent;
        while ((ent = readdir(dir)) != NULL) {
            if (strncmp(ent->d_name, "REC_", 4) != 0) {
                continue;
            }
            written = snprintf(buffer + used, len - used, "%s\"%s\"", first ? "" : ",", ent->d_name);
            if (written < 0 || (size_t)written >= len - used) {
                closedir(dir);
                return ESP_ERR_NO_MEM;
            }
            used += (size_t)written;
            first = false;
        }
        closedir(dir);
    }
    written = snprintf(buffer + used, len - used, "]}");
    if (written < 0 || (size_t)written >= len - used) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void day_storage_deinit(void)
{
    if (s_status.mounted) {
        esp_vfs_fat_sdcard_unmount(DAY_SD_MOUNT_POINT, s_card);
        s_status.mounted = false;
        s_status.available = false;
        s_card = NULL;
        s_partial_records_scanned = false;
    }
    if (s_bus_initialized) {
        spi_bus_free(s_host.slot);
        s_bus_initialized = false;
    }
}
