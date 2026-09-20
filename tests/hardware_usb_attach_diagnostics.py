"""Attach to CDC0 immediately after reset and collect non-destructive diagnostics.

This helper is intended for short maintenance windows such as low-battery
locked boot.  It reads public status/configuration and the date inventory,
then keeps the maintenance lease alive while capturing CDC1.  It never asks
for secrets, changes configuration, starts recording, enters MSC, or deletes
records.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import serial

from hardware_usb_smoke import drain_log, transact


def open_when_available(name: str, timeout_s: float) -> serial.Serial:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            port = serial.Serial(name, 115200, timeout=0.05, write_timeout=1.0)
            port.dtr = False
            port.rts = False
            return port
        except (OSError, serial.SerialException) as error:
            last_error = error
            time.sleep(0.05)
    raise TimeoutError(f"could not open {name} within {timeout_s:.1f}s: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-port", required=True)
    parser.add_argument("--log-port", required=True)
    parser.add_argument("--log-output", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=float, default=120.0)
    parser.add_argument("--hold-seconds", type=float, default=120.0)
    parser.add_argument("--expected-firmware", default="2.1.1")
    args = parser.parse_args()

    captured = bytearray()
    protocol_port: serial.Serial | None = None
    log_port: serial.Serial | None = None
    try:
        protocol_port = open_when_available(args.protocol_port, args.wait_seconds)
        hello, _, _ = transact(protocol_port, 6001, 1)
        assert hello.get("firmware_version") == args.expected_firmware, hello
        log_port = open_when_available(args.log_port, 5.0)
        time.sleep(0.1)
        captured.extend(drain_log(log_port))

        status, _, _ = transact(protocol_port, 6002, 3)
        config, _, _ = transact(protocol_port, 6003, 12)
        dates, _, _ = transact(protocol_port, 6004, 11, {"cursor": 0, "limit": 20})
        print(
            json.dumps(
                {"hello": hello, "status": status, "config": config, "dates": dates},
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )

        deadline = time.monotonic() + args.hold_seconds
        sequence = 6005
        while time.monotonic() < deadline:
            captured.extend(drain_log(log_port))
            transact(protocol_port, sequence, 2)
            sequence += 1
            time.sleep(2.0)
    finally:
        if log_port is not None:
            try:
                captured.extend(drain_log(log_port))
            except (OSError, serial.SerialException):
                pass
            log_port.close()
        if protocol_port is not None:
            protocol_port.close()
        args.log_output.parent.mkdir(parents=True, exist_ok=True)
        args.log_output.write_bytes(captured)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
