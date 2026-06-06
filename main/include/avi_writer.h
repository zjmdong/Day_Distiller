#pragma once

#include <stdint.h>
#include <stdio.h>
#include "esp_err.h"

typedef struct {
    FILE *file;
    uint32_t width;
    uint32_t height;
    uint32_t fps;
    uint32_t frames;
    long riff_size_pos;
    long avih_us_per_frame_pos;
    long avih_frames_pos;
    long strh_scale_pos;
    long strh_rate_pos;
    long strh_frames_pos;
    long movi_size_pos;
    long movi_list_pos;
} day_avi_writer_t;

esp_err_t day_avi_begin(day_avi_writer_t *writer, FILE *file, uint32_t width, uint32_t height, uint32_t fps);
esp_err_t day_avi_write_frame(day_avi_writer_t *writer, const uint8_t *data, uint32_t len);
esp_err_t day_avi_finish(day_avi_writer_t *writer, uint32_t actual_frame_us);
