"""Read-only Day Distiller USB Link hardware smoke test.

Run this script with the ESP-IDF Python environment so pyserial is available.
It deliberately exercises only commands that cannot mutate or delete recordings.
"""

from __future__ import annotations

import argparse
import binascii
import json
import struct
import time
from pathlib import Path
from typing import Any

import serial


HEADER = struct.Struct("<2sBBBBHIHHI")
CRC = struct.Struct("<I")
FRAME_END = 0xC0
FRAME_ESC = 0xDB
MAX_FRAME = 2048
MAX_PAYLOAD = 1600


def slip_encode(frame: bytes) -> bytes:
    encoded = bytearray([FRAME_END])
    for value in frame:
        if value == FRAME_END:
            encoded.extend((FRAME_ESC, 0xDC))
        elif value == FRAME_ESC:
            encoded.extend((FRAME_ESC, 0xDD))
        else:
            encoded.append(value)
    encoded.append(FRAME_END)
    return bytes(encoded)


def make_request(sequence: int, command: int, payload: dict[str, Any]) -> bytes:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = HEADER.pack(b"DD", 1, 1, 0, 0, HEADER.size, sequence, command, 0, len(encoded))
    body = header + encoded
    return slip_encode(body + CRC.pack(binascii.crc32(body) & 0xFFFFFFFF))


def read_slip_frame(port: serial.Serial, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    frame = bytearray()
    started = False
    escaped = False
    while time.monotonic() < deadline:
        value = port.read(1)
        if not value:
            continue
        byte = value[0]
        if byte == FRAME_END:
            if started and frame:
                return bytes(frame)
            started = True
            frame.clear()
            escaped = False
            continue
        if not started:
            continue
        if escaped:
            if byte == 0xDC:
                frame.append(FRAME_END)
            elif byte == 0xDD:
                frame.append(FRAME_ESC)
            else:
                raise AssertionError(f"invalid SLIP escape 0x{byte:02x}")
            escaped = False
        elif byte == FRAME_ESC:
            escaped = True
        else:
            frame.append(byte)
        if len(frame) > MAX_FRAME:
            raise AssertionError("decoded frame exceeds 2048 bytes")
    raise TimeoutError(f"no complete response received from {port.port} within {timeout:.1f}s")


def transact(
    port: serial.Serial, sequence: int, command: int, payload: dict[str, Any] | None = None
) -> tuple[dict[str, Any], float, int]:
    port.reset_input_buffer()
    request = make_request(sequence, command, payload or {})
    started = time.perf_counter()
    port.write(request)
    port.flush()
    frame = read_slip_frame(port, 2.0)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if len(frame) < HEADER.size + CRC.size:
        raise AssertionError("response is shorter than header plus CRC")
    expected_crc = CRC.unpack(frame[-CRC.size :])[0]
    actual_crc = binascii.crc32(frame[:-CRC.size]) & 0xFFFFFFFF
    if expected_crc != actual_crc:
        raise AssertionError("response CRC32 mismatch")
    magic, version, frame_type, flags, reserved, header_len, response_sequence, response_command, status, payload_len = HEADER.unpack(
        frame[: HEADER.size]
    )
    assert magic == b"DD", magic
    assert version == 1, version
    assert frame_type == 2, frame_type
    assert flags == 0 and reserved == 0
    assert header_len == HEADER.size, header_len
    assert response_sequence == sequence, (response_sequence, sequence)
    assert response_command == command, (response_command, command)
    assert status == 0, status
    assert payload_len <= MAX_PAYLOAD, payload_len
    assert len(frame) == HEADER.size + payload_len + CRC.size
    payload = json.loads(frame[HEADER.size : -CRC.size].decode("utf-8"))
    assert isinstance(payload, dict)
    return payload, elapsed_ms, len(frame)


def walk_keys(value: Any) -> list[str]:
    keys: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            keys.append(str(key).lower())
            keys.extend(walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.extend(walk_keys(child))
    return keys


def drain_log(port: serial.Serial) -> bytes:
    chunks = bytearray()
    while port.in_waiting:
        chunks.extend(port.read(port.in_waiting))
        time.sleep(0.01)
    return bytes(chunks)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-port", required=True)
    parser.add_argument("--log-port", required=True)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--log-output", type=Path, required=True)
    args = parser.parse_args()

    assert args.iterations > 0
    latencies: list[float] = []
    frame_sizes: list[int] = []
    captured_log = bytearray()
    sequence = 1

    with serial.Serial(args.log_port, 115200, timeout=0.05) as log_port, serial.Serial(
        args.protocol_port, 115200, timeout=0.05, write_timeout=1.0
    ) as protocol_port:
        log_port.dtr = False
        log_port.rts = False
        protocol_port.dtr = False
        protocol_port.rts = False
        time.sleep(0.2)
        captured_log.extend(drain_log(log_port))

        hello, elapsed, size = transact(protocol_port, sequence, 1)
        sequence += 1
        latencies.append(elapsed)
        frame_sizes.append(size)
        assert hello.get("protocol") == 1
        assert hello.get("device") == "Day Distiller"
        assert hello.get("mode") == "serial"
        assert hello.get("maintenance") is True

        ping, elapsed, size = transact(protocol_port, sequence, 2)
        sequence += 1
        latencies.append(elapsed)
        frame_sizes.append(size)
        assert ping.get("protocol") == 1

        last_status: dict[str, Any] = {}
        for index in range(args.iterations):
            last_status, elapsed, size = transact(protocol_port, sequence, 3)
            sequence += 1
            latencies.append(elapsed)
            frame_sizes.append(size)
            assert last_status.get("protocol") == 1
            forbidden = {"password", "wifi_password", "passphrase", "psk"}.intersection(walk_keys(last_status))
            assert not forbidden, f"sensitive keys in normal status: {sorted(forbidden)}"
            captured_log.extend(drain_log(log_port))
            if index + 1 < args.iterations:
                time.sleep(args.interval)

        captured_log.extend(drain_log(log_port))

    args.log_output.parent.mkdir(parents=True, exist_ok=True)
    args.log_output.write_bytes(captured_log)
    decoded_log = captured_log.decode("utf-8", errors="replace")
    fatal_markers = ("stack overflow", "guru meditation", "panic'ed", "abort() was called")
    matched = [marker for marker in fatal_markers if marker in decoded_log.lower()]
    assert not matched, f"fatal markers in CDC1 log: {matched}"
    assert max(latencies) < 100.0, f"response exceeded 100 ms: {max(latencies):.1f} ms"

    print(
        json.dumps(
            {
                "result": "PASS",
                "protocol_port": args.protocol_port,
                "log_port": args.log_port,
                "requests": len(latencies),
                "latency_ms": {
                    "min": round(min(latencies), 2),
                    "max": round(max(latencies), 2),
                    "average": round(sum(latencies) / len(latencies), 2),
                },
                "max_frame_bytes": max(frame_sizes),
                "firmware_version": hello.get("firmware_version"),
                "device_id": hello.get("device_id"),
                "storage": last_status.get("storage"),
                "cdc1_log_bytes": len(captured_log),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
