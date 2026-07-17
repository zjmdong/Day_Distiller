from __future__ import annotations

import itertools
import re
import time
from dataclasses import dataclass, replace
from typing import Any

from .protocol import (
    Command,
    Frame,
    FrameType,
    ProtocolError,
    SlipDecoder,
    Status,
    build_request,
    parse_frame,
    slip_encode,
)


DEFAULT_BAUDRATE = 115200
DEFAULT_TIMEOUT = 0.7
ESPRESSIF_VID = 0x303A
DAY_DISTILLER_PIDS = {0x4020, 0x4021}


@dataclass(frozen=True)
class PortCandidate:
    device: str
    description: str
    hwid: str
    vid: int | None
    pid: int | None
    likely: bool
    serial_number: str | None = None
    interface: str | None = None
    location: str | None = None
    interface_number: int | None = None
    role: str = "unknown"


@dataclass(frozen=True)
class DeviceProfile:
    firmware_version: str
    serial_number: str
    device_id: str
    protocol_version: int
    adapter_name: str
    capabilities: frozenset[str]

    @property
    def is_firmware_v2(self) -> bool:
        return self.adapter_name == "transactional_export_v2"

    @property
    def display_firmware(self) -> str:
        return self.firmware_version or "1.x（旧版）"

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    @property
    def supports_device_settings(self) -> bool:
        return self.supports("device_config_v1")

    @property
    def supports_device_status(self) -> bool:
        return self.supports("device_status_v2")


class UsbLinkDevice:
    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.port_name = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None
        self._decoder = SlipDecoder()
        self._sequence = itertools.count(1)

    def __enter__(self) -> "UsbLinkDevice":
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def open(self, retries: int = 3) -> None:
        if self._serial is not None:
            return
        import serial

        last_error: Exception | None = None
        for attempt in range(max(1, retries)):
            handle = None
            try:
                handle = serial.Serial(
                    self.port_name,
                    self.baudrate,
                    timeout=self.timeout,
                    write_timeout=self.timeout,
                    rtscts=False,
                    dsrdtr=False,
                )
                # Native USB CDC can re-enumerate between CreateFile and the
                # first ClearCommError call. A brief settle plus bounded retry
                # handles that transition without surfacing a phantom COM-port
                # error to the user.
                time.sleep(0.08)
                handle.reset_input_buffer()
                self._serial = handle
                return
            except (OSError, PermissionError, serial.SerialException) as exc:
                last_error = exc
                if handle is not None:
                    try:
                        handle.close()
                    except Exception:
                        pass
                if attempt + 1 < max(1, retries):
                    time.sleep(0.18 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def request(
        self,
        command: Command,
        payload: dict[str, Any] | None = None,
        timeout: float = 2.0,
    ) -> Frame:
        if self._serial is None:
            self.open()
        assert self._serial is not None

        sequence = next(self._sequence)
        raw = build_request(command, sequence, payload)
        self._serial.write(slip_encode(raw))
        self._serial.flush()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self._serial.read(256)
            if not chunk:
                continue
            for decoded in self._decoder.feed(chunk):
                frame = parse_frame(decoded)
                if frame.frame_type != FrameType.RESPONSE:
                    continue
                if frame.sequence != sequence:
                    continue
                if frame.command != command:
                    continue
                if frame.status != Status.OK:
                    detail = frame.payload.decode("utf-8", errors="replace")
                    raise ProtocolError(f"{frame.status.name}: {detail}")
                return frame
        raise TimeoutError(f"no response to {command.name} on {self.port_name}")

    def hello(self) -> dict[str, Any]:
        return self.request(Command.HELLO).payload_json()

    def get_status(self) -> dict[str, Any]:
        return self.request(Command.GET_STATUS).payload_json()

    def enter_msc(
        self,
        access: str = "rw",
        export_ids: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        if access not in {"rw", "ro"}:
            raise ValueError("access must be 'rw' or 'ro'")
        payload: dict[str, Any] = {"access": access}
        if export_ids:
            payload["export_ids"] = list(export_ids)
        return self.request(Command.ENTER_MSC, payload, timeout=5.0).payload_json()

    def exit_msc(self, force: bool = False, next_mode: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"force": force}
        if next_mode is not None:
            if next_mode not in {"normal", "maintenance"}:
                raise ValueError("next_mode must be 'normal' or 'maintenance'")
            payload["next_mode"] = next_mode
        return self.request(Command.EXIT_MSC, payload, timeout=5.0).payload_json()

    def list_record_dates(self, cursor: int = 0, limit: int = 15) -> dict[str, Any]:
        return self.request(
            Command.LIST_RECORD_DATES,
            {"cursor": cursor, "limit": limit},
            timeout=8.0,
        ).payload_json()

    def begin_export(self, target_date: str, client_request_id: str) -> dict[str, Any]:
        return self.request(
            Command.BEGIN_EXPORT,
            {"date": target_date, "client_request_id": client_request_id},
            timeout=15.0,
        ).payload_json()

    def get_export_status(
        self,
        export_id: str | None = None,
        cursor: int = 0,
        limit: int = 8,
    ) -> dict[str, Any]:
        payload: dict[str, Any]
        if export_id:
            payload = {"export_id": export_id}
        else:
            payload = {"cursor": cursor, "limit": limit}
        return self.request(Command.GET_EXPORT_STATUS, payload, timeout=8.0).payload_json()

    def commit_export_delete(
        self,
        export_id: str,
        manifest_sha256: str,
        record_count: int,
    ) -> dict[str, Any]:
        return self.request(
            Command.COMMIT_EXPORT_DELETE,
            {
                "export_id": export_id,
                "manifest_sha256": manifest_sha256,
                "confirm_record_count": record_count,
            },
            timeout=45.0,
        ).payload_json()

    def abort_export(self, export_id: str) -> dict[str, Any]:
        return self.request(
            Command.ABORT_EXPORT,
            {"export_id": export_id},
            timeout=8.0,
        ).payload_json()

    def end_session(self) -> dict[str, Any]:
        return self.request(Command.END_SESSION, {}, timeout=5.0).payload_json()

    def get_config(self, include_secrets: bool = True) -> dict[str, Any]:
        return self.request(
            Command.GET_CONFIG,
            {"include_secrets": bool(include_secrets)},
            timeout=5.0,
        ).payload_json()

    def set_config(
        self,
        patch: dict[str, Any],
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        if not patch:
            raise ValueError("device config patch cannot be empty")
        payload: dict[str, Any] = {"patch": patch}
        if expected_revision is not None:
            payload["expected_revision"] = int(expected_revision)
        return self.request(Command.SET_CONFIG, payload, timeout=8.0).payload_json()

    def preview_led(
        self,
        color: str,
        brightness_percent: int,
        duration_ms: int = 2000,
    ) -> dict[str, Any]:
        return self.request(
            Command.PREVIEW_LED,
            {
                "color": color,
                "brightness_percent": int(brightness_percent),
                "duration_ms": int(duration_ms),
            },
            timeout=5.0,
        ).payload_json()


def _interface_number(hwid: str, location: str | None = None) -> int | None:
    text = f"{hwid} {location or ''}"
    match = re.search(r"(?:MI_|interface\s*)([0-9a-f]{2})", text, re.IGNORECASE)
    if match:
        return int(match.group(1), 16)
    # pyserial on current Windows usbser.sys omits MI_XX from hwid and
    # exposes the interface only as a location suffix such as 1-1:x.2.
    match = re.search(r":x\.([0-9a-f]+)$", str(location or ""), re.IGNORECASE)
    if match:
        return int(match.group(1), 16)
    return None


def _port_role(pid: int | None, interface_number: int | None) -> str:
    if pid == 0x4020:
        if interface_number == 0:
            # Firmware 2.0.1 and later use interface 0 for the protocol.
            return "protocol"
        if interface_number == 2:
            # Firmware 2.0.0 used interface 2 for the protocol. Keep probing
            # it as a compatibility fallback after the current primary port.
            return "compat"
    if pid == 0x4021:
        return "protocol"
    return "unknown"


def device_profile(status: dict[str, Any], port: PortCandidate | None = None) -> DeviceProfile:
    raw_capabilities = status.get("capabilities")
    capabilities = frozenset(
        str(item) for item in raw_capabilities if isinstance(item, str)
    ) if isinstance(raw_capabilities, list) else frozenset()
    transactional = bool(
        {"transactional_export_v2", "export_transactions"} & capabilities
    )
    firmware_version = str(status.get("firmware_version") or "").strip()
    device_id = str(status.get("device_id") or "").strip()
    usb_serial = str(port.serial_number or "").strip() if port else ""
    serial_number = usb_serial or device_id or "DD-USB-LINK"
    try:
        protocol_version = int(status.get("protocol", 1))
    except (TypeError, ValueError):
        protocol_version = 1
    return DeviceProfile(
        firmware_version=firmware_version,
        serial_number=serial_number,
        device_id=device_id or serial_number,
        protocol_version=protocol_version,
        adapter_name="transactional_export_v2" if transactional else "legacy_msc_v1",
        capabilities=capabilities,
    )


def list_serial_ports() -> list[PortCandidate]:
    from serial.tools import list_ports

    candidates: list[PortCandidate] = []
    for port in list_ports.comports():
        vid = getattr(port, "vid", None)
        pid = getattr(port, "pid", None)
        desc = port.description or ""
        hwid = port.hwid or ""
        serial_number = getattr(port, "serial_number", None)
        interface = getattr(port, "interface", None)
        location = getattr(port, "location", None)
        interface_number = _interface_number(hwid, location)
        role = _port_role(pid, interface_number)
        text = f"{desc} {hwid}".lower()
        likely = (
            vid == ESPRESSIF_VID
            and (pid in DAY_DISTILLER_PIDS or "day distiller" in text or "tinyusb" in text)
        ) or "day distiller" in text
        candidates.append(
            PortCandidate(
                device=port.device,
                description=desc,
                hwid=hwid,
                vid=vid,
                pid=pid,
                likely=likely,
                serial_number=serial_number,
                interface=interface,
                location=location,
                interface_number=interface_number,
                role=role,
            )
        )
    # Windows can omit the interface number for MI_00. Infer it only inside
    # one Day Distiller composite-device group when MI_02 is also present.
    groups: dict[tuple[int | None, str | None], list[int]] = {}
    for index, item in enumerate(candidates):
        groups.setdefault((item.pid, item.serial_number), []).append(index)
    for (pid, _serial), indexes in groups.items():
        if pid != 0x4020 or not any(candidates[index].interface_number == 2 for index in indexes):
            continue
        for index in indexes:
            if candidates[index].interface_number is None:
                candidates[index] = replace(
                    candidates[index],
                    interface_number=0,
                    role="protocol",
                )

    role_order = {"protocol": 0, "compat": 1, "unknown": 2, "log": 3}
    return sorted(
        candidates,
        key=lambda item: (not item.likely, role_order[item.role], item.device),
    )


def find_device(timeout_per_port: float = 0.8) -> tuple[PortCandidate, dict[str, Any]] | None:
    for candidate in list_serial_ports():
        try:
            timeout = max(timeout_per_port, 1.1) if candidate.role == "protocol" else timeout_per_port
            with UsbLinkDevice(candidate.device, timeout=timeout) as device:
                status = device.hello()
            protocol = status.get("protocol")
            if str(protocol) == "1" and status.get("device") == "Day Distiller":
                return candidate, status
        except Exception:
            continue
    return None
