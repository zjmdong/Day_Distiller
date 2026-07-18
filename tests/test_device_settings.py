from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from day_distiller_client.device import UsbLinkDevice, device_profile
from day_distiller_client.device_settings import (
    DeviceSettingsSnapshot,
    build_patch,
    format_device_status_cards,
    format_interval,
    format_storage_size,
    validate_settings,
)
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


def test_device_settings_ui_keeps_hello_capabilities_after_status_read() -> None:
    app_source = (Path(__file__).parents[1] / "src/day_distiller_client/app.py").read_text(
        encoding="utf-8"
    )
    assert '"profile": profile' in app_source
    assert 'profile = result.get("profile") or device_profile(result.get("hello", status), port)' in app_source


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


def test_device_status_cards_format_protocol_values_for_people() -> None:
    status = {
        "battery": {
            "available": True,
            "soc_percent": 85.0546875,
            "voltage_v": 4.132499969482422,
            "charge_state": "unknown",
        },
        "rtc": {"available": True, "valid": True, "iso8601": "2026-07-18T09:30:00+08:00"},
        "clock": {"timezone": "CST-8", "last_sync_source": "ntp", "last_sync_unix": 1784338200},
        "wifi": {"available": True, "configured": True, "ssid": "Xiaomi_hongmei", "ip": ""},
        "storage": {"ready": True, "total_bytes": 32_000_000_000, "free_bytes": 18_250_000_000},
        "power": {"wake_reason": "other", "timer_wake_enabled": True, "next_wake_sec": 600},
        "led": {"mode": "usb_handshake", "brightness_percent": 35, "recording_color": "#FF3000"},
    }
    cards = format_device_status_cards(
        status,
        firmware_version="2.1.0",
        serial_number="DD-TEST",
        settings=DeviceSettingsSnapshot.from_payload(_payload()),
    )

    assert "固件版本" in cards["identity"] and "序列号" in cards["identity"]
    assert "85.1%" in cards["battery"] and "4.13 V" in cards["battery"]
    assert "unknown" not in " ".join(cards.values())
    assert "other" not in " ".join(cards.values())
    assert "无 IP" not in cards["wifi"] and "Xiaomi_hongmei" in cards["wifi"]
    assert "#FF3000" not in cards["led"] and "●" in cards["led"]
    assert "32.0 GB" in cards["storage"] and "18.2 GB" in cards["storage"]
    assert "✓" in cards["rtc"] and "✓" in cards["clock"]


def test_human_storage_and_wake_interval_units() -> None:
    assert format_storage_size(1_500_000_000) == "1.5 GB"
    assert format_interval(60) == "1 分钟"
    assert format_interval(3600) == "1 小时"


def test_rebooting_msc_commands_accept_only_observed_usb_disconnect() -> None:
    device = UsbLinkDevice("COM8")
    device.request = MagicMock(side_effect=OSError("USB re-enumerating"))
    device._wait_until_port_removed = MagicMock(return_value=True)

    assert device.enter_msc("ro") == {"accepted": True, "rebooting": True}
    assert device.exit_msc(next_mode="maintenance") == {
        "accepted": True,
        "rebooting": True,
    }

    device._wait_until_port_removed.return_value = False
    with pytest.raises(OSError, match="re-enumerating"):
        device.enter_msc("ro")
