#include "device_identity.h"

#include <stdbool.h>
#include <inttypes.h>
#include <stdio.h>
#include "esp_mac.h"

static char s_device_id[DAY_DEVICE_ID_MAX] = "DD-000000000000";
static bool s_initialized;

esp_err_t day_device_identity_init(void)
{
    if (s_initialized) {
        return ESP_OK;
    }
    uint8_t mac[6] = {0};
    esp_err_t ret = esp_efuse_mac_get_default(mac);
    if (ret != ESP_OK) {
        return ret;
    }
    int written = snprintf(s_device_id, sizeof(s_device_id),
                           "DD-%02X%02X%02X%02X%02X%02X",
                           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    if (written != sizeof(s_device_id) - 1) {
        return ESP_FAIL;
    }
    s_initialized = true;
    return ESP_OK;
}

const char *day_device_id(void)
{
    (void)day_device_identity_init();
    return s_device_id;
}

const char *day_device_usb_serial(void)
{
    return day_device_id();
}
