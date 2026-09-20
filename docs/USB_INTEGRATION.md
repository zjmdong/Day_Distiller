# USB integration

[Project](../README.md) · [Setup](GETTING_STARTED.md) · [Hardware](HARDWARE.md)

This guide describes firmware **2.1.0** on `firmware-production`. The wire protocol remains **version 1**; feature version numbers do not imply a new wire format. Use the current C headers and handlers as the authoritative contract.

## Interfaces and discovery

| Mode | Interfaces | VID / PID |
| :--- | :--- | :--- |
| Serial | CDC0: protocol; CDC1: logs | `0x303A / 0x4020` |
| Storage maintenance | One protocol CDC + USB mass storage | `0x303A / 0x4021` |

`ENTER_MSC` and `EXIT_MSC` change USB mode and can reboot/re-enumerate the device. Rediscover ports after a transition rather than retaining a stale COM-port name. Firmware 2.0.0 used the opposite protocol/log CDC assignment; a backwards-compatible client probes with `HELLO` rather than relying only on interface order.

Use `HELLO` to obtain identity and capabilities, then enable only advertised features. Ordinary status polling must not be interpreted as a fresh capability negotiation. `GET_STATUS` is backed by cached device status, not a request to start the sensors.

## Wire format

The [original framing reference](usb_link_protocol.md#framing) describes the stable SLIP/CRC32 envelope. Its command/status examples describe the earlier, smaller interface, **not** the full current command set.

- SLIP delimiter `0xC0`, with escaped delimiter/escape bytes.
- A 20-byte little-endian header beginning with `DD`, including protocol version, type, sequence number, command, status and JSON length.
- UTF-8 JSON payload, followed by a little-endian CRC32 over header and payload.
- Current limits: **2048-byte decoded frame**, **1600-byte JSON payload**.

Definitions: [`usb_protocol.h`](../main/include/usb_protocol.h) and [`usb_protocol.c`](../main/src/usb/usb_protocol.c). Host implementation: [`protocol.py`](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/src/day_distiller_client/protocol.py).

## Command families

| ID | Command | Purpose |
| ---: | :--- | :--- |
| 1 | `HELLO` | Identify device and discover capabilities |
| 2 | `PING` | Keep the maintenance session alive |
| 3 | `GET_STATUS` | Read cached device state |
| 4–5 | `ENTER_MSC`, `EXIT_MSC` | Transfer storage ownership and change USB mode |
| 6 | `BEGIN_EXPORT` | Prepare a date-scoped export transaction |
| 7 | `COMMIT_EXPORT_DELETE` | Explicitly commit verified cleanup for an export |
| 8 | `ABORT_EXPORT` | Abort an export without implicitly deleting recordings |
| 9 | `END_SESSION` | End the maintenance session |
| 10 | `GET_EXPORT_STATUS` | Query/recover an export's state |
| 11 | `LIST_RECORD_DATES` | Enumerate available recording dates |
| 12–13 | `GET_CONFIG`, `SET_CONFIG` | Read/update validated settings with revision handling |
| 14 | `PREVIEW_LED` | Temporarily preview an LED configuration |

This table is an index, not a substitute for payload validation. For exact request fields, error cases and capability names, read [`usb_protocol.c`](../main/src/usb/usb_protocol.c), [`usb_link.c`](../main/src/usb/usb_link.c), [`usb_session.c`](../main/src/usb/usb_session.c) and their [contract tests](../tests).

## Import and deletion are separate operations

1. Discover the device and open a maintenance session.
2. For transactional-capable firmware, prepare an export and retain its identity and manifest hash.
3. Enter **read-only** MSC for import, copy only the intended records and verify file sizes and SHA-256 against the manifest.
4. Eject on the host before asking the firmware to exit MSC. Do not let the host filesystem and firmware mount/write the card concurrently.
5. Process the local copies. Use keepalives where required; retain enough state to recover an interruption.
6. Clean up only through the supported, explicit verification/commit flow. A timeout, disconnect, aborted transaction or failed journal is not permission to delete recordings.

Legacy firmware does not provide the same transactional guarantees. The desktop branch selects an adapter based on capabilities and performs additional host-side verification for legacy cleanup. Do not send modern deletion or settings commands to a device merely because it answers `HELLO`.

## Extending the protocol

- Keep existing IDs and version-1 framing compatible; advertise new functionality through capabilities.
- Validate malformed JSON, bounds, sequence handling, unexpected states and disconnected sessions on both implementations.
- Treat configuration revision conflicts as real conflicts; re-read before retrying a write.
- Keep secret values out of normal status messages and logs. USB secret-reading controls are **not authentication for the separate Wi-Fi web interface**.
- Test with synthetic data first. Review any tool capable of writing MSC sectors or deleting recordings before attaching a valuable card.

See also the desktop [firmware adaptation guide](https://github.com/zjmdong/Day_Distiller/blob/desktop-app/docs/firmware-2.1-desktop-adaptation.md).
