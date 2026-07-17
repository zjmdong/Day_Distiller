file(GLOB_RECURSE DAY_DISTILLER_SRCS "src/*.c")

idf_component_register(
    SRCS "main.c" ${DAY_DISTILLER_SRCS}
    INCLUDE_DIRS "include"
    EMBED_TXTFILES "web/index.html"
    REQUIRES
        dns_server
        esp32-camera
        led_strip
        nvs_flash
        esp_timer
        esp_event
        esp_netif
        esp_wifi
        esp_http_server
        esp_driver_gpio
        esp_driver_i2c
        esp_driver_i2s
        esp_driver_spi
        esp_driver_sdspi
        esp_tinyusb
        cjson
        fatfs
        mbedtls
        sdmmc
        lwip)
