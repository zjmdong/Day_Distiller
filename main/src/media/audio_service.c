#include "audio_service.h"

#include <math.h>
#include <string.h>
#include "day_pins.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "esp_log.h"

static const char *TAG = "day_audio";

static i2s_chan_handle_t s_rx_chan;
static uint32_t s_sample_rate;
static bool s_rx_enabled;
static day_audio_status_t s_status = {
    .last_error = ESP_ERR_INVALID_STATE,
};

static esp_err_t configure_channel(uint32_t sample_rate_hz)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 8;
    chan_cfg.dma_frame_num = 256;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, NULL, &s_rx_chan), TAG, "new I2S channel failed");

    i2s_std_slot_config_t slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                                         I2S_SLOT_MODE_MONO);
    slot_cfg.slot_mask = I2S_STD_SLOT_LEFT;

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(sample_rate_hz),
        .slot_cfg = slot_cfg,
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = DAY_PIN_MIC_SCK,
            .ws = DAY_PIN_MIC_WS,
            .dout = I2S_GPIO_UNUSED,
            .din = DAY_PIN_MIC_SD,
        },
    };
    return i2s_channel_init_std_mode(s_rx_chan, &std_cfg);
}

esp_err_t day_audio_init(uint32_t sample_rate_hz)
{
    if (s_rx_chan && s_sample_rate == sample_rate_hz) {
        return ESP_OK;
    }
    day_audio_deinit();
    esp_err_t ret = configure_channel(sample_rate_hz);
    s_sample_rate = ret == ESP_OK ? sample_rate_hz : 0;
    s_status.initialized = ret == ESP_OK;
    s_status.sample_rate_hz = sample_rate_hz;
    s_status.last_error = ret;
    return ret;
}

esp_err_t day_audio_start(uint32_t sample_rate_hz)
{
    esp_err_t ret = day_audio_init(sample_rate_hz);
    if (ret == ESP_OK && !s_rx_enabled) {
        ret = i2s_channel_enable(s_rx_chan);
    }
    s_rx_enabled = ret == ESP_OK;
    s_status.last_error = ret;
    return ret;
}

esp_err_t day_audio_read_pcm16(int16_t *samples, size_t sample_count, size_t *out_samples, uint32_t timeout_ms)
{
    if (!samples || !out_samples || sample_count == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_samples = 0;
    if (!s_rx_chan) {
        return ESP_ERR_INVALID_STATE;
    }

    int32_t raw[256];
    size_t want = sample_count > 256 ? 256 : sample_count;
    size_t bytes_read = 0;
    esp_err_t ret = i2s_channel_read(s_rx_chan, raw, want * sizeof(raw[0]), &bytes_read, timeout_ms);
    if (ret != ESP_OK) {
        s_status.last_error = ret;
        return ret;
    }
    size_t count = bytes_read / sizeof(raw[0]);
    float sum_sq = 0.0f;
    float peak = 0.0f;
    for (size_t i = 0; i < count; ++i) {
        int32_t shifted = raw[i] >> 14;
        if (shifted > INT16_MAX) {
            shifted = INT16_MAX;
        } else if (shifted < INT16_MIN) {
            shifted = INT16_MIN;
        }
        samples[i] = (int16_t)shifted;
        float normalized = (float)samples[i] / 32768.0f;
        sum_sq += normalized * normalized;
        if (fabsf(normalized) > peak) {
            peak = fabsf(normalized);
        }
    }
    *out_samples = count;
    if (count > 0) {
        s_status.rms = sqrtf(sum_sq / (float)count);
        s_status.peak = peak;
    }
    s_status.last_error = ESP_OK;
    return ESP_OK;
}

esp_err_t day_audio_stop(void)
{
    if (!s_rx_chan || !s_rx_enabled) {
        return ESP_OK;
    }
    esp_err_t ret = i2s_channel_disable(s_rx_chan);
    if (ret == ESP_OK) {
        s_rx_enabled = false;
    }
    s_status.last_error = ret;
    return ret;
}

void day_audio_deinit(void)
{
    if (s_rx_chan) {
        ESP_ERROR_CHECK_WITHOUT_ABORT(day_audio_stop());
        ESP_ERROR_CHECK_WITHOUT_ABORT(i2s_del_channel(s_rx_chan));
        s_rx_chan = NULL;
    }
    s_rx_enabled = false;
    s_sample_rate = 0;
    s_status.initialized = false;
}

day_audio_status_t day_audio_get_status(void)
{
    return s_status;
}

static void write_le16(FILE *file, uint16_t value)
{
    fputc(value & 0xff, file);
    fputc((value >> 8) & 0xff, file);
}

static void write_le32(FILE *file, uint32_t value)
{
    fputc(value & 0xff, file);
    fputc((value >> 8) & 0xff, file);
    fputc((value >> 16) & 0xff, file);
    fputc((value >> 24) & 0xff, file);
}

esp_err_t day_audio_write_wav_header(FILE *file, uint32_t sample_rate_hz, uint32_t data_bytes)
{
    if (!file) {
        return ESP_ERR_INVALID_ARG;
    }
    fwrite("RIFF", 1, 4, file);
    write_le32(file, 36 + data_bytes);
    fwrite("WAVEfmt ", 1, 8, file);
    write_le32(file, 16);
    write_le16(file, 1);
    write_le16(file, 1);
    write_le32(file, sample_rate_hz);
    write_le32(file, sample_rate_hz * 2);
    write_le16(file, 2);
    write_le16(file, 16);
    fwrite("data", 1, 4, file);
    write_le32(file, data_bytes);
    return ferror(file) ? ESP_FAIL : ESP_OK;
}

esp_err_t day_audio_patch_wav_header(FILE *file, uint32_t sample_rate_hz, uint32_t data_bytes)
{
    if (!file) {
        return ESP_ERR_INVALID_ARG;
    }
    fflush(file);
    fseek(file, 0, SEEK_SET);
    esp_err_t ret = day_audio_write_wav_header(file, sample_rate_hz, data_bytes);
    fflush(file);
    fseek(file, 0, SEEK_END);
    return ret;
}
