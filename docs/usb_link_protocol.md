# Day Distiller USB Link Protocol

Protocol version: 1

USB Link is an optional maintenance interface layered beside the existing
application behavior. The normal boot mode exposes two TinyUSB CDC ACM
interfaces:

- CDC0: USB Link command protocol (firmware 2.0.1 and later).
- CDC1: device logs and debugging output (firmware 2.0.1 and later).

Firmware 2.0.0 used the opposite assignment. Desktop clients must probe both
CDC functions with `HELLO` so that 2.0.0 and 2.0.1 devices remain compatible.

After a successful `ENTER_MSC` request the device reboots into maintenance
storage mode and exposes:

- CDC0: USB Link command protocol.
- MSC0: TF card as a USB mass-storage device.

The ESP32-S3 USB full-speed device limit means maintenance storage mode uses
one protocol CDC plus MSC. Log CDC is restored after returning to serial mode.

## Framing

Frames are sent over the protocol CDC port using SLIP:

- `0xC0`: frame delimiter.
- `0xDB 0xDC`: escaped `0xC0`.
- `0xDB 0xDD`: escaped `0xDB`.

Each decoded frame is:

| Offset | Size | Field |
| --- | ---: | --- |
| 0 | 2 | Magic: `DD` |
| 2 | 1 | Version: `1` |
| 3 | 1 | Type: request `1`, response `2`, event `3` |
| 4 | 1 | Flags, reserved for future use |
| 5 | 1 | Reserved |
| 6 | 2 | Header length, little-endian, currently `20` |
| 8 | 4 | Sequence number, little-endian |
| 12 | 2 | Command id, little-endian |
| 14 | 2 | Status id, little-endian |
| 16 | 4 | JSON payload length, little-endian |
| 20 | N | UTF-8 JSON payload |
| 20+N | 4 | CRC32 little-endian over header and payload |

Maximum decoded frame size is 2048 bytes. Maximum JSON payload size is 1600
bytes. Unknown frame types are ignored.

## Commands

| Id | Name | Request payload | Response payload |
| ---: | --- | --- | --- |
| 1 | `HELLO` | `{}` | Status JSON |
| 2 | `PING` | `{}` | Status JSON |
| 3 | `GET_STATUS` | `{}` | Status JSON |
| 4 | `ENTER_MSC` | `{"access":"rw"}` or `{"access":"ro"}` | Status JSON, then reboot |
| 5 | `EXIT_MSC` | `{"force":false}` | Status JSON, then reboot |

`ENTER_MSC` is accepted only in serial mode and is rejected while recording is
active. `EXIT_MSC` requires the host to eject the volume first unless
`force:true` is supplied.

## Status Codes

| Id | Name |
| ---: | --- |
| 0 | `OK` |
| 1 | `BAD_FRAME` |
| 2 | `UNSUPPORTED_VERSION` |
| 3 | `UNSUPPORTED_CMD` |
| 4 | `INVALID_ARG` |
| 5 | `BUSY` |
| 6 | `STORAGE_ERROR` |
| 7 | `BAD_STATE` |
| 8 | `TIMEOUT` |

## Status JSON

Status responses contain:

```json
{
  "protocol": 1,
  "device": "Day Distiller",
  "mode": "serial",
  "maintenance": true,
  "recording": false,
  "usb_full_speed": true,
  "storage": {
    "ready": true,
    "mounted": true,
    "usb_exposed": false,
    "total_bytes": 0,
    "free_bytes": 0,
    "last_error": 0
  },
  "capabilities": ["enter_msc", "exit_msc", "msc_rw", "msc_ro", "slip_crc32_json"]
}
```

In MSC mode, `storage` reports `usb_exposed`, `read_only`, `ejected`,
`sector_size`, `sector_count`, and `total_bytes`.
