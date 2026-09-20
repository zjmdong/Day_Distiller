import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class RuntimeRecoveryWebLedContractTests(unittest.TestCase):
    def test_tinyusb_is_stopped_before_every_deep_sleep(self):
        link = read("main/src/usb/usb_link.c")
        power = read("main/src/power/power_manager.c")
        self.assertIn("esp_err_t day_usb_link_stop(void)", link)
        self.assertIn("tinyusb_cdcacm_deinit(TINYUSB_CDC_ACM_1)", link)
        self.assertIn("tinyusb_cdcacm_deinit(TINYUSB_CDC_ACM_0)", link)
        self.assertIn("tinyusb_driver_uninstall()", link)
        self.assertEqual(power.count("stop_usb_before_sleep();"), 2)
        self.assertLess(power.index("stop_usb_before_sleep();"), power.index("esp_deep_sleep_start();"))

    def test_rgb_is_cleared_deinitialized_and_held_low_before_sleep(self):
        led = read("main/src/drivers/led_status.c")
        power = read("main/src/power/power_manager.c")
        sleep = led[led.index("esp_err_t day_led_prepare_for_sleep") :]
        for token in (
            "led_strip_clear",
            "led_strip_del",
            "gpio_set_level(DAY_PIN_RGB_DIN, 0)",
            "gpio_hold_en(DAY_PIN_RGB_DIN)",
            "gpio_deep_sleep_hold_en()",
        ):
            self.assertIn(token, sleep)
        self.assertIn("day_led_prepare_for_sleep()", power)

    def test_storage_mount_failure_rolls_back_bus_and_retries_without_format(self):
        storage = read("main/src/storage/storage_service.c")
        self.assertIn("DAY_STORAGE_MOUNT_ATTEMPTS 3", storage)
        self.assertIn("release_storage_bus();", storage)
        self.assertIn("format_if_mount_failed = false", storage)
        self.assertIn("for (int attempt = 1; attempt <= DAY_STORAGE_MOUNT_ATTEMPTS; ++attempt)", storage)
        self.assertNotIn("unlink(", storage[: storage.index("esp_err_t day_storage_make_record_paths")])

    def test_disabled_shake_wake_disables_gpio_wakeup(self):
        imu = read("main/src/drivers/imu.c")
        function = imu[imu.index("esp_err_t day_imu_configure_shake_wake") :]
        self.assertIn("enabled ? gpio_wakeup_enable", function)
        self.assertIn(": gpio_wakeup_disable", function)

    def test_web_exposes_led_settings_without_wifi_secret(self):
        server = read("main/src/web/web_server.c")
        page = read("main/web/index.html")
        config_json = server[server.index("static void config_to_json") : server.index("static esp_err_t config_get_handler")]
        self.assertIn('\"led_brightness_percent\"', server)
        self.assertIn('\"led_recording_color\"', server)
        self.assertIn("wifi_password_set", config_json)
        self.assertNotIn("wifi_password\\\":\\\"%s", config_json)
        for element_id in (
            'id="ledBrightness"',
            'id="ledRecordingColor"',
            'id="previewLedBtn"',
            'id="saveLedBtn"',
        ):
            self.assertIn(element_id, page)
        self.assertIn('led_brightness_percent: +$("ledBrightness").value', page)
        self.assertIn('led_recording_color: $("ledRecordingColor").value.toUpperCase()', page)
        self.assertIn('/api/led/preview', server)
        self.assertIn('day_led_preview', server)

    def test_camera_is_only_initialized_on_record_or_preview_and_reset_when_idle(self):
        app = read("main/src/app_core.c")
        status = read("main/src/device/device_status.c")
        camera = read("main/src/media/camera_service.c")
        recorder = read("main/src/media/recorder.c")
        board = read("main/src/board/board.c")
        self.assertNotIn("day_camera_init", app)
        self.assertNotIn("day_camera_init", status)
        self.assertIn("day_camera_init_record(cfg)", recorder)
        self.assertIn("day_camera_init_preview(s_cb.config)", read("main/src/web/web_server.c"))
        self.assertIn("day_camera_deinit();", recorder)
        self.assertIn("hold_camera_in_reset();", camera)
        self.assertIn("day_camera_prepare_for_sleep", camera)
        self.assertIn("gpio_hold_en(DAY_PIN_CAM_RST)", camera)
        self.assertIn("gpio_set_level(DAY_PIN_CAM_RST, 0)", board)
        self.assertIn(".pin_pwdn = -1", camera)


if __name__ == "__main__":
    unittest.main()
