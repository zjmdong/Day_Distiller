# Milestone 2 hardware issue log

Date: 2026-07-17 (Asia/Shanghai)
Device: ESP32-S3, MAC 94:a9:90:e1:8a:98
Firmware image before fix: SHA-256 329C5A352AF752C2F39F8A626F46A123C5B5054AD3263DDC2227D6A1B022C43A

## ROM-to-application reset transient

Reproduction:

1. Flash on ROM USB Serial/JTAG COM5 with `idf.py -p COM5 flash`.
2. Run `esptool --before no-reset --after watchdog-reset run`.

Observed result: esptool reported Windows pySerial `OSError(22)` while the ROM stub disconnected, then executed the watchdog hard reset. Four seconds later the application enumerated successfully as PID 0x4020, CDC0 COM8 and CDC1 COM9. No pin or hardware parameter was changed.

Minimal result: application HELLO responded with firmware 2.1.0; the reset exception was transient and did not prevent boot.

## MSC GET_STATUS incomplete frame

Reproduction:

1. Enter MSC using command 4 with `{"access":"ro"}`.
2. Confirm Windows disk `TinyUSB TEST MSC Storage` is online, healthy, and `IsReadOnly=True`.
3. On MSC CDC COM10, command 1 HELLO succeeds (880-byte decoded frame).
4. On the same CDC port, command 3 GET_STATUS times out after two seconds. Repeating command 1 still succeeds; repeating command 3 still times out.

Minimal result: no panic or reset occurred. The 1024-byte TinyUSB CDC TX queue was smaller than the approximately 1.3 KB detailed response. The sender ignored partial queue writes, so MSC traffic could cause the frame tail to be dropped. The fix encodes on heap, checks every queued byte count, flushes when full, and only reports success after the whole frame is flushed.

Safety result: the MSC disk remained read-only. The volume was ejected through the Windows shell; HELLO then reported `ejected:true`, and EXIT_MSC returned to serial maintenance. No export-delete or filesystem-write command was sent.
