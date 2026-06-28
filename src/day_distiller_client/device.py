from __future__ import annotations

import itertools
import time
from dataclasses import dataclass
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

    def open(self) -> None:
        if self._serial is not None:
            return
        import serial

        self._serial = serial.Serial(
            self.port_name,
            self.baudrate,
            timeout=self.timeout,
            write_timeout=self.timeout,
        )
        self._serial.reset_input_buffer()

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

    def enter_msc(self, access: str = "rw") -> dict[str, Any]:
        if access not in {"rw", "ro"}:
            raise ValueError("access must be 'rw' or 'ro'")
        return self.request(Command.ENTER_MSC, {"access": access}, timeout=3.0).payload_json()

    def exit_msc(self, force: bool = False) -> dict[str, Any]:
        return self.request(Command.EXIT_MSC, {"force": force}, timeout=3.0).payload_json()


def list_serial_ports() -> list[PortCandidate]:
    from serial.tools import list_ports

    candidates: list[PortCandidate] = []
    for port in list_ports.comports():
        vid = getattr(port, "vid", None)
        pid = getattr(port, "pid", None)
        desc = port.description or ""
        hwid = port.hwid or ""
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
            )
        )
    return sorted(candidates, key=lambda item: (not item.likely, item.device))


def find_device(timeout_per_port: float = 0.8) -> tuple[PortCandidate, dict[str, Any]] | None:
    for candidate in list_serial_ports():
        try:
            with UsbLinkDevice(candidate.device, timeout=timeout_per_port) as device:
                status = device.hello()
            if status.get("protocol") == 1 and status.get("device") == "Day Distiller":
                return candidate, status
        except Exception:
            continue
    return None
