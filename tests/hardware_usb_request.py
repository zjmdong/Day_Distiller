"""Send one explicitly non-destructive USB Link hardware-test request."""

from __future__ import annotations

import argparse
import json

import serial

from hardware_usb_smoke import transact


READ_ONLY_COMMANDS = {1, 2, 3, 4, 5, 10, 11}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--command", required=True, type=int, choices=sorted(READ_ONLY_COMMANDS))
    parser.add_argument("--payload")
    args = parser.parse_args()
    safe_defaults = {
        4: {"access": "ro"},
        5: {"force": False, "next_mode": "maintenance"},
        10: {"cursor": 0, "limit": 20},
        11: {"cursor": 0, "limit": 20},
    }
    payload = json.loads(args.payload) if args.payload is not None else safe_defaults.get(args.command, {})
    if not isinstance(payload, dict):
        raise AssertionError("payload must be a JSON object")
    if args.command == 4:
        assert payload.get("access") == "ro", "hardware test only permits read-only MSC"
        assert not payload.get("export_ids"), "hardware test does not select export transactions"
    if args.command == 5:
        assert payload.get("force") is False, "hardware test requires host ejection before EXIT_MSC"

    with serial.Serial(args.port, 115200, timeout=0.05, write_timeout=1.0) as port:
        port.dtr = False
        port.rts = False
        response, latency_ms, frame_bytes = transact(port, 1001, args.command, payload)
    print(
        json.dumps(
            {
                "result": "PASS",
                "command": args.command,
                "latency_ms": round(latency_ms, 2),
                "frame_bytes": frame_bytes,
                "response": response,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
