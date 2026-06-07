#include "wifi_portal.h"

#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "app_config.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "dns_server.h"
#include "esp_sntp.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "lwip/inet.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

#define WIFI_BIT_CONNECTED BIT0
#define WIFI_BIT_FAIL BIT1

static const char *TAG = "day_wifi";

static bool s_initialized;
static esp_netif_t *s_ap_netif;
static esp_netif_t *s_sta_netif;
static dns_server_handle_t s_dns;
static EventGroupHandle_t s_events;
static day_wifi_status_t s_status = {
    .last_error = ESP_ERR_INVALID_STATE,
};
static int s_retry_count;
static bool s_connecting;

static void set_wifi_low_latency(void)
{
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_set_ps(WIFI_PS_NONE));
}

static void json_escape_string(const char *src, char *dst, size_t len)
{
    if (!dst || len == 0) {
        return;
    }
    size_t used = 0;
    if (!src) {
        dst[0] = '\0';
        return;
    }
    while (*src && used + 1 < len) {
        char c = *src++;
        if ((c == '"' || c == '\\') && used + 2 < len) {
            dst[used++] = '\\';
            dst[used++] = c;
        } else if ((unsigned char)c >= 0x20) {
            dst[used++] = c;
        }
    }
    dst[used] = '\0';
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        s_status.sta_connected = false;
        if (s_connecting && s_retry_count < 3) {
            s_retry_count++;
            esp_wifi_connect();
        } else if (s_events) {
            xEventGroupSetBits(s_events, WIFI_BIT_FAIL);
        }
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_AP_STACONNECTED) {
        s_status.ap_clients++;
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_AP_STADISCONNECTED) {
        if (s_status.ap_clients > 0) {
            s_status.ap_clients--;
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = event_data;
        s_status.sta_connected = true;
        inet_ntoa_r(event->ip_info.ip.addr, s_status.ip_addr, sizeof(s_status.ip_addr));
        if (s_events) {
            xEventGroupSetBits(s_events, WIFI_BIT_CONNECTED);
        }
    }
}

static esp_err_t init_netif_once(void)
{
    esp_err_t ret = esp_netif_init();
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        return ret;
    }
    ret = esp_event_loop_create_default();
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        return ret;
    }
    return ESP_OK;
}

esp_err_t day_wifi_init(void)
{
    if (s_initialized) {
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(init_netif_once(), TAG, "netif init failed");
    s_events = xEventGroupCreate();
    if (!s_events) {
        return ESP_ERR_NO_MEM;
    }

    s_sta_netif = esp_netif_create_default_wifi_sta();
    s_ap_netif = esp_netif_create_default_wifi_ap();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&cfg), TAG, "wifi init failed");
    set_wifi_low_latency();
    esp_log_level_set("example_dns_redirect_server", ESP_LOG_WARN);
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL));
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL));
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    s_initialized = true;
    s_status.last_error = ESP_OK;
    return ESP_OK;
}

static esp_err_t connect_sta(const day_config_t *cfg)
{
    if (!cfg || cfg->wifi_ssid[0] == '\0') {
        return ESP_ERR_NOT_FOUND;
    }
    wifi_config_t sta_cfg = {0};
    strlcpy((char *)sta_cfg.sta.ssid, cfg->wifi_ssid, sizeof(sta_cfg.sta.ssid));
    strlcpy((char *)sta_cfg.sta.password, cfg->wifi_password, sizeof(sta_cfg.sta.password));
    sta_cfg.sta.threshold.authmode = WIFI_AUTH_OPEN;

    xEventGroupClearBits(s_events, WIFI_BIT_CONNECTED | WIFI_BIT_FAIL);
    s_retry_count = 0;
    s_connecting = true;
    s_status.time_synced = false;
    wifi_mode_t mode = s_status.ap_running ? WIFI_MODE_APSTA : WIFI_MODE_STA;
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(mode), TAG, "set STA mode failed");
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &sta_cfg), TAG, "set STA config failed");
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_start());
    set_wifi_low_latency();
    ESP_RETURN_ON_ERROR(esp_wifi_connect(), TAG, "connect failed");
    EventBits_t bits = xEventGroupWaitBits(s_events, WIFI_BIT_CONNECTED | WIFI_BIT_FAIL,
                                           pdTRUE, pdFALSE, pdMS_TO_TICKS(15000));
    s_connecting = false;
    if (bits & WIFI_BIT_CONNECTED) {
        strlcpy(s_status.sta_ssid, cfg->wifi_ssid, sizeof(s_status.sta_ssid));
        return ESP_OK;
    }
    return ESP_ERR_TIMEOUT;
}

static esp_err_t sync_sntp(const day_config_t *cfg)
{
    const char *tz = (cfg && cfg->timezone[0]) ? cfg->timezone : "CST-8";
    const char *server = (cfg && cfg->ntp_server[0]) ? cfg->ntp_server : "ntp1.aliyun.com";
    setenv("TZ", tz, 1);
    tzset();

    if (esp_sntp_enabled()) {
        esp_sntp_stop();
    }
    esp_sntp_set_sync_status(SNTP_SYNC_STATUS_RESET);
    esp_sntp_set_sync_mode(SNTP_SYNC_MODE_IMMED);
    esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
    esp_sntp_setservername(0, server);
    esp_sntp_init();
    ESP_LOGI(TAG, "SNTP sync start server=%s timezone=%s", server, tz);

    for (int i = 0; i < 20; ++i) {
        if (esp_sntp_get_sync_status() == SNTP_SYNC_STATUS_COMPLETED) {
            time_t now = 0;
            time(&now);
            s_status.time_synced = true;
            ESP_LOGI(TAG, "SNTP synced at %lld", (long long)now);
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }
    return ESP_ERR_TIMEOUT;
}

esp_err_t day_wifi_sync_time(const day_config_t *cfg)
{
    esp_err_t ret = day_wifi_init();
    if (ret == ESP_OK) {
        ret = connect_sta(cfg);
    }
    if (ret == ESP_OK) {
        ret = sync_sntp(cfg);
    }
    s_status.last_error = ret;
    return ret;
}

static void build_ap_ssid(char *out, size_t len)
{
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    snprintf(out, len, "AI_CAM_%02X%02X", mac[4], mac[5]);
}

static esp_err_t set_captive_portal_url(void)
{
#ifdef CONFIG_ESP_ENABLE_DHCP_CAPTIVEPORTAL
    esp_netif_ip_info_t ip_info;
    ESP_RETURN_ON_ERROR(esp_netif_get_ip_info(s_ap_netif, &ip_info), TAG, "get AP IP failed");
    char ip_addr[16];
    inet_ntoa_r(ip_info.ip.addr, ip_addr, sizeof(ip_addr));
    char uri[32];
    snprintf(uri, sizeof(uri), "http://%s", ip_addr);
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_netif_dhcps_stop(s_ap_netif));
    esp_err_t ret = esp_netif_dhcps_option(s_ap_netif, ESP_NETIF_OP_SET,
                                           ESP_NETIF_CAPTIVEPORTAL_URI, uri, strlen(uri));
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_netif_dhcps_start(s_ap_netif));
    return ret;
#else
    return ESP_OK;
#endif
}

esp_err_t day_wifi_run_portal_window(uint32_t window_ms)
{
    ESP_RETURN_ON_ERROR(day_wifi_init(), TAG, "wifi init failed");
    build_ap_ssid(s_status.ap_ssid, sizeof(s_status.ap_ssid));
    wifi_config_t ap_cfg = {
        .ap = {
            .ssid_len = strlen(s_status.ap_ssid),
            .channel = 1,
            .max_connection = 2,
            .authmode = WIFI_AUTH_OPEN,
        },
    };
    strlcpy((char *)ap_cfg.ap.ssid, s_status.ap_ssid, sizeof(ap_cfg.ap.ssid));

    wifi_mode_t mode = s_status.sta_connected ? WIFI_MODE_APSTA : WIFI_MODE_AP;
    if (s_status.sta_connected) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_disconnect());
        s_status.sta_connected = false;
        s_status.ip_addr[0] = '\0';
        mode = WIFI_MODE_AP;
    }
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(mode), TAG, "set AP mode failed");
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_AP, &ap_cfg), TAG, "set AP config failed");
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_start());
    set_wifi_low_latency();
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_set_bandwidth(WIFI_IF_AP, WIFI_BW20));
    set_captive_portal_url();

    if (!s_dns) {
        dns_server_config_t dns_cfg = DNS_SERVER_CONFIG_SINGLE("*", "WIFI_AP_DEF");
        s_dns = start_dns_server(&dns_cfg);
    }
    s_status.ap_running = true;
    s_status.last_error = ESP_OK;
    ESP_LOGI(TAG, "portal AP running ssid=%s window_ms=%lu", s_status.ap_ssid, (unsigned long)window_ms);

    int64_t deadline = esp_timer_get_time() + (int64_t)window_ms * 1000;
    while (esp_timer_get_time() < deadline) {
        if (s_status.ap_clients > 0) {
            deadline = esp_timer_get_time() + 30000000LL;
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }
    return ESP_OK;
}

esp_err_t day_wifi_stop_portal(void)
{
    if (s_dns) {
        stop_dns_server(s_dns);
        s_dns = NULL;
    }
    s_status.ap_running = false;
    s_status.ap_clients = 0;
    wifi_mode_t mode = s_status.sta_connected ? WIFI_MODE_STA : WIFI_MODE_NULL;
    esp_err_t ret = s_initialized ? esp_wifi_set_mode(mode) : ESP_OK;
    s_status.last_error = ret;
    ESP_LOGI(TAG, "portal AP stopped: %s", esp_err_to_name(ret));
    return ret;
}

esp_err_t day_wifi_restore_portal_ap_only(void)
{
    if (!s_initialized || !s_status.ap_running) {
        return ESP_OK;
    }
    if (s_status.sta_connected) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_disconnect());
        s_status.sta_connected = false;
        s_status.ip_addr[0] = '\0';
    }
    esp_err_t ret = esp_wifi_set_mode(WIFI_MODE_AP);
    if (ret == ESP_OK) {
        set_wifi_low_latency();
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_set_bandwidth(WIFI_IF_AP, WIFI_BW20));
    }
    s_status.last_error = ret;
    return ret;
}

esp_err_t day_wifi_scan_json(char *buffer, size_t len)
{
    if (!buffer || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    ESP_RETURN_ON_ERROR(day_wifi_init(), TAG, "wifi init failed");
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_set_mode(s_status.ap_running ? WIFI_MODE_APSTA : WIFI_MODE_STA));
    ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_start());
    set_wifi_low_latency();
    wifi_scan_config_t scan_cfg = {0};
    esp_err_t ret = esp_wifi_scan_start(&scan_cfg, true);
    if (ret != ESP_OK) {
        return ret;
    }
    wifi_ap_record_t aps[12];
    uint16_t count = sizeof(aps) / sizeof(aps[0]);
    ret = esp_wifi_scan_get_ap_records(&count, aps);
    if (ret != ESP_OK) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_wifi_restore_portal_ap_only());
        return ret;
    }
    size_t used = snprintf(buffer, len, "{\"ok\":true,\"aps\":[");
    for (int i = 0; i < count; ++i) {
        char ssid[sizeof(aps[i].ssid) * 2 + 1];
        json_escape_string((const char *)aps[i].ssid, ssid, sizeof(ssid));
        int written = snprintf(buffer + used, len - used,
                               "%s{\"ssid\":\"%s\",\"rssi\":%d,\"auth\":%d}",
                               i == 0 ? "" : ",", ssid, aps[i].rssi, aps[i].authmode);
        if (written < 0 || (size_t)written >= len - used) {
            return ESP_ERR_NO_MEM;
        }
        used += (size_t)written;
    }
    if (snprintf(buffer + used, len - used, "]}") >= len - used) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_wifi_restore_portal_ap_only());
        return ESP_ERR_NO_MEM;
    }
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_wifi_restore_portal_ap_only());
    return ESP_OK;
}

esp_err_t day_wifi_save_credentials_and_connect(day_config_t *cfg, const char *ssid, const char *password)
{
    if (!cfg || !ssid) {
        return ESP_ERR_INVALID_ARG;
    }
    strlcpy(cfg->wifi_ssid, ssid, sizeof(cfg->wifi_ssid));
    strlcpy(cfg->wifi_password, password ? password : "", sizeof(cfg->wifi_password));
    ESP_ERROR_CHECK_WITHOUT_ABORT(day_config_save(cfg));
    esp_err_t ret = day_wifi_sync_time(cfg);
    s_status.last_error = ret;
    return ret;
}

day_wifi_status_t day_wifi_get_status(void)
{
    return s_status;
}

void day_wifi_deinit_for_sleep(void)
{
    day_wifi_stop_portal();
    if (s_initialized) {
        wifi_mode_t mode = WIFI_MODE_NULL;
        if (esp_wifi_get_mode(&mode) == ESP_OK && (mode == WIFI_MODE_STA || mode == WIFI_MODE_APSTA)) {
            ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_disconnect());
        }
        ESP_ERROR_CHECK_WITHOUT_ABORT(esp_wifi_stop());
    }
    s_status.ap_running = false;
    s_status.sta_connected = false;
}
