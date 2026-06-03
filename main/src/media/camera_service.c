#include "camera_service.h"

#include <stdio.h>
#include <string.h>
#include "day_pins.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "day_camera";

static bool s_initialized;
static day_camera_status_t s_status = {
    .last_error = ESP_ERR_INVALID_STATE,
};

static framesize_t sanitize_framesize(int value)
{
    if (value < 0 || value >= FRAMESIZE_INVALID) {
        return FRAMESIZE_FHD;
    }
    return (framesize_t)value;
}

esp_err_t day_camera_init(const day_config_t *cfg)
{
    if (s_initialized) {
        return ESP_OK;
    }
    int jpeg_quality = cfg ? cfg->camera_jpeg_quality : 12;
    if (jpeg_quality < 4 || jpeg_quality > 63) {
        jpeg_quality = 12;
    }
    framesize_t frame_size = cfg ? sanitize_framesize(cfg->camera_framesize) : FRAMESIZE_FHD;

    camera_config_t camera_config = {
        .pin_pwdn = -1,
        .pin_reset = DAY_PIN_CAM_RST,
        .pin_xclk = DAY_PIN_CAM_MCLK,
        .pin_sccb_sda = DAY_PIN_CAM_SIOD,
        .pin_sccb_scl = DAY_PIN_CAM_SIOC,
        .pin_d7 = DAY_PIN_CAM_D7,
        .pin_d6 = DAY_PIN_CAM_D6,
        .pin_d5 = DAY_PIN_CAM_D5,
        .pin_d4 = DAY_PIN_CAM_D4,
        .pin_d3 = DAY_PIN_CAM_D3,
        .pin_d2 = DAY_PIN_CAM_D2,
        .pin_d1 = DAY_PIN_CAM_D1,
        .pin_d0 = DAY_PIN_CAM_D0,
        .pin_vsync = DAY_PIN_CAM_VSYNC,
        .pin_href = DAY_PIN_CAM_HREF,
        .pin_pclk = DAY_PIN_CAM_PCLK,
        .xclk_freq_hz = 20000000,
        .ledc_timer = LEDC_TIMER_0,
        .ledc_channel = LEDC_CHANNEL_0,
        .pixel_format = PIXFORMAT_JPEG,
        .frame_size = frame_size,
        .jpeg_quality = jpeg_quality,
        .fb_count = 2,
        .fb_location = CAMERA_FB_IN_PSRAM,
        .grab_mode = CAMERA_GRAB_LATEST,
        .sccb_i2c_port = DAY_I2C_CAMERA_PORT,
    };

    esp_err_t ret = esp_camera_init(&camera_config);
    s_initialized = ret == ESP_OK;
    s_status.initialized = s_initialized;
    s_status.last_error = ret;
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "camera init failed: %s", esp_err_to_name(ret));
    }
    return ret;
}

void day_camera_deinit(void)
{
    if (s_initialized) {
        esp_camera_deinit();
    }
    s_initialized = false;
    s_status.initialized = false;
    s_status.streaming = false;
    s_status.recording = false;
}

esp_err_t day_camera_capture(camera_fb_t **out_fb)
{
    if (!out_fb) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_fb = NULL;
    if (!s_initialized) {
        s_status.last_error = ESP_ERR_INVALID_STATE;
        return ESP_ERR_INVALID_STATE;
    }
    int64_t start = esp_timer_get_time();
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        s_status.last_error = ESP_FAIL;
        return ESP_FAIL;
    }
    int64_t elapsed = esp_timer_get_time() - start;
    s_status.frame_count++;
    s_status.fps = elapsed > 0 ? 1000000.0f / (float)elapsed : 0.0f;
    s_status.last_error = ESP_OK;
    *out_fb = fb;
    return ESP_OK;
}

void day_camera_return(camera_fb_t *fb)
{
    if (fb) {
        esp_camera_fb_return(fb);
    }
}

esp_err_t day_camera_stream_mjpeg(httpd_req_t *req)
{
    static const char *boundary = "123456789000000000000987654321";
    char part_header[96];
    esp_err_t ret = httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=123456789000000000000987654321");
    if (ret != ESP_OK) {
        return ret;
    }
    s_status.streaming = true;
    while (!s_status.recording) {
        camera_fb_t *fb = NULL;
        ret = day_camera_capture(&fb);
        if (ret != ESP_OK) {
            break;
        }
        ret = httpd_resp_send_chunk(req, "\r\n--", 4);
        if (ret == ESP_OK) {
            ret = httpd_resp_send_chunk(req, boundary, strlen(boundary));
        }
        if (ret == ESP_OK) {
            int hlen = snprintf(part_header, sizeof(part_header),
                                "\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                                (unsigned)fb->len);
            ret = httpd_resp_send_chunk(req, part_header, hlen);
        }
        if (ret == ESP_OK) {
            ret = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
        }
        day_camera_return(fb);
        if (ret != ESP_OK) {
            break;
        }
    }
    httpd_resp_send_chunk(req, NULL, 0);
    s_status.streaming = false;
    return ret;
}

void day_camera_set_recording(bool recording)
{
    s_status.recording = recording;
}

day_camera_status_t day_camera_get_status(void)
{
    return s_status;
}
