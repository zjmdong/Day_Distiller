import binascii
import json
import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HEADER = struct.Struct("<2sBBBBHIHHI")
FRAME_END = 0xC0
FRAME_ESC = 0xDB


def make_frame(sequence: int, command: int, payload: dict) -> bytes:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = HEADER.pack(b"DD", 1, 1, 0, 0, 20, sequence, command, 0, len(encoded))
    body = header + encoded
    return body + struct.pack("<I", binascii.crc32(body) & 0xFFFFFFFF)


def slip_encode(frame: bytes) -> bytes:
    output = bytearray([FRAME_END])
    for value in frame:
        if value == FRAME_END:
            output.extend((FRAME_ESC, 0xDC))
        elif value == FRAME_ESC:
            output.extend((FRAME_ESC, 0xDD))
        else:
            output.append(value)
    output.append(FRAME_END)
    return bytes(output)


def slip_decode(stream: bytes) -> bytes:
    output = bytearray()
    escaped = False
    for value in stream[1:-1]:
        if escaped:
            output.append(FRAME_END if value == 0xDC else FRAME_ESC)
            escaped = False
        elif value == FRAME_ESC:
            escaped = True
        else:
            output.append(value)
    return bytes(output)


class UsbProtocolContractTests(unittest.TestCase):
    def test_wire_header_remains_twenty_bytes_and_protocol_one(self):
        self.assertEqual(HEADER.size, 20)
        frame = make_frame(17, 1, {})
        fields = HEADER.unpack(frame[:20])
        self.assertEqual(fields[:3], (b"DD", 1, 1))
        self.assertEqual(fields[5], 20)

    def test_slip_and_crc_round_trip_reserved_bytes(self):
        frame = make_frame(0xDBC0, 2, {"bytes": "\u00c0\u00db"})
        decoded = slip_decode(slip_encode(frame))
        self.assertEqual(decoded, frame)
        expected_crc = struct.unpack("<I", decoded[-4:])[0]
        self.assertEqual(expected_crc, binascii.crc32(decoded[:-4]) & 0xFFFFFFFF)

    def test_frozen_command_and_status_ids_are_unchanged(self):
        protocol = (ROOT / "main/include/usb_protocol.h").read_text(encoding="utf-8")
        for name, value in {
            "HELLO": 1,
            "PING": 2,
            "GET_STATUS": 3,
            "ENTER_MSC": 4,
            "EXIT_MSC": 5,
        }.items():
            self.assertIn(f"DAY_USB_CMD_{name} = {value}", protocol)
        for name, value in {
            "OK": 0,
            "BAD_FRAME": 1,
            "UNSUPPORTED_VERSION": 2,
            "UNSUPPORTED_CMD": 3,
            "INVALID_ARG": 4,
            "BUSY": 5,
            "STORAGE_ERROR": 6,
            "BAD_STATE": 7,
            "TIMEOUT": 8,
        }.items():
            self.assertIn(f"DAY_USB_STATUS_{name} = {value}", protocol)

    def test_usb_identity_and_interface_layout_remain_compatible(self):
        source = (ROOT / "main/src/usb/usb_link.c").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count(".idVendor = 0x303A"), 2)
        self.assertIn(".idProduct = 0x4020", source)
        self.assertIn(".idProduct = 0x4021", source)
        self.assertIn("2 * TUD_CDC_DESC_LEN", source)
        self.assertIn("TUD_CDC_DESC_LEN + TUD_MSC_DESC_LEN", source)

    def test_primary_cdc_is_the_protocol_port_for_windows_usbser(self):
        source = (ROOT / "main/src/usb/usb_link.c").read_text(encoding="utf-8")
        self.assertIn("static tinyusb_cdcacm_itf_t s_protocol_port = TINYUSB_CDC_ACM_0", source)
        self.assertIn("init_cdc_port(TINYUSB_CDC_ACM_0, true)", source)
        self.assertIn("init_cdc_port(TINYUSB_CDC_ACM_1, false)", source)

    def test_protocol_buffers_do_not_exhaust_the_protocol_task_stack(self):
        source = (ROOT / "main/src/usb/usb_link.c").read_text(encoding="utf-8")
        self.assertIn("uint8_t *frame = malloc(frame_len)", source)
        self.assertIn("day_usb_rx_msg_t *msg = malloc(sizeof(*msg))", source)
        self.assertIn('xTaskCreate(protocol_task, "day_usb_proto", 8192', source)

    def test_cdc_transmit_handles_frames_larger_than_tinyusb_queue(self):
        source = (ROOT / "main/src/usb/usb_link.c").read_text(encoding="utf-8")
        defaults = (ROOT / "sdkconfig.defaults").read_text(encoding="utf-8")
        self.assertIn("CONFIG_TINYUSB_CDC_TX_BUFSIZE=1024", defaults)
        self.assertIn("size_t queued = tinyusb_cdcacm_write_queue(", source)
        self.assertIn("offset += queued", source)
        self.assertIn("offset < encoded_len && queued == 0", source)
        self.assertIn("free(encoded)", source)

    def test_arguments_use_structured_json_not_substring_matching(self):
        link = (ROOT / "main/src/usb/usb_link.c").read_text(encoding="utf-8")
        parser = (ROOT / "main/src/usb/usb_protocol.c").read_text(encoding="utf-8")
        self.assertNotIn("strstr(", link)
        self.assertIn("cJSON_ParseWithLengthOpts", parser)
        self.assertIn("duplicate_field", parser)
        self.assertIn("invalid_utf8", parser)

    def test_status_additions_do_not_remove_legacy_fields(self):
        source = (ROOT / "main/src/usb/usb_link.c").read_text(encoding="utf-8")
        for field in (
            "protocol",
            "device",
            "mode",
            "maintenance",
            "recording",
            "usb_full_speed",
            "storage",
            "capabilities",
            "firmware_version",
            "device_id",
            "runtime_state",
            "metadata_schemas",
        ):
            self.assertIn(f'"{field}"', source)


if __name__ == "__main__":
    unittest.main()
