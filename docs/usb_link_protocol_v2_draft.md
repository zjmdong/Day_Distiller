# Day Distiller USB Export Protocol v2 Draft

Status: design only; the current firmware still implements USB Link protocol 1.

This document defines additive commands for transactional day export. It does
not change the existing SLIP framing, 20-byte header, CRC32, command IDs 1-5,
or serial/MSC USB descriptors described in `usb_link_protocol.md`.

## Compatibility rules

- A host must start with `HELLO` and inspect `protocol` and `capabilities`.
- A device that does not advertise `export_transactions` is handled with the
  existing protocol-1 MSC workflow.
- New commands use the existing request/response JSON framing and status codes.
- Existing `ENTER_MSC` and `EXIT_MSC` payloads remain valid.
- A v2-capable host must never require these commands to import protocol-1
  recordings.

## Proposed capabilities

```json
[
  "export_transactions",
  "export_manifest_v2",
  "exit_to_maintenance",
  "commit_export_delete",
  "abort_export",
  "end_session"
]
```

## Proposed commands

| Id | Name | Valid mode | Purpose |
| ---: | --- | --- | --- |
| 6 | `BEGIN_EXPORT` | serial | Freeze the record set for one local date and create a manifest. |
| 7 | `COMMIT_EXPORT_DELETE` | serial | Delete only records referenced by a completed export transaction. |
| 8 | `ABORT_EXPORT` | serial | Close the transaction without deleting recordings. |
| 9 | `END_SESSION` | serial | End host maintenance and allow the normal sleep path. |

`EXIT_MSC` gains the optional payload field `next_mode`. The value
`"maintenance"` requests a reboot back to serial maintenance mode without an
automatic recording between USB modes.

### BEGIN_EXPORT

Request:

```json
{"date":"2026-07-15"}
```

Response:

```json
{
  "export_id":"019f6403-df26-7c20-891b-7bc733ef35b5",
  "date":"2026-07-15",
  "record_count":144,
  "total_bytes":123456789,
  "manifest_path":"/EXPORTS/019f6403-df26-7c20-891b-7bc733ef35b5.json"
}
```

The manifest is stored on the TF card because the protocol JSON payload is
limited to 1600 bytes. It contains the immutable list of record directory
names and file sizes. A host computes SHA-256 while copying and stores those
digests in its local import manifest.

### COMMIT_EXPORT_DELETE

Request:

```json
{"export_id":"019f6403-df26-7c20-891b-7bc733ef35b5"}
```

The firmware validates the transaction, deletes only the record directories
listed in its manifest, and keeps the manifest with a committed status until a
later housekeeping pass. Repeated commits are idempotent.

### ABORT_EXPORT

Request:

```json
{"export_id":"019f6403-df26-7c20-891b-7bc733ef35b5"}
```

Aborting never deletes recordings and is idempotent.

### END_SESSION

Request: `{}`. The response is sent before the maintenance flag is cleared.
The application then follows the existing deep-sleep configuration.

## Proposed recording metadata schema 2

Each record retains the current directory naming convention and media file
names. `meta.json` adds:

- `schema_version`, `device_id`, and globally unique `record_id`;
- UTC start time in milliseconds, timezone, and wake reason;
- actual duration and per-stream start/end offsets;
- video frame timestamp index, audio first-sample offset, and IMU timing data;
- per-stream size, count, sample rate or FPS, and error status.

Protocol-1 metadata remains readable. Hosts derive missing absolute time from
the record directory and treat missing stream offsets as unknown instead of
zero.

## Safety and recovery invariants

1. MSC import is read-only.
2. A commit is allowed only after returning to serial mode.
3. A commit can delete only paths captured by `BEGIN_EXPORT`.
4. A power loss before commit preserves recordings.
5. A power loss during commit can be resumed idempotently.
6. New recordings created after `BEGIN_EXPORT` are never part of that export.
7. The desktop client records delivery success before requesting deletion.

## Compatibility test matrix

| Host | Firmware | Expected result |
| --- | --- | --- |
| v1 | v1 | Existing manual MSC workflow. |
| v2 | v1 | Legacy read-only import and verified host-managed cleanup. |
| v1 | v2 | Existing command IDs 1-5 continue to work. |
| v2 | v2 | Transactional export and device-managed commit/delete. |

