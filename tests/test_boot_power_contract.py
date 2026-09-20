import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
read = lambda p: (ROOT / p).read_text(encoding="utf-8")


class BootPowerContractTests(unittest.TestCase):
    def test_wifi_budget_portal_and_usb_preemption(self):
        wifi = read("main/src/net/wifi_portal.c")
        app = read("main/src/app_core.c")
        self.assertIn("cfg->wifi_ssid[0] == '\\0'", wifi)
        self.assertIn("6000000LL", wifi)
        self.assertIn("pdMS_TO_TICKS(100)", wifi)
        self.assertIn("day_usb_link_maintenance_active()", wifi)
        self.assertIn("client_present", wifi)
        self.assertIn("deadline_us = now_us + (int64_t)window_ms * 1000", wifi)
        self.assertNotIn("300000000LL", wifi)
        self.assertNotIn("30000000LL", wifi)
        self.assertIn("day_wifi_run_portal_window(10000)", app)

    def test_usb_only_starts_on_cold_boot_and_stops_before_recording(self):
        app = read("main/src/app_core.c")
        defaults = read("sdkconfig.defaults")
        cold_start = app.index("if (cold_boot) {")
        usb_start = app.index("day_usb_link_start_serial_mode()", cold_start)
        board_start = app.index("day_board_init()", usb_start)
        self.assertLess(cold_start, usb_start)
        self.assertLess(usb_start, board_start)
        self.assertLess(app.index("day_usb_link_stop()"), app.index("record_once_cb()", app.index("void day_app_run")))
        self.assertIn("CONFIG_ESP_CONSOLE_NONE=y", defaults)
        self.assertNotIn("CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y", defaults)

    def test_low_battery_latch_is_crc_protected_and_poweron_only_clear(self):
        power = read("main/src/power/power_manager.c")
        for token in ("RTC_NOINIT_ATTR", "DAY_POWER_LATCH_MAGIC", "DAY_POWER_LATCH_SCHEMA",
                      "latch_crc", "ESP_RST_POWERON", "ESP_SLEEP_WAKEUP_ALL"):
            self.assertIn(token, power)
        self.assertNotIn("ESP_RST_SW", power)
        self.assertNotIn("ESP_RST_TASK_WDT", power)

    def test_post_record_policy_uses_availability_and_strict_thresholds(self):
        app = read("main/src/app_core.c")
        self.assertIn("automatic_wakeup", app)
        self.assertIn("battery.available", app)
        self.assertIn("battery.last_error", app)
        self.assertIn("battery.soc_percent < s_config.low_battery_percent", app)
        self.assertIn("battery.soc_percent < 40.0f", app)
        self.assertNotIn("soc_percent > 0.1f", app)
        self.assertIn("day_power_latch_low_battery", app)

    def test_disabled_auto_record_does_not_enable_timer(self):
        power = read("main/src/power/power_manager.c")
        timer = power.index("esp_sleep_enable_timer_wakeup")
        guard = power.rfind("if (cfg->auto_record_enabled)", 0, timer)
        self.assertGreaterEqual(guard, 0)


if __name__ == "__main__":
    unittest.main()
