#include "storage_service.h"

#include <dirent.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include "day_pins.h"
#include "driver/sdspi_host.h"
#include "driver/spi_common.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "sdmmc_cmd.h"

static const char *TAG = "day_storage";

static sdmmc_card_t *s_card;
static bool s_bus_initialized;
static sdmmc_host_t s_host;
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
    s_status.last_error = ret;
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "TF card mounted");
        day_storage_refresh_status(&s_status);
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
    status->last_error = ret;
    s_status = *status;
    return ret;
}

day_storage_status_t day_storage_get_status(void)
{
    day_storage_status_t status;
    day_storage_refresh_status(&status);
    return status;
}

static uint32_t next_sequence(void)
{
    DIR *dir = opendir(DAY_SD_MOUNT_POINT);
    if (!dir) {
        return 0;
    }
    uint32_t max_seq = 0;
    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        unsigned seq = 0;
        if (sscanf(ent->d_name, "REC_%04u_", &seq) == 1 && seq >= max_seq) {
            max_seq = seq + 1;
        }
    }
    closedir(dir);
    return max_seq % 10000;
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
    paths->sequence = next_sequence();

    struct tm tm;
    localtime_r(&record_time, &tm);
    if (tm.tm_year < 100) {
        time_t fallback = time(NULL);
        localtime_r(&fallback, &tm);
    }

    snprintf(paths->dir_path, sizeof(paths->dir_path),
             DAY_SD_MOUNT_POINT "/REC_%04lu_%02d%02d%02d_%02d%02d%02d",
             (unsigned long)paths->sequence,
             (tm.tm_year + 1900) % 100, tm.tm_mon + 1, tm.tm_mday,
             tm.tm_hour, tm.tm_min, tm.tm_sec);

    if (mkdir(paths->dir_path, 0775) != 0) {
        ESP_LOGW(TAG, "mkdir failed for %s", paths->dir_path);
        return ESP_FAIL;
    }
    snprintf(paths->video_path, sizeof(paths->video_path), "%s/video.avi", paths->dir_path);
    snprintf(paths->audio_path, sizeof(paths->audio_path), "%s/audio.wav", paths->dir_path);
    snprintf(paths->imu_path, sizeof(paths->imu_path), "%s/imu.json", paths->dir_path);
    snprintf(paths->meta_path, sizeof(paths->meta_path), "%s/meta.json", paths->dir_path);
    return ESP_OK;
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
        s_card = NULL;
    }
    if (s_bus_initialized) {
        spi_bus_free(s_host.slot);
        s_bus_initialized = false;
    }
}
