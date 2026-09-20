"""Capture shutdown breadcrumbs and the reset reason after END_SESSION.

Only HELLO, GET_STATUS, and END_SESSION are sent.  The helper never changes
configuration or touches record/export commands.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import serial

from hardware_usb_attach_diagnostics import open_when_available
from hardware_usb_smoke import drain_log, transact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol-port", required=True)
    parser.add_argument("--log-port", required=True)
    parser.add_argument("--log-output", type=Path, required=True)
    parser.add_argument("--reenumeration-timeout", type=float, default=45.0)
    args = parser.parse_args()

    captured = bytearray()
    with open_when_available(args.protocol_port, 5.0) as protocol_port, open_when_available(
        args.log_port, 5.0
    ) as log_port:
        transact(protocol_port, 7001, 1)
        captured.extend(drain_log(log_port))
        transact(protocol_port, 7002, 9)
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            try:
                captured.extend(log_port.read(log_port.in_waiting or 1))
            except (OSError, serial.SerialException):
                break

    args.log_output.parent.mkdir(parents=True, exist_ok=True)
    args.log_output.write_bytes(captured)

    # Require the old instance to disappear before accepting a new port.
    gone_deadline = time.monotonic() + 10.0
    while time.monotonic() < gone_deadline:
        try:
            probe = serial.Serial(args.protocol_port, 115200, timeout=0.05)
        except (OSError, serial.SerialException):
            break
        else:
            probe.close()
            time.sleep(0.1)

    with open_when_available(args.protocol_port, args.reenumeration_timeout) as protocol_port:
        hello, _, _ = transact(protocol_port, 7010, 1)
        status, _, _ = transact(protocol_port, 7011, 3)

    print(
        json.dumps(
            {
                "firmware_version": hello.get("firmware_version"),
                "post_reset_power": status.get("power"),
                "last_record_error": status.get("last_record_error"),
                "camera": status.get("camera"),
                "captured_log_bytes": len(captured),
                "captured_log_tail": captured.decode("utf-8", errors="replace")[-3000:],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
