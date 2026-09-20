import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_H = ROOT / "main" / "include" / "usb_protocol.h"
PROTOCOL_C = ROOT / "main" / "src" / "usb" / "usb_protocol.c"
LINK_C = ROOT / "main" / "src" / "usb" / "usb_link.c"
SETTINGS_C = ROOT / "main" / "src" / "config" / "device_settings.c"


class UsbConfigStatusV21ContractTests(unittest.TestCase):
    def test_version_and_command_ids_are_additive(self):
        header = PROTOCOL_H.read_text(encoding="utf-8")
        self.assertIn('#define DAY_USB_FIRMWARE_VERSION "2.1.1"', header)
        expected = {
            "HELLO": 1,
            "PING": 2,
            "GET_STATUS": 3,
            "ENTER_MSC": 4,
            "EXIT_MSC": 5,
            "BEGIN_EXPORT": 6,
            "COMMIT_EXPORT_DELETE": 7,
            "ABORT_EXPORT": 8,
            "END_SESSION": 9,
            "GET_EXPORT_STATUS": 10,
            "LIST_RECORD_DATES": 11,
            "GET_CONFIG": 12,
            "SET_CONFIG": 13,
            "PREVIEW_LED": 14,
        }
        for name, command_id in expected.items():
            self.assertRegex(
                header,
                rf"DAY_USB_CMD_{name}\s*=\s*{command_id}\s*,",
            )

    def test_status_is_cache_only_and_secret_free(self):
        source = LINK_C.read_text(encoding="utf-8")
        details = source[
            source.index("static bool add_status_details") :
            source.index("static char *make_config_payload")
        ]
        request_case = source[
            source.index("case DAY_USB_CMD_GET_STATUS") :
            source.index("case DAY_USB_CMD_ENTER_MSC")
        ]
        self.assertIn("day_status_get_snapshot(&snapshot)", details)
        for forbidden in (
            "day_battery_read",
            "day_rtc_read",
            "day_camera_init",
            "day_audio_init",
            "day_imu_init",
            "day_storage_refresh",
            "esp_vfs_fat_info",
            "wifi_password",
        ):
            self.assertNotIn(forbidden, details)
            self.assertNotIn(forbidden, request_case)
        self.assertIn('json_add_string(wifi, "ssid"', details)
        self.assertNotRegex(details, r'json_add_string\([^\n]*"password"')

    def test_hello_negotiates_capabilities_and_status_uses_bounded_details(self):
        source = LINK_C.read_text(encoding="utf-8")
        self.assertIn("(!detailed && !add_capabilities(root))", source)
        for capability in (
            "device_status_v2",
            "battery_status",
            "rtc_status",
            "wifi_status",
            "power_status",
            "device_config_v1",
            "device_config_write",
            "config_secrets_over_usb",
            "led_settings",
        ):
            self.assertIn(f'"{capability}"', source)
        self.assertIn('"led_preview"', source)
        self.assertIn("strlen(payload) > DAY_USB_PAYLOAD_MAX", source)
        self.assertIn("cJSON_free(payload)", source)

    def test_worst_case_detailed_status_stays_below_payload_limit(self):
        # GET_STATUS omits the capability array already negotiated by HELLO.  This
        # is what permits all documented status objects and maximum-length text to
        # fit without truncation in the frozen 1600-byte v1 payload.
        payload = {
            "protocol": 1,
            "firmware_version": "2.1.0",
            "device": "Day Distiller",
            "device_id": "F" * 16,
            "mode": "serial",
            "runtime_state": "serial_maintenance",
            "session_id": "F" * 16,
            "maintenance": True,
            "recording": True,
            "usb_full_speed": True,
            "metadata_schemas": [1, 2],
            "active_exports": 32,
            "storage": {
                "ready": True,
                "mounted": True,
                "usb_exposed": False,
                "total_bytes": 18446744073709551615,
                "free_bytes": 18446744073709551615,
                "last_error": -2147483648,
                "available": True,
                "sample_age_ms": 4294967295,
            },
            "status_schema": 2,
            "config_revision": 4294967295,
            "snapshot_monotonic_ms": 18446744073709551615,
            "last_record_error": -2147483648,
            "battery": {
                "available": True,
                "soc_percent": 100.123456,
                "voltage_v": 4.123456,
                "charge_state": "not_charging",
                "confidence": 100,
                "sample_age_ms": 4294967295,
                "last_error": -2147483648,
            },
            "rtc": {
                "available": True,
                "valid": True,
                "unix_time": 4102444799,
                "iso8601": "2099-12-31T23:59:59+08:00",
                "sample_age_ms": 4294967295,
                "last_error": -2147483648,
            },
            "clock": {
                "system_valid": True,
                "timezone": "X" * 32,
                "source": "X" * 15,
                "last_sync_source": "X" * 15,
                "last_sync_unix": 4102444799,
            },
            "wifi": {
                "available": True,
                "configured": True,
                "sta_connected": True,
                "ap_running": True,
                "ap_clients": 2147483647,
                "time_synced": True,
                "ssid": "界" * 10 + "AB",
                "ip": "255.255.255.255",
                "rssi": None,
                "last_error": -2147483648,
            },
            "power": {
                "wake_reason": "X" * 19,
                "reset_reason": "X" * 19,
                "low_battery_latched": True,
                "timer_wake_enabled": True,
                "next_wake_sec": 86400,
            },
            "led": {
                "mode": "X" * 23,
                "brightness_percent": 100,
                "recording_color": "#FFFFFF",
            },
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLess(len(encoded), 1600, len(encoded))

    def test_get_config_secret_gating_and_patch_atomicity_are_explicit(self):
        link = LINK_C.read_text(encoding="utf-8")
        protocol = PROTOCOL_C.read_text(encoding="utf-8")
        settings = SETTINGS_C.read_text(encoding="utf-8")
        config_builder = link[
            link.index("static char *make_config_payload") :
            link.index("static char *make_set_config_payload")
        ]
        self.assertIn("include_secrets &&", config_builder)
        self.assertIn('"password_set"', config_builder)
        self.assertIn('"password"', config_builder)
        self.assertIn("day_recorder_is_active()", link)
        self.assertIn("DAY_USB_STATUS_BUSY", link)
        self.assertIn('"expected_revision"', protocol)
        self.assertIn('"patch"', protocol)
        self.assertIn('"duplicate_field"', protocol)
        self.assertIn('"unknown_field"', protocol)
        self.assertIn('"empty_patch"', protocol)
        commit_at = settings.index("result = nvs_commit(nvs)")
        memory_at = settings.index("s_settings.config = *candidate")
        self.assertLess(commit_at, memory_at)
        self.assertIn('"revision_conflict"', settings)

    def test_config_responses_fit_with_maximum_utf8_and_secret_lengths(self):
        config = {
            "schema_version": 1,
            "revision": 4294967295,
            "video": {
                "record_framesize": 13,
                "jpeg_quality": 63,
                "record_fps": 30,
                "preview_framesize": 13,
                "preview_fps": 30,
            },
            "wifi": {
                "ssid": "界" * 10 + "AB",
                "password_set": True,
                "password": "P" * 63,
            },
            "time": {"timezone": "X" * 32, "ntp_server": "N" * 64},
            "system": {
                "wake_interval_sec": 86400,
                "auto_record_enabled": True,
                "shake_trigger_enabled": True,
                "low_battery_percent": 39,
            },
            "led": {"brightness_percent": 100, "recording_color": "#FFFFFF"},
            "audio": {"sample_rate_hz": 48000},
            "imu": {"sample_rate_hz": 833, "orientation": 5},
        }
        encoded = json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLess(len(encoded), 1600, len(encoded))


if __name__ == "__main__":
    unittest.main()
