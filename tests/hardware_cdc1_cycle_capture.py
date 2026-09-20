"""Capture CDC1 across disconnect/re-enumeration cycles without sending USB commands."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import serial


def open_when_available(name: str, timeout_s: float) -> serial.Serial:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            port = serial.Serial(name, 115200, timeout=0.05)
            port.dtr = False
            port.rts = False
            return port
        except (OSError, serial.SerialException):
            time.sleep(0.05)
    raise TimeoutError(f"{name} did not become available within {timeout_s:.1f}s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--wait-seconds", type=float, default=90.0)
    parser.add_argument("--capture-seconds", type=float, default=45.0)
    args = parser.parse_args()

    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    summaries = []
    for cycle in range(1, args.cycles + 1):
        port = open_when_available(args.port, args.wait_seconds)
        captured = bytearray()
        disconnect = ""
        deadline = time.monotonic() + args.capture_seconds
        try:
            while time.monotonic() < deadline:
                try:
                    captured.extend(port.read(port.in_waiting or 1))
                except (OSError, serial.SerialException) as error:
                    disconnect = str(error)
                    break
        finally:
            port.close()
        output = args.output_prefix.with_name(f"{args.output_prefix.name}-cycle{cycle}.log")
        output.write_bytes(captured)
        decoded = captured.decode("utf-8", errors="replace")
        summaries.append((cycle, len(captured), disconnect, decoded[-5000:]))
        print(f"CYCLE {cycle} bytes={len(captured)} disconnect={disconnect!r}", flush=True)
        print(decoded[-5000:], flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
