"""Observe one automatic wake/record cycle without requesting a recording.

The test waits for the runtime USB device, captures CDC1 until the firmware
reports that its automatic recording has finished, and only then starts a
maintenance session on CDC0.  It uses read-only protocol commands plus
END_SESSION.  It never exports, enters MSC, deletes, or writes configuration.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import serial
from serial.tools import list_ports

from hardware_usb_smoke import transact


RUNTIME_VID = 0x303A
RUNTIME_PID = 0x4020


def runtime_ports() -> tuple[str | None, str | None]:
    protocol_port = None
    log_port = None
    for info in list_ports.comports():
        if info.vid != RUNTIME_VID or info.pid != RUNTIME_PID:
            continue
        hwid = info.hwid.upper()
        if "MI_00" in hwid:
            protocol_port = info.device
        elif "MI_02" in hwid:
            log_port = info.device
    return protocol_port, log_port


def wait_for_runtime_ports(timeout_s: float) -> tuple[str, str]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        protocol_port, log_port = runtime_ports()
        if protocol_port and log_port:
            return protocol_port, log_port
        time.sleep(0.1)
    raise TimeoutError(f"runtime CDC ports did not enumerate within {timeout_s:.1f}s")


def open_named_port(name: str, timeout_s: float) -> serial.Serial:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            port = serial.Serial(name, 115200, timeout=0.05)
            port.dtr = False
            port.rts = False
            return port
        except (OSError, serial.SerialException) as error:
            last_error = error
            time.sleep(0.05)
    raise TimeoutError(f"could not open {name} within {timeout_s:.1f}s: {last_error}")


def date_count(payload: dict, date: str) -> int:
    for item in payload.get("items", []):
        if item.get("date") == date:
            return int(item.get("record_count", 0))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-output", type=Path, required=True)
    parser.add_argument("--protocol-port")
    parser.add_argument("--log-port")
    parser.add_argument("--expected-firmware", default="2.1.1")
    parser.add_argument("--date", required=True)
    parser.add_argument("--previous-count", type=int)
    parser.add_argument("--enumeration-timeout", type=float, default=90.0)
    parser.add_argument("--record-timeout", type=float, default=30.0)
    args = parser.parse_args()

    if bool(args.protocol_port) != bool(args.log_port):
        parser.error("--protocol-port and --log-port must be provided together")
    if args.protocol_port:
        protocol_name, log_name = args.protocol_port, args.log_port
    else:
        protocol_name, log_name = wait_for_runtime_ports(args.enumeration_timeout)
    captured = bytearray()
    record_finished = False
    disconnect_error = ""

    try:
        with open_named_port(log_name, args.enumeration_timeout) as log_port:
            deadline = time.monotonic() + args.record_timeout
            while time.monotonic() < deadline:
                try:
                    captured.extend(log_port.read(log_port.in_waiting or 1))
                except serial.SerialException as error:
                    disconnect_error = str(error)
                    break
                decoded = captured.decode("utf-8", errors="replace")
                if "record finished:" in decoded:
                    record_finished = True
                    break
    finally:
        args.log_output.parent.mkdir(parents=True, exist_ok=True)
        args.log_output.write_bytes(captured)

    decoded = captured.decode("utf-8", errors="replace")
    fatal_markers = ("stack overflow", "guru meditation", "panic'ed", "abort() was called")
    matched = [marker for marker in fatal_markers if marker in decoded.lower()]
    assert not matched, f"fatal markers in CDC1 log: {matched}"
    assert record_finished, (
        "automatic recording did not finish before timeout/disconnect",
        disconnect_error,
        decoded[-2000:],
    )
    result_match = re.search(r"record finished: ([^ ]+) path=([^\r\n]+)", decoded)
    assert result_match and result_match.group(1) == "ESP_OK", result_match.group(0) if result_match else decoded[-1000:]

    # HELLO is intentionally delayed until after record_finished so that the
    # maintenance lease cannot suppress the automatic recording under test.
    sequence = 5001
    with serial.Serial(protocol_name, 115200, timeout=0.05, write_timeout=1.0) as protocol_port:
        protocol_port.dtr = False
        protocol_port.rts = False
        hello, _, _ = transact(protocol_port, sequence, 1)
        sequence += 1
        status, _, _ = transact(protocol_port, sequence, 3)
        sequence += 1
        dates, _, _ = transact(protocol_port, sequence, 11, {"cursor": 0, "limit": 20})
        sequence += 1
        status_again, _, _ = transact(protocol_port, sequence, 3)
        sequence += 1
        transact(protocol_port, sequence, 9)

    assert hello.get("firmware_version") == args.expected_firmware
    assert status.get("power", {}).get("reset_reason") == "deep_sleep", status.get("power")
    assert status.get("power", {}).get("wake_reason") == "timer", status.get("power")
    assert status.get("storage", {}).get("mounted") is True, status.get("storage")
    assert status.get("recording") is False, status
    assert status_again.get("recording") is False, status_again

    current_count = date_count(dates, args.date)
    if args.previous_count is not None:
        assert current_count == args.previous_count + 1, (args.previous_count, current_count, dates)

    print(
        json.dumps(
            {
                "result": "PASS",
                "firmware_version": hello.get("firmware_version"),
                "wake": status.get("power"),
                "storage": status.get("storage"),
                "recording": status_again.get("recording"),
                "record_result": result_match.group(1),
                "record_path": result_match.group(2),
                "date": args.date,
                "record_count": current_count,
                "previous_count": args.previous_count,
                "cdc1_log_bytes": len(captured),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
