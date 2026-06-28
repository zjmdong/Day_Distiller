from __future__ import annotations

import binascii
import json
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


PROTOCOL_VERSION = 1
MAGIC = b"DD"
HEADER = struct.Struct("<2sBBBBHIHHI")
HEADER_LEN = HEADER.size
CRC_LEN = 4
FRAME_MAX = 2048
PAYLOAD_MAX = 1600

SLIP_END = 0xC0
SLIP_ESC = 0xDB
SLIP_ESC_END = 0xDC
SLIP_ESC_ESC = 0xDD


class FrameType(IntEnum):
    REQUEST = 1
    RESPONSE = 2
    EVENT = 3


class Command(IntEnum):
    HELLO = 1
    PING = 2
    GET_STATUS = 3
    ENTER_MSC = 4
    EXIT_MSC = 5


class Status(IntEnum):
    OK = 0
    BAD_FRAME = 1
    UNSUPPORTED_VERSION = 2
    UNSUPPORTED_CMD = 3
    INVALID_ARG = 4
    BUSY = 5
    STORAGE_ERROR = 6
    BAD_STATE = 7
    TIMEOUT = 8


class ProtocolError(Exception):
    """Raised when a USB Link frame is invalid."""


@dataclass(frozen=True)
class Frame:
    frame_type: FrameType
    sequence: int
    command: Command
    status: Status
    payload: bytes
    flags: int = 0

    def payload_json(self) -> dict[str, Any]:
        if not self.payload:
            return {}
        return json.loads(self.payload.decode("utf-8"))


def crc32(data: bytes) -> int:
    return binascii.crc32(data) & 0xFFFFFFFF


def json_payload(value: dict[str, Any] | None) -> bytes:
    if not value:
        return b"{}"
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > PAYLOAD_MAX:
        raise ProtocolError(f"payload too large: {len(encoded)} bytes")
    return encoded


def build_frame(
    frame_type: FrameType,
    command: Command,
    sequence: int,
    payload: dict[str, Any] | bytes | None = None,
    status: Status = Status.OK,
    flags: int = 0,
) -> bytes:
    if payload is None or isinstance(payload, dict):
        payload_bytes = json_payload(payload)
    else:
        payload_bytes = payload
    if len(payload_bytes) > PAYLOAD_MAX:
        raise ProtocolError(f"payload too large: {len(payload_bytes)} bytes")

    header = HEADER.pack(
        MAGIC,
        PROTOCOL_VERSION,
        int(frame_type),
        flags & 0xFF,
        0,
        HEADER_LEN,
        sequence & 0xFFFFFFFF,
        int(command),
        int(status),
        len(payload_bytes),
    )
    body = header + payload_bytes
    return body + struct.pack("<I", crc32(body))


def build_request(command: Command, sequence: int, payload: dict[str, Any] | None = None) -> bytes:
    return build_frame(FrameType.REQUEST, command, sequence, payload, Status.OK)


def parse_frame(raw: bytes) -> Frame:
    if len(raw) < HEADER_LEN + CRC_LEN:
        raise ProtocolError("frame too short")
    if len(raw) > FRAME_MAX:
        raise ProtocolError("frame too large")

    expected_crc = struct.unpack_from("<I", raw, len(raw) - CRC_LEN)[0]
    actual_crc = crc32(raw[:-CRC_LEN])
    if expected_crc != actual_crc:
        raise ProtocolError("crc mismatch")

    (
        magic,
        version,
        frame_type,
        flags,
        _reserved,
        header_len,
        sequence,
        command,
        status,
        payload_len,
    ) = HEADER.unpack_from(raw)

    if magic != MAGIC:
        raise ProtocolError("bad magic")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported version: {version}")
    if header_len != HEADER_LEN:
        raise ProtocolError(f"bad header length: {header_len}")
    if payload_len != len(raw) - HEADER_LEN - CRC_LEN:
        raise ProtocolError("payload length mismatch")

    try:
        parsed_type = FrameType(frame_type)
        parsed_command = Command(command)
        parsed_status = Status(status)
    except ValueError as exc:
        raise ProtocolError(str(exc)) from exc

    payload = raw[HEADER_LEN : HEADER_LEN + payload_len]
    return Frame(parsed_type, sequence, parsed_command, parsed_status, payload, flags)


def slip_encode(raw: bytes) -> bytes:
    out = bytearray([SLIP_END])
    for byte in raw:
        if byte == SLIP_END:
            out.extend((SLIP_ESC, SLIP_ESC_END))
        elif byte == SLIP_ESC:
            out.extend((SLIP_ESC, SLIP_ESC_ESC))
        else:
            out.append(byte)
    out.append(SLIP_END)
    return bytes(out)


class SlipDecoder:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._escaped = False

    def feed(self, chunk: bytes) -> list[bytes]:
        frames: list[bytes] = []
        for byte in chunk:
            if byte == SLIP_END:
                if self._buffer:
                    frames.append(bytes(self._buffer))
                    self._buffer.clear()
                self._escaped = False
                continue
            if byte == SLIP_ESC:
                self._escaped = True
                continue
            if self._escaped:
                if byte == SLIP_ESC_END:
                    byte = SLIP_END
                elif byte == SLIP_ESC_ESC:
                    byte = SLIP_ESC
                else:
                    self._buffer.clear()
                    self._escaped = False
                    continue
                self._escaped = False
            self._buffer.append(byte)
            if len(self._buffer) > FRAME_MAX:
                self._buffer.clear()
                self._escaped = False
        return frames
