#pragma once

#include "day_types.h"
#include "esp_err.h"
#include "esp_http_server.h"
#include "esp_camera.h"

esp_err_t day_camera_init(const day_config_t *cfg);
esp_err_t day_camera_init_preview(const day_config_t *cfg);
esp_err_t day_camera_init_record(const day_config_t *cfg);
void day_camera_deinit(void);
esp_err_t day_camera_capture(camera_fb_t **out_fb);
void day_camera_return(camera_fb_t *fb);
void day_camera_note_frame_done(int64_t frame_start_us, uint32_t target_fps);
esp_err_t day_camera_stream_mjpeg(httpd_req_t *req, uint32_t target_fps);
void day_camera_set_recording(bool recording);
day_camera_status_t day_camera_get_status(void);
