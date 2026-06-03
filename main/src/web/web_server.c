#include "web_server.h"

#include <stdlib.h>
#include <string.h>
#include "camera_service.h"
#include "recorder.h"
#include "storage_service.h"
#include "wifi_portal.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

extern const char web_index_html_start[] asm("_binary_index_html_start");
extern const char web_index_html_end[] asm("_binary_index_html_end");

static const char *TAG = "day_web";

#define DAY_STATUS_JSON_LEN 2600
#define DAY_FILES_JSON_LEN 1800
#define DAY_WIFI_SCAN_JSON_LEN 2000

static httpd_handle_t s_server;
static httpd_handle_t s_stream_server;
static day_web_callbacks_t s_cb;
static int s_ws_clients[4] = {-1, -1, -1, -1};
static volatile bool s_record_request_active;

static void record_request_task(void *arg);

static void json_send(httpd_req_t *req, const char *json)
{
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, json);
}

static const char *boolstr(bool value)
{
    return value ? "true" : "false";
}

static esp_err_t root_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, web_index_html_start, web_index_html_end - web_index_html_start);
}

static void status_to_json(const day_device_status_t *st, char *buf, size_t len)
{
    snprintf(buf, len,
             "{"
             "\"recording\":%s,"
             "\"last_record_error\":\"%s\","
             "\"battery\":{\"soc\":%.2f,\"voltage\":%.3f,\"charge_state\":%d,\"confidence\":%u,\"err\":\"%s\"},"
             "\"rtc\":{\"valid\":%s,\"iso\":\"%s\",\"err\":\"%s\"},"
             "\"storage\":{\"mounted\":%s,\"total\":%llu,\"free\":%llu,\"err\":\"%s\"},"
             "\"camera\":{\"initialized\":%s,\"recording\":%s,\"streaming\":%s,\"frames\":%lu,\"fps\":%.1f,\"err\":\"%s\"},"
             "\"audio\":{\"initialized\":%s,\"rate\":%lu,\"rms\":%.4f,\"peak\":%.4f,\"err\":\"%s\"},"
             "\"imu\":{\"present\":%s,\"ax\":%d,\"ay\":%d,\"az\":%d,\"gx\":%d,\"gy\":%d,\"gz\":%d,"
             "\"roll\":%.2f,\"pitch\":%.2f,\"yaw\":%.2f,\"err\":\"%s\"},"
             "\"wifi\":{\"ap\":%s,\"sta\":%s,\"time_synced\":%s,\"clients\":%d,\"ap_ssid\":\"%s\",\"sta_ssid\":\"%s\",\"ip\":\"%s\",\"err\":\"%s\"}"
             "}",
             boolstr(st->recording_active),
             esp_err_to_name(st->last_record_error),
             st->battery.soc_percent, st->battery.voltage_v, st->battery.charge_state,
             st->battery.charge_confidence, esp_err_to_name(st->battery.last_error),
             boolstr(st->rtc.valid), st->rtc.iso8601, esp_err_to_name(st->rtc.last_error),
             boolstr(st->storage.mounted), (unsigned long long)st->storage.total_bytes,
             (unsigned long long)st->storage.free_bytes, esp_err_to_name(st->storage.last_error),
             boolstr(st->camera.initialized), boolstr(st->camera.recording), boolstr(st->camera.streaming),
             (unsigned long)st->camera.frame_count, st->camera.fps, esp_err_to_name(st->camera.last_error),
             boolstr(st->audio.initialized), (unsigned long)st->audio.sample_rate_hz,
             st->audio.rms, st->audio.peak, esp_err_to_name(st->audio.last_error),
             boolstr(st->imu.present), st->imu.last_sample.ax, st->imu.last_sample.ay, st->imu.last_sample.az,
             st->imu.last_sample.gx, st->imu.last_sample.gy, st->imu.last_sample.gz,
             st->imu.last_sample.roll_deg, st->imu.last_sample.pitch_deg, st->imu.last_sample.yaw_deg,
             esp_err_to_name(st->imu.last_error),
             boolstr(st->wifi.ap_running), boolstr(st->wifi.sta_connected), boolstr(st->wifi.time_synced),
             st->wifi.ap_clients, st->wifi.ap_ssid, st->wifi.sta_ssid, st->wifi.ip_addr,
             esp_err_to_name(st->wifi.last_error));
}

static esp_err_t status_handler(httpd_req_t *req)
{
    day_device_status_t *status = calloc(1, sizeof(*status));
    char *json = malloc(DAY_STATUS_JSON_LEN);
    if (!status || !json) {
        free(status);
        free(json);
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "out of memory");
        return ESP_ERR_NO_MEM;
    }
    if (s_cb.get_status) {
        s_cb.get_status(status);
    }
    status_to_json(status, json, DAY_STATUS_JSON_LEN);
    json_send(req, json);
    free(status);
    free(json);
    return ESP_OK;
}

static void config_to_json(const day_config_t *cfg, char *buf, size_t len)
{
    snprintf(buf, len,
             "{"
             "\"auto_record_enabled\":%s,"
             "\"wake_interval_sec\":%lu,"
             "\"shake_trigger_enabled\":%s,"
             "\"camera_framesize\":%d,"
             "\"camera_jpeg_quality\":%d,"
             "\"audio_sample_rate_hz\":%lu,"
             "\"imu_sample_rate_hz\":%lu,"
             "\"low_battery_percent\":%u,"
             "\"wifi_ssid\":\"%s\""
             "}",
             boolstr(cfg->auto_record_enabled),
             (unsigned long)cfg->wake_interval_sec,
             boolstr(cfg->shake_trigger_enabled),
             cfg->camera_framesize,
             cfg->camera_jpeg_quality,
             (unsigned long)cfg->audio_sample_rate_hz,
             (unsigned long)cfg->imu_sample_rate_hz,
             cfg->low_battery_percent,
             cfg->wifi_ssid);
}

static esp_err_t config_get_handler(httpd_req_t *req)
{
    char json[700];
    config_to_json(s_cb.config, json, sizeof(json));
    json_send(req, json);
    return ESP_OK;
}

static esp_err_t read_body(httpd_req_t *req, char *buf, size_t len)
{
    int remaining = req->content_len;
    if (remaining <= 0 || (size_t)remaining >= len) {
        return ESP_ERR_INVALID_SIZE;
    }
    int offset = 0;
    while (remaining > 0) {
        int ret = httpd_req_recv(req, buf + offset, remaining);
        if (ret <= 0) {
            return ESP_FAIL;
        }
        remaining -= ret;
        offset += ret;
    }
    buf[offset] = '\0';
    return ESP_OK;
}

static const char *json_value_ptr(const char *json, const char *name)
{
    char key[64];
    snprintf(key, sizeof(key), "\"%s\"", name);
    const char *p = strstr(json, key);
    if (!p) {
        return NULL;
    }
    p = strchr(p + strlen(key), ':');
    if (!p) {
        return NULL;
    }
    p++;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') {
        p++;
    }
    return p;
}

static void json_bool(const char *json, const char *name, bool *out)
{
    const char *p = json_value_ptr(json, name);
    if (!p) {
        return;
    }
    if (strncmp(p, "true", 4) == 0) {
        *out = true;
    } else if (strncmp(p, "false", 5) == 0) {
        *out = false;
    }
}

static void json_u32(const char *json, const char *name, uint32_t *out)
{
    const char *p = json_value_ptr(json, name);
    if (p) {
        *out = (uint32_t)strtoul(p, NULL, 10);
    }
}

static void json_int(const char *json, const char *name, int *out)
{
    const char *p = json_value_ptr(json, name);
    if (p) {
        *out = (int)strtol(p, NULL, 10);
    }
}

static bool json_string(const char *json, const char *name, char *out, size_t len)
{
    const char *p = json_value_ptr(json, name);
    if (!p || *p != '"' || len == 0) {
        return false;
    }
    p++;
    size_t used = 0;
    while (*p && *p != '"' && used + 1 < len) {
        if (*p == '\\' && p[1]) {
            p++;
        }
        out[used++] = *p++;
    }
    out[used] = '\0';
    return true;
}

static esp_err_t config_post_handler(httpd_req_t *req)
{
    char body[768];
    esp_err_t ret = read_body(req, body, sizeof(body));
    if (ret != ESP_OK) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad body");
        return ret;
    }
    day_config_t next = *s_cb.config;
    json_bool(body, "auto_record_enabled", &next.auto_record_enabled);
    json_bool(body, "shake_trigger_enabled", &next.shake_trigger_enabled);
    json_u32(body, "wake_interval_sec", &next.wake_interval_sec);
    json_int(body, "camera_framesize", &next.camera_framesize);
    json_int(body, "camera_jpeg_quality", &next.camera_jpeg_quality);
    json_u32(body, "audio_sample_rate_hz", &next.audio_sample_rate_hz);
    json_u32(body, "imu_sample_rate_hz", &next.imu_sample_rate_hz);
    uint32_t low_batt = next.low_battery_percent;
    json_u32(body, "low_battery_percent", &low_batt);
    if (low_batt > 0 && low_batt < 100) {
        next.low_battery_percent = (uint8_t)low_batt;
    }
    if (s_cb.save_config) {
        ret = s_cb.save_config(&next);
    }
    json_send(req, ret == ESP_OK ? "{\"ok\":true}" : "{\"ok\":false}");
    return ret;
}

static esp_err_t record_handler(httpd_req_t *req)
{
    if (!s_cb.record_once) {
        json_send(req, "{\"ok\":false,\"error\":\"ESP_ERR_NOT_SUPPORTED\"}");
        return ESP_OK;
    }
    if (s_record_request_active || day_recorder_is_active()) {
        json_send(req, "{\"ok\":false,\"busy\":true}");
        return ESP_OK;
    }
    s_record_request_active = true;
    BaseType_t ok = xTaskCreate(record_request_task, "web_record", 6144, NULL, 6, NULL);
    if (ok != pdPASS) {
        s_record_request_active = false;
        json_send(req, "{\"ok\":false,\"error\":\"ESP_ERR_NO_MEM\"}");
        return ESP_OK;
    }
    json_send(req, "{\"ok\":true,\"started\":true}");
    return ESP_OK;
}

static esp_err_t files_handler(httpd_req_t *req)
{
    char *json = malloc(DAY_FILES_JSON_LEN);
    if (!json) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "out of memory");
        return ESP_ERR_NO_MEM;
    }
    esp_err_t ret = day_storage_files_json(json, DAY_FILES_JSON_LEN);
    if (ret != ESP_OK) {
        snprintf(json, DAY_FILES_JSON_LEN, "{\"mounted\":false,\"error\":\"%s\",\"records\":[]}", esp_err_to_name(ret));
    }
    json_send(req, json);
    free(json);
    return ESP_OK;
}

static esp_err_t wifi_scan_handler(httpd_req_t *req)
{
    char *json = malloc(DAY_WIFI_SCAN_JSON_LEN);
    if (!json) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "out of memory");
        return ESP_ERR_NO_MEM;
    }
    esp_err_t ret = day_wifi_scan_json(json, DAY_WIFI_SCAN_JSON_LEN);
    if (ret != ESP_OK) {
        snprintf(json, DAY_WIFI_SCAN_JSON_LEN, "{\"ok\":false,\"error\":\"%s\",\"aps\":[]}", esp_err_to_name(ret));
    }
    json_send(req, json);
    free(json);
    return ESP_OK;
}

static esp_err_t wifi_connect_handler(httpd_req_t *req)
{
    char body[256];
    esp_err_t ret = read_body(req, body, sizeof(body));
    if (ret != ESP_OK) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad body");
        return ret;
    }
    char ssid[DAY_WIFI_SSID_MAX + 1] = {0};
    char pass[DAY_WIFI_PASSWORD_MAX + 1] = {0};
    if (!json_string(body, "ssid", ssid, sizeof(ssid)) || ssid[0] == '\0') {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "missing ssid");
        return ESP_FAIL;
    }
    json_string(body, "password", pass, sizeof(pass));
    ret = day_wifi_save_credentials_and_connect(s_cb.config, ssid, pass);
    json_send(req, ret == ESP_OK ? "{\"ok\":true}" : "{\"ok\":false}");
    return ESP_OK;
}

static esp_err_t stream_handler(httpd_req_t *req)
{
    esp_err_t ret = day_camera_init(s_cb.config);
    if (ret != ESP_OK) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, esp_err_to_name(ret));
        return ret;
    }
    return day_camera_stream_mjpeg(req);
}

static void record_request_task(void *arg)
{
    esp_err_t ret = s_cb.record_once ? s_cb.record_once() : ESP_ERR_NOT_SUPPORTED;
    ESP_LOGI(TAG, "web record request finished: %s", esp_err_to_name(ret));
    s_record_request_active = false;
    vTaskDelete(NULL);
}

static void add_ws_client(int fd)
{
    for (size_t i = 0; i < sizeof(s_ws_clients) / sizeof(s_ws_clients[0]); ++i) {
        if (s_ws_clients[i] == fd) {
            return;
        }
        if (s_ws_clients[i] < 0) {
            s_ws_clients[i] = fd;
            return;
        }
    }
    s_ws_clients[0] = fd;
}

static bool has_ws_clients(void)
{
    for (size_t i = 0; i < sizeof(s_ws_clients) / sizeof(s_ws_clients[0]); ++i) {
        if (s_ws_clients[i] >= 0) {
            return true;
        }
    }
    return false;
}

static esp_err_t ws_handler(httpd_req_t *req)
{
    add_ws_client(httpd_req_to_sockfd(req));
    httpd_ws_frame_t pkt = {
        .type = HTTPD_WS_TYPE_TEXT,
    };
    esp_err_t ret = httpd_ws_recv_frame(req, &pkt, 0);
    if (ret == ESP_OK && pkt.len > 0) {
        uint8_t drain[32];
        pkt.payload = drain;
        httpd_ws_recv_frame(req, &pkt, sizeof(drain));
    }
    return ESP_OK;
}

static void telemetry_task(void *arg)
{
    day_device_status_t *status = calloc(1, sizeof(*status));
    char *json = malloc(DAY_STATUS_JSON_LEN);
    if (!status || !json) {
        ESP_LOGE(TAG, "telemetry alloc failed");
        free(status);
        free(json);
        vTaskDelete(NULL);
    }

    while (s_server) {
        if (!has_ws_clients()) {
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }
        memset(status, 0, sizeof(*status));
        if (s_cb.get_status) {
            s_cb.get_status(status);
        }
        status_to_json(status, json, DAY_STATUS_JSON_LEN);
        httpd_ws_frame_t frame = {
            .type = HTTPD_WS_TYPE_TEXT,
            .payload = (uint8_t *)json,
            .len = strlen(json),
        };
        for (size_t i = 0; i < sizeof(s_ws_clients) / sizeof(s_ws_clients[0]); ++i) {
            if (s_ws_clients[i] >= 0) {
                if (httpd_ws_send_frame_async(s_server, s_ws_clients[i], &frame) != ESP_OK) {
                    s_ws_clients[i] = -1;
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(250));
    }
    free(status);
    free(json);
    vTaskDelete(NULL);
}

static esp_err_t redirect_404(httpd_req_t *req, httpd_err_code_t err)
{
    ESP_LOGI(TAG, "captive redirect uri=%s", req->uri);
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "http://192.168.4.1/");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_sendstr(req, "Redirect to the device panel");
    return ESP_OK;
}

static esp_err_t captive_redirect_handler(httpd_req_t *req)
{
    return redirect_404(req, HTTPD_404_NOT_FOUND);
}

esp_err_t day_web_start(const day_web_callbacks_t *callbacks)
{
    if (s_server) {
        return ESP_OK;
    }
    if (!callbacks || !callbacks->config) {
        return ESP_ERR_INVALID_ARG;
    }
    s_cb = *callbacks;
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.max_uri_handlers = 16;
    config.max_open_sockets = 6;
    config.stack_size = 6144;
    config.lru_purge_enable = true;
    config.uri_match_fn = httpd_uri_match_wildcard;

    esp_err_t ret = httpd_start(&s_server, &config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "web server start failed: %s max_open_sockets=%d",
                 esp_err_to_name(ret), config.max_open_sockets);
        return ret;
    }

    httpd_uri_t root = {.uri = "/", .method = HTTP_GET, .handler = root_handler};
    httpd_uri_t status = {.uri = "/api/status", .method = HTTP_GET, .handler = status_handler};
    httpd_uri_t cfg_get = {.uri = "/api/config", .method = HTTP_GET, .handler = config_get_handler};
    httpd_uri_t cfg_post = {.uri = "/api/config", .method = HTTP_POST, .handler = config_post_handler};
    httpd_uri_t record = {.uri = "/api/record", .method = HTTP_POST, .handler = record_handler};
    httpd_uri_t files = {.uri = "/api/files", .method = HTTP_GET, .handler = files_handler};
    httpd_uri_t scan = {.uri = "/api/wifi/scan", .method = HTTP_GET, .handler = wifi_scan_handler};
    httpd_uri_t connect = {.uri = "/api/wifi/connect", .method = HTTP_POST, .handler = wifi_connect_handler};
    httpd_uri_t ws = {.uri = "/ws/telemetry", .method = HTTP_GET, .handler = ws_handler, .is_websocket = true};
    httpd_uri_t captive = {.uri = "/*", .method = HTTP_GET, .handler = captive_redirect_handler};

    httpd_register_uri_handler(s_server, &root);
    httpd_register_uri_handler(s_server, &status);
    httpd_register_uri_handler(s_server, &cfg_get);
    httpd_register_uri_handler(s_server, &cfg_post);
    httpd_register_uri_handler(s_server, &record);
    httpd_register_uri_handler(s_server, &files);
    httpd_register_uri_handler(s_server, &scan);
    httpd_register_uri_handler(s_server, &connect);
    httpd_register_uri_handler(s_server, &ws);
    httpd_register_uri_handler(s_server, &captive);
    httpd_register_err_handler(s_server, HTTPD_404_NOT_FOUND, redirect_404);

    for (size_t i = 0; i < sizeof(s_ws_clients) / sizeof(s_ws_clients[0]); ++i) {
        s_ws_clients[i] = -1;
    }
    if (xTaskCreate(telemetry_task, "web_telemetry", 4096, NULL, 4, NULL) != pdPASS) {
        ESP_LOGE(TAG, "failed to create telemetry task");
    }
    httpd_config_t stream_config = HTTPD_DEFAULT_CONFIG();
    stream_config.server_port = 81;
    stream_config.ctrl_port = ESP_HTTPD_DEF_CTRL_PORT + 1;
    stream_config.max_uri_handlers = 2;
    stream_config.max_open_sockets = 2;
    stream_config.stack_size = 6144;
    stream_config.lru_purge_enable = true;
    ret = httpd_start(&s_stream_server, &stream_config);
    if (ret == ESP_OK) {
        httpd_uri_t stream = {.uri = "/stream.mjpg", .method = HTTP_GET, .handler = stream_handler};
        httpd_register_uri_handler(s_stream_server, &stream);
        ESP_LOGI(TAG, "stream server started on port %u", stream_config.server_port);
    } else {
        ESP_LOGE(TAG, "stream server start failed: %s", esp_err_to_name(ret));
    }

    ESP_LOGI(TAG, "web server started on port %u", config.server_port);
    return ESP_OK;
}

esp_err_t day_web_stop(void)
{
    if (s_stream_server) {
        httpd_handle_t stream_server = s_stream_server;
        s_stream_server = NULL;
        ESP_ERROR_CHECK_WITHOUT_ABORT(httpd_stop(stream_server));
    }
    if (s_server) {
        httpd_handle_t server = s_server;
        s_server = NULL;
        return httpd_stop(server);
    }
    return ESP_OK;
}
