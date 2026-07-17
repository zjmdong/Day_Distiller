import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from day_distiller_client.protocol import (
    Command,
    FrameType,
    ProtocolError,
    SlipDecoder,
    Status,
    build_frame,
    build_request,
    parse_frame,
    slip_encode,
)


class ProtocolTests(unittest.TestCase):
    def test_request_round_trip(self) -> None:
        raw = build_request(Command.ENTER_MSC, 42, {"access": "ro"})
        frame = parse_frame(raw)

        self.assertEqual(frame.frame_type, FrameType.REQUEST)
        self.assertEqual(frame.command, Command.ENTER_MSC)
        self.assertEqual(frame.status, Status.OK)
        self.assertEqual(frame.sequence, 42)
        self.assertEqual(frame.payload_json(), {"access": "ro"})

    def test_response_round_trip(self) -> None:
        raw = build_frame(FrameType.RESPONSE, Command.HELLO, 7, {"protocol": 1})
        frame = parse_frame(raw)

        self.assertEqual(frame.frame_type, FrameType.RESPONSE)
        self.assertEqual(frame.command, Command.HELLO)
        self.assertEqual(frame.payload_json()["protocol"], 1)

    def test_firmware_v2_command_ids_keep_protocol_v1_framing(self) -> None:
        raw = build_request(Command.LIST_RECORD_DATES, 11, {"cursor": 0, "limit": 15})
        frame = parse_frame(raw)

        self.assertEqual(frame.command, Command.LIST_RECORD_DATES)
        self.assertEqual(frame.payload_json(), {"cursor": 0, "limit": 15})

    def test_crc_failure(self) -> None:
        raw = bytearray(build_request(Command.PING, 1, {}))
        raw[-1] ^= 0x55

        with self.assertRaises(ProtocolError):
            parse_frame(bytes(raw))

    def test_payload_length_failure(self) -> None:
        raw = bytearray(build_request(Command.PING, 1, {}))
        struct.pack_into("<I", raw, 16, 999)
        body = bytes(raw[:-4])
        crc = __import__("binascii").crc32(body) & 0xFFFFFFFF
        struct.pack_into("<I", raw, len(raw) - 4, crc)

        with self.assertRaises(ProtocolError):
            parse_frame(bytes(raw))

    def test_slip_decoder_chunked(self) -> None:
        raw = build_request(Command.PING, 99, {"bytes": "\u00c0\u00db"})
        encoded = slip_encode(raw)
        decoder = SlipDecoder()

        frames = []
        for byte in encoded:
            frames.extend(decoder.feed(bytes([byte])))

        self.assertEqual(len(frames), 1)
        self.assertEqual(parse_frame(frames[0]).sequence, 99)


if __name__ == "__main__":
    unittest.main()
