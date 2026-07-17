#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <time.h>
#include "esp_err.h"

#define DAY_WIFI_SSID_MAX 32
#define DAY_WIFI_PASSWORD_MAX 64
#define DAY_NTP_SERVER_MAX 64
#define DAY_TIMEZONE_MAX 32
#define DAY_AUDIO_WAVEFORM_SAMPLES 64

typedef enum {
    DAY_CHARGE_UNKNOWN = 0,
    DAY_CHARGE_NOT_CHARGING,
    DAY_CHARGE_ESTIMATED,
} day_charge_state_t;

typedef struct {
    bool auto_record_enabled;
    uint32_t wake_interval_sec;
    bool shake_trigger_enabled;
    int camera_framesize;
    int camera_preview_framesize;
    int camera_record_framesize;
    int camera_jpeg_quality;
    uint32_t camera_preview_fps;
    uint32_t camera_record_fps;
    uint32_t audio_sample_rate_hz;
    uint32_t imu_sample_rate_hz;
    uint8_t imu_orientation;
    uint8_t low_battery_percent;
    char ntp_server[DAY_NTP_SERVER_MAX + 1];
    char timezone[DAY_TIMEZONE_MAX + 1];
    char wifi_ssid[DAY_WIFI_SSID_MAX + 1];
    char wifi_password[DAY_WIFI_PASSWORD_MAX + 1];
} day_config_t;

typedef struct {
    float soc_percent;
    float voltage_v;
    day_charge_state_t charge_state;
    uint8_t charge_confidence;
    esp_err_t last_error;
} day_battery_status_t;

typedef struct {
    bool valid;
    time_t unix_time;
    char iso8601[32];
    esp_err_t last_error;
} day_rtc_status_t;

typedef struct {
    int64_t t_us;
    int16_t ax;
    int16_t ay;
    int16_t az;
    int16_t gx;
    int16_t gy;
    int16_t gz;
    float roll_deg;
    float pitch_deg;
    float yaw_deg;
    float q0;
    float q1;
    float q2;
    float q3;
} day_imu_sample_t;

typedef struct {
    bool present;
    day_imu_sample_t last_sample;
    esp_err_t last_error;
} day_imu_status_t;

typedef struct {
    bool mounted;
    uint64_t total_bytes;
    uint64_t free_bytes;
    esp_err_t last_error;
} day_storage_status_t;

typedef struct {
    bool initialized;
    bool recording;
    bool streaming;
    uint32_t frame_count;
    float fps;
    esp_err_t last_error;
} day_camera_status_t;

typedef struct {
    bool initialized;
    uint32_t sample_rate_hz;
    float rms;
    float peak;
    uint8_t waveform_len;
    int8_t waveform[DAY_AUDIO_WAVEFORM_SAMPLES];
    esp_err_t last_error;
} day_audio_status_t;

typedef struct {
    bool ap_running;
    bool sta_connected;
    bool time_synced;
    int ap_clients;
    uint8_t retry_count;
    char ap_ssid[DAY_WIFI_SSID_MAX + 1];
    char sta_ssid[DAY_WIFI_SSID_MAX + 1];
    char ip_addr[16];
    esp_err_t last_error;
} day_wifi_status_t;

typedef struct {
    day_config_t config;
    day_battery_status_t battery;
    day_rtc_status_t rtc;
    day_imu_status_t imu;
    day_storage_status_t storage;
    day_camera_status_t camera;
    day_audio_status_t audio;
    day_wifi_status_t wifi;
    bool recording_active;
    esp_err_t last_record_error;
} day_device_status_t;

typedef struct {
    char dir_path[96];
    char final_dir_path[96];
    char record_name[24];
    char video_path[128];
    char audio_path[128];
    char imu_path[128];
    char meta_path[128];
    uint32_t sequence;
    uint64_t record_counter;
    time_t record_time;
} day_record_paths_t;
