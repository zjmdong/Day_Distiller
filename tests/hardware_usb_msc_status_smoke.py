"""Read-only MSC/CDC coexistence smoke test for long USB Link frames."""

from __future__ import annotations

import argparse
import json

import serial

from hardware_usb_config_smoke import BAD_STATE, request_ok, transact_status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--expected-firmware", default="2.1.1")
    args = parser.parse_args()
    assert args.iterations > 0

    latencies: list[float] = []
    frame_sizes: list[int] = []
    sequence = 3001
    with serial.Serial(args.port, 115200, timeout=0.05, write_timeout=1.0) as port:
        port.dtr = False
        port.rts = False
        hello, latency, frame = request_ok(port, sequence, 1, {})
        sequence += 1
        latencies.append(latency)
        frame_sizes.append(frame)
        assert hello.get("firmware_version") == args.expected_firmware
        assert hello.get("mode") == "msc"
        assert hello.get("storage", {}).get("read_only") is True

        last_status = {}
        for _ in range(args.iterations):
            last_status, latency, frame = request_ok(port, sequence, 3, {})
            sequence += 1
            latencies.append(latency)
            frame_sizes.append(frame)
            assert last_status.get("mode") == "msc"
            assert last_status.get("storage", {}).get("read_only") is True
            assert last_status.get("storage", {}).get("usb_exposed") is True
            assert "battery" in last_status and "rtc" in last_status

        status, response, latency, frame = transact_status(port, sequence, 12, {})
        latencies.append(latency)
        frame_sizes.append(frame)
        assert status == BAD_STATE, response
        assert response.get("reason") == "serial_maintenance_required"

    assert max(latencies) < 100.0, max(latencies)
    print(
        json.dumps(
            {
                "result": "PASS",
                "requests": len(latencies),
                "max_latency_ms": round(max(latencies), 2),
                "max_frame_bytes": max(frame_sizes),
                "mode": last_status.get("mode"),
                "read_only": last_status.get("storage", {}).get("read_only"),
                "config_rejected_in_msc": True,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
