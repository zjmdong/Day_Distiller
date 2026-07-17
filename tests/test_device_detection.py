from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from day_distiller_client.device import (
    UsbLinkDevice,
    device_profile,
    find_device,
    list_serial_ports,
)
from day_distiller_client.export_adapter import select_export_adapter


def _port(device: str, hwid: str, pid: int, serial: str) -> SimpleNamespace:
    return SimpleNamespace(
        device=device,
        description=f"USB 串行设备 ({device})",
        hwid=hwid,
        vid=0x303A,
        pid=pid,
        serial_number=serial,
        interface="USB 串行设备",
        location=None,
    )


def test_serial_mode_protocol_interface_is_ranked_before_log_interface() -> None:
    ports = [
        _port("COM8", "USB VID:PID=303A:4020 MI_00", 0x4020, "DD-ABC"),
        _port("COM9", "USB VID:PID=303A:4020 MI_02", 0x4020, "DD-ABC"),
    ]
    with patch("serial.tools.list_ports.comports", return_value=ports):
        found = list_serial_ports()

    assert [item.device for item in found] == ["COM9", "COM8"]
    assert found[0].role == "protocol"
    assert found[1].role == "log"


def test_msc_mode_interface_zero_is_protocol() -> None:
    ports = [_port("COM7", "USB VID:PID=303A:4021 MI_00", 0x4021, "DD-ABC")]
    with patch("serial.tools.list_ports.comports", return_value=ports):
        found = list_serial_ports()

    assert found[0].role == "protocol"


def test_auto_discovery_skips_firmware_log_cdc() -> None:
    log_port = _port("COM8", "USB VID:PID=303A:4020 MI_00", 0x4020, "DD-ABC")
    protocol_port = _port("COM9", "USB VID:PID=303A:4020 MI_02", 0x4020, "DD-ABC")
    with patch("serial.tools.list_ports.comports", return_value=[log_port, protocol_port]), patch(
        "day_distiller_client.device.UsbLinkDevice"
    ) as device_type:
        device_type.return_value.__enter__.return_value.hello.return_value = {
            "protocol": 1,
            "device": "Day Distiller",
        }
        found = find_device()

    assert found is not None
    assert found[0].device == "COM9"
    device_type.assert_called_once_with("COM9", timeout=1.1)


def test_serial_open_retries_transient_windows_cdc_error() -> None:
    handle = MagicMock()
    with patch(
        "serial.Serial",
        side_effect=[PermissionError(13, "ClearCommError failed"), handle],
    ), patch("day_distiller_client.device.time.sleep"):
        device = UsbLinkDevice("COM9")
        device.open()
        device.close()

    handle.reset_input_buffer.assert_called_once_with()
    handle.close.assert_called_once_with()


def test_profile_selects_transactional_adapter_from_capability() -> None:
    port = _port("COM9", "USB VID:PID=303A:4020 MI_02", 0x4020, "DD-USB-123")
    with patch("serial.tools.list_ports.comports", return_value=[port]):
        candidate = list_serial_ports()[0]
    profile = device_profile(
        {
            "protocol": 1,
            "firmware_version": "2.0.0",
            "device_id": "DD-A1B2C3D4E5F6",
            "capabilities": ["transactional_export_v2", "list_record_dates"],
        },
        candidate,
    )

    assert profile.is_firmware_v2
    assert profile.adapter_name == "transactional_export_v2"
    assert profile.firmware_version == "2.0.0"
    assert profile.serial_number == "DD-USB-123"
    assert select_export_adapter(profile).adapter_name == "transactional_export_v2"


def test_profile_keeps_legacy_firmware_compatible() -> None:
    profile = device_profile(
        {
            "protocol": 1,
            "device": "Day Distiller",
            "capabilities": ["enter_msc", "exit_msc"],
        }
    )

    assert not profile.is_firmware_v2
    assert profile.adapter_name == "legacy_msc_v1"
    assert profile.display_firmware == "1.x（旧版）"
    assert profile.serial_number == "DD-USB-LINK"
    assert select_export_adapter(profile).adapter_name == "legacy_msc_v1"
