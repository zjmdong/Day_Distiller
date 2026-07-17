from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


FRAME_SIZES: tuple[tuple[str, int], ...] = (
    ("VGA 640×480", 10),
    ("SVGA 800×600", 11),
    ("HD 1280×720", 13),
    ("UXGA 1600×1200", 15),
    ("FHD 1920×1080", 16),
)
JPEG_QUALITIES: tuple[tuple[str, int], ...] = (
    ("高画质", 10),
    ("标准", 12),
    ("轻量", 18),
    ("省空间", 25),
)
FPS_VALUES = (5, 10, 12, 15, 20, 24, 30)
MAX_FPS_BY_FRAME_SIZE = {10: 30, 11: 20, 13: 15, 15: 10, 16: 15}
TIMEZONES: tuple[tuple[str, str], ...] = (
    ("UTC+8 中国标准时间", "CST-8"),
    ("UTC", "UTC0"),
    ("UTC+9 日本标准时间", "JST-9"),
    ("UTC-5", "EST5"),
    ("UTC-8", "PST8"),
)
WAKE_INTERVALS: tuple[tuple[str, int], ...] = (
    ("1 分钟", 60),
    ("5 分钟", 300),
    ("10 分钟", 600),
    ("15 分钟", 900),
    ("30 分钟", 1800),
    ("1 小时", 3600),
)
LOW_BATTERY_VALUES = (5, 10, 15, 20, 25, 30, 35, 39)
DEFAULT_RECORDING_COLOR = "#FF3000"
COLOR_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}")


def _section(value: dict[str, Any], name: str) -> dict[str, Any]:
    section = value.get(name)
    return section if isinstance(section, dict) else {}


def _value(section: dict[str, Any], root: dict[str, Any], key: str, *aliases: str, default: Any) -> Any:
    for name in (key, *aliases):
        if name in section:
            return section[name]
        if name in root:
            return root[name]
    return default


@dataclass(frozen=True)
class DeviceSettingsSnapshot:
    schema_version: int
    revision: int
    record_framesize: int
    jpeg_quality: int
    record_fps: int
    wifi_ssid: str
    wifi_password: str | None
    wifi_password_set: bool
    timezone: str
    ntp_server: str
    wake_interval_sec: int
    auto_record_enabled: bool
    low_battery_percent: int
    led_brightness_percent: int
    recording_color: str

    @classmethod
    def from_payload(cls, value: dict[str, Any]) -> "DeviceSettingsSnapshot":
        video = _section(value, "video")
        wifi = _section(value, "wifi")
        time = _section(value, "time")
        system = _section(value, "system")
        led = _section(value, "led")
        password_value = _value(wifi, value, "password", "wifi_password", default=None)
        password = str(password_value) if password_value is not None else None
        return cls(
            schema_version=int(value.get("schema_version", 1)),
            revision=max(0, int(value.get("revision", 0))),
            record_framesize=int(
                _value(video, value, "record_framesize", "camera_record_framesize", default=16)
            ),
            jpeg_quality=int(
                _value(video, value, "jpeg_quality", "camera_jpeg_quality", default=12)
            ),
            record_fps=int(
                _value(video, value, "record_fps", "camera_record_fps", default=15)
            ),
            wifi_ssid=str(_value(wifi, value, "ssid", "wifi_ssid", default="")),
            wifi_password=password,
            wifi_password_set=bool(
                _value(wifi, value, "password_set", "wifi_password_set", default=password is not None)
            ),
            timezone=str(_value(time, value, "timezone", default="CST-8")),
            ntp_server=str(
                _value(time, value, "ntp_server", default="ntp1.aliyun.com")
            ),
            wake_interval_sec=int(
                _value(system, value, "wake_interval_sec", default=300)
            ),
            auto_record_enabled=bool(
                _value(system, value, "auto_record_enabled", default=True)
            ),
            low_battery_percent=int(
                _value(system, value, "low_battery_percent", default=20)
            ),
            led_brightness_percent=int(
                _value(led, value, "brightness_percent", default=100)
            ),
            recording_color=normalize_color(
                str(_value(led, value, "recording_color", default=DEFAULT_RECORDING_COLOR))
            ),
        )


def normalize_color(value: str) -> str:
    candidate = value.strip().upper()
    if not COLOR_PATTERN.fullmatch(candidate):
        raise ValueError("录制灯颜色必须是 #RRGGBB 格式")
    if candidate == "#000000":
        raise ValueError("录制灯颜色不能为纯黑")
    return candidate


def validate_settings(snapshot: DeviceSettingsSnapshot) -> None:
    if snapshot.record_framesize not in MAX_FPS_BY_FRAME_SIZE:
        raise ValueError("录像分辨率不受支持")
    if not 4 <= snapshot.jpeg_quality <= 63:
        raise ValueError("录像质量必须在 4–63 之间")
    if snapshot.record_fps not in FPS_VALUES:
        raise ValueError("录像帧率不受支持")
    if snapshot.record_fps > MAX_FPS_BY_FRAME_SIZE[snapshot.record_framesize]:
        raise ValueError("当前录像分辨率不支持所选帧率")
    if len(snapshot.wifi_ssid.encode("utf-8")) > 32:
        raise ValueError("Wi-Fi 名称不能超过 32 字节")
    if snapshot.wifi_password is not None and len(snapshot.wifi_password.encode("utf-8")) > 63:
        raise ValueError("Wi-Fi 密码不能超过 63 字节")
    if not 1 <= len(snapshot.timezone.encode("utf-8")) <= 32:
        raise ValueError("时区格式无效")
    if not 1 <= len(snapshot.ntp_server.encode("utf-8")) <= 64:
        raise ValueError("NTP 服务器格式无效")
    if any(char.isspace() or ord(char) < 0x20 for char in snapshot.ntp_server):
        raise ValueError("NTP 服务器只能填写主机名或 IP")
    if not 60 <= snapshot.wake_interval_sec <= 86400:
        raise ValueError("自动唤醒间隔必须在 1 分钟到 24 小时之间")
    if not 5 <= snapshot.low_battery_percent <= 39:
        raise ValueError("低电量阈值必须在 5%–39% 之间")
    if not 5 <= snapshot.led_brightness_percent <= 100:
        raise ValueError("RGB 灯亮度不能低于 5%")
    normalize_color(snapshot.recording_color)


def build_patch(snapshot: DeviceSettingsSnapshot, *, include_password: bool = True) -> dict[str, Any]:
    validate_settings(snapshot)
    wifi: dict[str, Any] = {"ssid": snapshot.wifi_ssid}
    if include_password and snapshot.wifi_password is not None:
        wifi["password"] = snapshot.wifi_password
    return {
        "video": {
            "record_framesize": snapshot.record_framesize,
            "jpeg_quality": snapshot.jpeg_quality,
            "record_fps": snapshot.record_fps,
        },
        "wifi": wifi,
        "time": {
            "timezone": snapshot.timezone,
            "ntp_server": snapshot.ntp_server,
        },
        "system": {
            "wake_interval_sec": snapshot.wake_interval_sec,
            "auto_record_enabled": snapshot.auto_record_enabled,
            "low_battery_percent": snapshot.low_battery_percent,
        },
        "led": {
            "brightness_percent": snapshot.led_brightness_percent,
            "recording_color": normalize_color(snapshot.recording_color),
        },
    }
