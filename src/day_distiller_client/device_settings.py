from __future__ import annotations

from html import escape
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


def _status_icon(ok: bool, success_text: str, failure_text: str) -> str:
    color = "#22C875" if ok else "#FF5A67"
    symbol = "✓" if ok else "✕"
    text = success_text if ok else failure_text
    return (
        f'<span style="color:{color};font-size:17px;font-weight:700">{symbol}</span> '
        f"{escape(text)}"
    )


def _number(value: Any, digits: int) -> str | None:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return None


def format_storage_size(value: Any) -> str:
    try:
        size = max(0, int(value))
    except (TypeError, ValueError):
        return "—"
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(size)
    unit = units[0]
    for unit in units:
        if amount < 1000.0 or unit == units[-1]:
            break
        amount /= 1000.0
    digits = 0 if unit in {"B", "KB"} else 1
    return f"{amount:.{digits}f} {unit}"


def format_interval(seconds: Any) -> str:
    try:
        value = max(0, int(seconds))
    except (TypeError, ValueError):
        return "—"
    if value and value % 3600 == 0:
        return f"{value // 3600} 小时"
    if value and value % 60 == 0:
        return f"{value // 60} 分钟"
    return f"{value} 秒"


def format_device_status_cards(
    status: dict[str, Any],
    *,
    firmware_version: str,
    serial_number: str,
    settings: DeviceSettingsSnapshot | None = None,
) -> dict[str, str]:
    """Return user-facing rich text without leaking raw protocol values."""

    def section(name: str) -> dict[str, Any]:
        value = status.get(name)
        return value if isinstance(value, dict) else {}

    battery = section("battery")
    rtc = section("rtc")
    clock = section("clock")
    wifi = section("wifi")
    storage = section("storage")
    power = section("power")
    led = section("led")

    values: dict[str, str] = {
        "identity": (
            f"固件版本　{escape(firmware_version or '1.x（旧版）')}<br>"
            f"序列号　{escape(serial_number or '—')}"
        )
    }

    if battery and battery.get("available", True) is not False:
        percent = _number(battery.get("soc_percent", battery.get("soc")), 1)
        voltage = _number(battery.get("voltage_v", battery.get("voltage")), 2)
        values["battery"] = (
            f"电量　{percent + '%' if percent is not None else '—'}<br>"
            f"电压　{voltage + ' V' if voltage is not None else '—'}"
        )
    else:
        values["battery"] = "当前不可用"

    if rtc and rtc.get("available", True) is not False:
        values["rtc"] = _status_icon(bool(rtc.get("valid")), "有效", "无效")
    else:
        values["rtc"] = "当前不可用"

    rtc_time = rtc.get("iso8601") or rtc.get("iso") or "未校时"
    timezone = str(clock.get("timezone") or (settings.timezone if settings else "—"))
    timezone_label = {
        "CST-8": "UTC+8（中国标准时间）",
        "UTC0": "UTC",
        "JST-9": "UTC+9（日本标准时间）",
        "EST5": "UTC-5",
        "PST8": "UTC-8",
    }.get(timezone, timezone)
    last_source = str(clock.get("last_sync_source") or "none").lower()
    last_sync_ok = bool(clock.get("last_sync_unix")) and last_source not in {
        "none",
        "untrusted",
        "unknown",
    }
    values["clock"] = (
        f"RTC 时间　{escape(str(rtc_time))}<br>"
        f"时区　{escape(timezone_label)}<br>"
        f"上次对时　{_status_icon(last_sync_ok, '成功', '未成功')}"
    )

    configured = bool(wifi.get("configured"))
    wifi_lines = [f"配置状态　{_status_icon(configured, '已配置', '未配置')}"]
    if configured:
        wifi_lines.append(f"Wi-Fi 名称　{escape(str(wifi.get('ssid') or '—'))}")
    values["wifi"] = "<br>".join(wifi_lines) if wifi else "当前固件未提供"

    if storage:
        ready = bool(storage.get("ready"))
        values["storage"] = (
            f"就绪状态　{_status_icon(ready, '已就绪', '未就绪')}<br>"
            f"总空间　{format_storage_size(storage.get('total_bytes'))}<br>"
            f"剩余空间　{format_storage_size(storage.get('free_bytes'))}"
        )
    else:
        values["storage"] = "当前固件未提供"

    wake_enabled = bool(
        settings.auto_record_enabled if settings is not None else power.get("timer_wake_enabled")
    )
    wake_interval = (
        settings.wake_interval_sec
        if settings is not None
        else power.get("next_wake_sec")
    )
    values["power"] = (
        f"自动唤醒　{_status_icon(wake_enabled, '已开启', '已关闭')}<br>"
        f"唤醒间隔　{format_interval(wake_interval)}"
        if power or settings is not None
        else "当前固件未提供"
    )

    brightness = (
        settings.led_brightness_percent
        if settings is not None
        else led.get("brightness_percent")
    )
    color = (
        settings.recording_color
        if settings is not None
        else str(led.get("recording_color") or DEFAULT_RECORDING_COLOR)
    )
    try:
        color = normalize_color(str(color))
    except ValueError:
        color = DEFAULT_RECORDING_COLOR
    values["led"] = (
        f"全局亮度　{escape(str(brightness))}%<br>"
        f'录制灯颜色　<span style="color:{color};font-size:20px">●</span>'
        if led or settings is not None
        else "当前固件未提供"
    )
    return values
