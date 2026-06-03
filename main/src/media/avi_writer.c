#include "avi_writer.h"

#include <string.h>

static void w4(FILE *f, const char *s)
{
    fwrite(s, 1, 4, f);
}

static void le16(FILE *f, uint16_t v)
{
    fputc(v & 0xff, f);
    fputc((v >> 8) & 0xff, f);
}

static void le32(FILE *f, uint32_t v)
{
    fputc(v & 0xff, f);
    fputc((v >> 8) & 0xff, f);
    fputc((v >> 16) & 0xff, f);
    fputc((v >> 24) & 0xff, f);
}

static void patch32(FILE *f, long pos, uint32_t v)
{
    long cur = ftell(f);
    fseek(f, pos, SEEK_SET);
    le32(f, v);
    fseek(f, cur, SEEK_SET);
}

esp_err_t day_avi_begin(day_avi_writer_t *writer, FILE *file, uint32_t width, uint32_t height, uint32_t fps)
{
    if (!writer || !file || width == 0 || height == 0 || fps == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(writer, 0, sizeof(*writer));
    writer->file = file;
    writer->width = width;
    writer->height = height;
    writer->fps = fps;

    w4(file, "RIFF");
    writer->riff_size_pos = ftell(file);
    le32(file, 0);
    w4(file, "AVI ");

    w4(file, "LIST");
    le32(file, 4 + 8 + 56 + 8 + 4 + 8 + 56 + 8 + 40);
    w4(file, "hdrl");

    w4(file, "avih");
    le32(file, 56);
    le32(file, 1000000 / fps);
    le32(file, 0);
    le32(file, 0);
    le32(file, 0x10);
    writer->avih_frames_pos = ftell(file);
    le32(file, 0);
    le32(file, 0);
    le32(file, 1);
    le32(file, 0);
    le32(file, width);
    le32(file, height);
    for (int i = 0; i < 4; ++i) {
        le32(file, 0);
    }

    w4(file, "LIST");
    le32(file, 4 + 8 + 56 + 8 + 40);
    w4(file, "strl");

    w4(file, "strh");
    le32(file, 56);
    w4(file, "vids");
    w4(file, "MJPG");
    le32(file, 0);
    le16(file, 0);
    le16(file, 0);
    le32(file, 0);
    le32(file, 1);
    le32(file, fps);
    le32(file, 0);
    writer->strh_frames_pos = ftell(file);
    le32(file, 0);
    le32(file, 0);
    le32(file, 0xffffffff);
    le32(file, 0);
    le16(file, 0);
    le16(file, 0);
    le16(file, width);
    le16(file, height);

    w4(file, "strf");
    le32(file, 40);
    le32(file, 40);
    le32(file, width);
    le32(file, height);
    le16(file, 1);
    le16(file, 24);
    w4(file, "MJPG");
    le32(file, width * height * 3);
    le32(file, 0);
    le32(file, 0);
    le32(file, 0);
    le32(file, 0);

    w4(file, "LIST");
    writer->movi_size_pos = ftell(file);
    le32(file, 0);
    writer->movi_list_pos = ftell(file) - 4;
    w4(file, "movi");
    return ferror(file) ? ESP_FAIL : ESP_OK;
}

esp_err_t day_avi_write_frame(day_avi_writer_t *writer, const uint8_t *data, uint32_t len)
{
    if (!writer || !writer->file || !data || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    w4(writer->file, "00dc");
    le32(writer->file, len);
    fwrite(data, 1, len, writer->file);
    if (len & 1) {
        fputc(0, writer->file);
    }
    writer->frames++;
    return ferror(writer->file) ? ESP_FAIL : ESP_OK;
}

esp_err_t day_avi_finish(day_avi_writer_t *writer)
{
    if (!writer || !writer->file) {
        return ESP_ERR_INVALID_ARG;
    }
    fflush(writer->file);
    long end = ftell(writer->file);
    patch32(writer->file, writer->riff_size_pos, (uint32_t)(end - 8));
    patch32(writer->file, writer->avih_frames_pos, writer->frames);
    patch32(writer->file, writer->strh_frames_pos, writer->frames);
    patch32(writer->file, writer->movi_size_pos, (uint32_t)(end - writer->movi_list_pos - 4));
    fflush(writer->file);
    return ferror(writer->file) ? ESP_FAIL : ESP_OK;
}
