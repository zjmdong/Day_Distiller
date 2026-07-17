"""Reversible Day Distiller 2.1 USB configuration hardware test.

Only the LED brightness setting is changed, and it is restored in a finally
block.  No export, delete, MSC-write, or recording command is ever sent.
Wi-Fi secret material is checked in memory and is never printed or written.
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

from hardware_usb_smoke import CRC, HEADER, drain_log, make_request, read_slip_frame


OK = 0
UNSUPPORTED_CMD = 3
INVALID_ARG = 4
BAD_STATE = 7


def transact_status(
    port: serial.Serial,
    sequence: int,
    command: int,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any], float, int]:
    port.reset_input_buffer()
    started = time.perf_counter()
    port.write(make_request(sequence, command, payload))
    port.flush()
    frame = read_slip_frame(port, 2.0)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    expected_crc = CRC.unpack(frame[-CRC.size :])[0]
    actual_crc = binascii.crc32(frame[:-CRC.size]) & 0xFFFFFFFF
    assert expected_crc == actual_crc
    unpacked = HEADER.unpack(frame[: HEADER.size])
    magic, version, frame_type, flags, reserved, header_len, response_sequence, response_command, status, payload_len = unpacked
    assert magic == b"DD" and version == 1 and frame_type == 2
    assert flags == 0 and reserved == 0 and header_len == HEADER.size
    assert response_sequence == sequence and response_command == command
    assert payload_len <= 1600
    assert len(frame) == HEADER.size + payload_len + CRC.size
    response = json.loads(frame[HEADER.size : -CRC.size].decode("utf-8"))
    assert isinstance(response, dict)
    return status, response, elapsed_ms, len(frame)


def request_ok(
    port: serial.Serial,
    sequence: int,
    command: int,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], float, int]:
    status, response, elapsed_ms, frame_bytes = transact_status(port, sequence, command, payload)
    assert status == OK, (command, status, response)
    return response, elapsed_ms, frame_bytes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-port", required=True)
    parser.add_argument("--log-port", required=True)
    parser.add_argument("--log-output", type=Path, required=True)
    parser.add_argument("--expected-firmware", default="2.1.0")
    args = parser.parse_args()

    sequence = 2001
    latencies: list[float] = []
    frames: list[int] = []
    captured_log = bytearray()
    original_brightness: int | None = None
    current_revision: int | None = None
    changed = False
    secret_bytes = b""

    with serial.Serial(args.log_port, 115200, timeout=0.05) as log_port, serial.Serial(
        args.protocol_port, 115200, timeout=0.05, write_timeout=1.0
    ) as protocol_port:
        log_port.dtr = False
        log_port.rts = False
        protocol_port.dtr = False
        protocol_port.rts = False
        time.sleep(0.2)
        captured_log.extend(drain_log(log_port))
        try:
            hello, latency, frame = request_ok(protocol_port, sequence, 1, {})
            sequence += 1
            latencies.append(latency)
            frames.append(frame)
            assert hello.get("firmware_version") == args.expected_firmware
            capabilities = hello.get("capabilities")
            assert isinstance(capabilities, list)
            for capability in ("device_status_v2", "device_config_v1", "device_config_write"):
                assert capability in capabilities
            assert "led_preview" not in capabilities

            dates_before, latency, frame = request_ok(
                protocol_port, sequence, 11, {"cursor": 0, "limit": 20}
            )
            sequence += 1
            latencies.append(latency)
            frames.append(frame)

            public_config, latency, frame = request_ok(protocol_port, sequence, 12, {})
            sequence += 1
            latencies.append(latency)
            frames.append(frame)
            assert "password" not in public_config.get("wifi", {})
            original_brightness = int(public_config["led"]["brightness_percent"])
            current_revision = int(public_config["revision"])

            private_config, latency, frame = request_ok(
                protocol_port, sequence, 12, {"include_secrets": True}
            )
            sequence += 1
            latencies.append(latency)
            frames.append(frame)
            wifi_private = private_config.get("wifi", {})
            assert "password" in wifi_private
            password = wifi_private.get("password")
            assert isinstance(password, str)
            secret_bytes = password.encode("utf-8")
            del password, private_config, wifi_private

            invalid_cases = (
                ({"patch": {"unknown": {"value": 1}}}, "patch"),
                ({"patch": {"led": {"brightness_percent": 4}}}, "led.brightness_percent"),
                ({"patch": {"led": {"recording_color": "#000000"}}}, "led.recording_color"),
                ({"patch": {"system": {"auto_record_enabled": 1}}}, "system.auto_record_enabled"),
            )
            for payload, expected_field in invalid_cases:
                status, response, latency, frame = transact_status(
                    protocol_port, sequence, 13, payload
                )
                sequence += 1
                latencies.append(latency)
                frames.append(frame)
                assert status == INVALID_ARG, (status, response)
                assert response.get("field") == expected_field, response

            status, response, latency, frame = transact_status(
                protocol_port,
                sequence,
                13,
                {
                    "expected_revision": max(0, current_revision - 1),
                    "patch": {"led": {"brightness_percent": original_brightness}},
                },
            )
            sequence += 1
            latencies.append(latency)
            frames.append(frame)
            assert status == BAD_STATE and response.get("reason") == "revision_conflict", response

            test_brightness = 5 if original_brightness != 5 else 6
            applied, latency, frame = request_ok(
                protocol_port,
                sequence,
                13,
                {
                    "expected_revision": current_revision,
                    "patch": {"led": {"brightness_percent": test_brightness}},
                },
            )
            sequence += 1
            latencies.append(latency)
            frames.append(frame)
            changed = True
            current_revision = int(applied["revision"])
            assert applied.get("changed") is True and "led" in applied.get("applied", [])

            confirmed, latency, frame = request_ok(protocol_port, sequence, 12, {})
            sequence += 1
            latencies.append(latency)
            frames.append(frame)
            assert confirmed["led"]["brightness_percent"] == test_brightness
            assert confirmed["revision"] == current_revision

            restored, latency, frame = request_ok(
                protocol_port,
                sequence,
                13,
                {
                    "expected_revision": current_revision,
                    "patch": {"led": {"brightness_percent": original_brightness}},
                },
            )
            sequence += 1
            latencies.append(latency)
            frames.append(frame)
            changed = False
            current_revision = int(restored["revision"])

            final_config, latency, frame = request_ok(protocol_port, sequence, 12, {})
            sequence += 1
            latencies.append(latency)
            frames.append(frame)
            assert final_config["led"]["brightness_percent"] == original_brightness
            assert "password" not in final_config["wifi"]

            dates_after, latency, frame = request_ok(
                protocol_port, sequence, 11, {"cursor": 0, "limit": 20}
            )
            latencies.append(latency)
            frames.append(frame)
            assert dates_after == dates_before, "record-date inventory changed during config test"
        finally:
            if original_brightness is not None:
                # Always read back the device in case a successful write response
                # was lost. Restoration changes only LED brightness and never
                # touches Wi-Fi or recording data.
                try:
                    config, _, _ = request_ok(protocol_port, sequence + 10, 12, {})
                    if int(config["led"]["brightness_percent"]) != original_brightness:
                        request_ok(
                            protocol_port,
                            sequence + 11,
                            13,
                            {
                                "expected_revision": int(config["revision"]),
                                "patch": {"led": {"brightness_percent": original_brightness}},
                            },
                        )
                    changed = False
                except Exception as restore_error:
                    raise AssertionError("failed to restore original LED brightness") from restore_error
            captured_log.extend(drain_log(log_port))

    if secret_bytes:
        assert secret_bytes not in captured_log, "Wi-Fi password leaked to CDC1 log"
    decoded_log = captured_log.decode("utf-8", errors="replace").lower()
    for marker in ("stack overflow", "guru meditation", "panic'ed", "abort() was called"):
        assert marker not in decoded_log, f"fatal marker in CDC1 log: {marker}"
    # HELLO is covered by the frozen 100 ms status-path requirement. Config
    # writes may include an NVS commit and use the protocol's two-second bound.
    assert latencies[0] < 100.0, latencies[0]
    assert max(latencies) < 2000.0, max(latencies)
    slowest_request_index = max(range(len(latencies)), key=latencies.__getitem__)
    args.log_output.parent.mkdir(parents=True, exist_ok=True)
    args.log_output.write_bytes(captured_log)
    print(
        json.dumps(
            {
                "result": "PASS",
                "requests": len(latencies),
                "max_latency_ms": round(max(latencies), 2),
                "slowest_request_index": slowest_request_index,
                "max_frame_bytes": max(frames),
                "firmware_version": args.expected_firmware,
                "config_restored": not changed,
                "record_inventory_unchanged": True,
                "secret_absent_from_log": True,
                "cdc1_log_bytes": len(captured_log),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
