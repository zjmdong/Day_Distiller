from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from day_distiller_client.device import UsbLinkDevice, device_profile
from day_distiller_client.device_settings import DeviceSettingsSnapshot, build_patch, validate_settings
from day_distiller_client.protocol import Command


def _payload() -> dict:
    return {
        "schema_version": 1,
        "revision": 7,
        "video": {"record_framesize": 13, "jpeg_quality": 12, "record_fps": 15},
        "wifi": {"ssid": "Home WiFi", "password_set": True},
        "time": {"timezone": "CST-8", "ntp_server": "ntp1.aliyun.com"},
        "system": {
            "wake_interval_sec": 600,
            "auto_record_enabled": True,
            "low_battery_percent": 20,
        },
        "led": {"brightness_percent": 35, "recording_color": "#00a6ff"},
    }


def test_device_settings_round_trip_omits_unchanged_secret() -> None:
    snapshot = DeviceSettingsSnapshot.from_payload(_payload())
    assert snapshot.recording_color == "#00A6FF"
    assert snapshot.wifi_password is None
    assert snapshot.wifi_password_set
    patch = build_patch(snapshot, include_password=False)
    assert patch["wifi"] == {"ssid": "Home WiFi"}
    assert patch["system"]["wake_interval_sec"] == 600


def test_device_settings_enforce_led_floor_and_resolution_fps_pair() -> None:
    values = _payload()
    values["led"]["brightness_percent"] = 4
    with pytest.raises(ValueError, match="5%"):
        validate_settings(DeviceSettingsSnapshot.from_payload(values))

    values = _payload()
    values["video"] = {"record_framesize": 15, "jpeg_quality": 12, "record_fps": 30}
    with pytest.raises(ValueError, match="帧率"):
        validate_settings(DeviceSettingsSnapshot.from_payload(values))


def test_firmware_capabilities_gate_settings_without_version_guessing() -> None:
    profile = device_profile(
        {
            "firmware_version": "2.1.0",
            "protocol": 1,
            "capabilities": ["device_status_v2", "device_config_v1", "device_config_write", "led_preview"],
        }
    )
    assert profile.supports_device_status
    assert profile.supports_device_settings
    assert profile.supports("device_config_write")
    assert profile.supports("led_preview")

    legacy = device_profile({"firmware_version": "1.8.0", "protocol": 1})
    assert not legacy.supports_device_settings


def test_usb_device_2_1_payloads_match_contract() -> None:
    device = UsbLinkDevice("COM9")
    response = MagicMock()
    response.payload_json.return_value = {"ok": True}
    device.request = MagicMock(return_value=response)

    device.get_config(include_secrets=True)
    device.request.assert_called_with(Command.GET_CONFIG, {"include_secrets": True}, timeout=5.0)

    patch = {"led": {"brightness_percent": 35, "recording_color": "#00A6FF"}}
    device.set_config(patch, expected_revision=7)
    device.request.assert_called_with(
        Command.SET_CONFIG,
        {"patch": patch, "expected_revision": 7},
        timeout=8.0,
    )

    device.preview_led("#00A6FF", 35, 2000)
    device.request.assert_called_with(
        Command.PREVIEW_LED,
        {"color": "#00A6FF", "brightness_percent": 35, "duration_ms": 2000},
        timeout=5.0,
    )
