import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace : index + 1]
    raise AssertionError(f"unterminated function {signature}")


class SettingsStatusContractTests(unittest.TestCase):
    def test_legacy_nvs_keys_migrate_with_revision_and_led_defaults(self):
        source = read("main/src/config/device_settings.c")
        for key in (
            "auto_rec", "shake", "wake_s", "cam_fs", "prev_fs", "rec_fs",
            "cam_q", "prev_fps", "rec_fps", "aud_rate", "imu_rate",
            "imu_orient", "low_batt", "ntp", "tz", "ssid", "pass",
        ):
            self.assertIn(f'"{key}"', source)
        for key in ("cfg_schema", "cfg_revision", "led_bri", "led_rec_r", "led_rec_g", "led_rec_b"):
            self.assertIn(f'"{key}"', source)
        self.assertIn("DAY_SETTINGS_NVS_SCHEMA_VERSION 2", read("main/include/device_settings.h"))
        self.assertIn("DAY_LED_DEFAULT_BRIGHTNESS_PERCENT 100", read("main/include/day_types.h"))

    def test_settings_commit_updates_memory_only_after_nvs_commit_succeeds(self):
        source = read("main/src/config/device_settings.c")
        persist = function_body(source, "static esp_err_t persist_config")
        self.assertIn("nvs_commit(nvs)", persist)
        self.assertNotIn("ESP_ERROR_CHECK_WITHOUT_ABORT", persist)
        replace = function_body(source, "esp_err_t day_settings_replace")
        self.assertLess(replace.index("persist_config(candidate, next_revision)"),
                        replace.index("s_settings.config = *candidate"))
        self.assertIn("revision_conflict", replace)

    def test_strict_settings_boundaries_and_secret_byte_limits_are_enforced(self):
        source = read("main/src/config/device_settings.c")
        for fragment in (
            "config->wake_interval_sec < 60",
            "config->wake_interval_sec > 86400",
            "config->low_battery_percent < 5",
            "config->low_battery_percent >= 40",
            "config->led_brightness_percent < 5",
            "config->led_brightness_percent > 100",
            "utf8_valid(config->wifi_ssid, DAY_WIFI_SSID_MAX",
            "utf8_valid(config->wifi_password, 63",
            "color_must_be_visible",
            "unsupported_for_framesize",
        ):
            self.assertIn(fragment, source)

    def test_status_api_only_copies_cache_and_never_touches_drivers(self):
        source = read("main/src/device/device_status.c")
        snapshot = function_body(source, "esp_err_t day_status_get_snapshot")
        for forbidden in (
            "day_battery_read", "day_rtc_read", "day_storage_refresh_status",
            "day_audio_start", "day_audio_read_pcm16", "day_imu_read", "day_camera_init",
        ):
            self.assertNotIn(forbidden, snapshot)
        app = read("main/src/app_core.c")
        collect = function_body(app, "static esp_err_t collect_status")
        self.assertEqual(collect.count("day_status_get_snapshot"), 1)
        self.assertNotIn("day_audio_start", collect)
        self.assertNotIn("day_imu_read", collect)

    def test_storage_snapshot_no_longer_scans_fat_on_read(self):
        source = read("main/src/storage/storage_service.c")
        getter = function_body(source, "day_storage_status_t day_storage_get_status")
        self.assertIn("return s_status", getter)
        self.assertNotIn("day_storage_refresh_status", getter)
        self.assertNotIn("esp_vfs_fat_info", getter)

    def test_background_refresh_is_bounded_and_pauses_during_recording(self):
        source = read("main/src/device/device_status.c")
        self.assertIn("#define BATTERY_REFRESH_US 2000000LL", source)
        self.assertIn("#define RTC_REFRESH_US 1000000LL", source)
        task = function_body(source, "static void status_task")
        self.assertIn("bool recording = day_recorder_is_active()", task)
        self.assertGreaterEqual(task.count("!recording"), 2)
        self.assertIn("uxTaskGetStackHighWaterMark", task)

    def test_wifi_password_never_appears_in_log_statements(self):
        sources = "\n".join(
            read(path)
            for path in (
                "main/src/config/device_settings.c",
                "main/src/net/wifi_portal.c",
                "main/src/app_core.c",
            )
        )
        log_calls = re.findall(r"ESP_LOG[A-Z]+\([^;]+;", sources, flags=re.DOTALL)
        for call in log_calls:
            self.assertNotRegex(call.lower(), r"password|wifi_password|\bpass\b")


if __name__ == "__main__":
    unittest.main()
