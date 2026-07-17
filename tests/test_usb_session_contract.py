import binascii
import struct
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INTENT_WITHOUT_CRC = struct.Struct("<IHBBQ40s")


def make_intent(target: int = 2, access: int = 1, session_id: int = 0x8F31C8D4D6BE4D54) -> bytes:
    body = INTENT_WITHOUT_CRC.pack(
        0x44445553,
        1,
        target,
        access,
        session_id,
        b"exp-DD-A1B2C3D4E5F6-019f6403\0",
    )
    return body + struct.pack("<I", binascii.crc32(body) & 0xFFFFFFFF)


def valid_intent(value: bytes) -> bool:
    if len(value) != 60:
        return False
    magic, schema, target, access, session_id, active_export = INTENT_WITHOUT_CRC.unpack(value[:-4])
    stored_crc = struct.unpack("<I", value[-4:])[0]
    return (
        magic == 0x44445553
        and schema == 1
        and target in (1, 2)
        and access in (0, 1)
        and session_id != 0
        and b"\0" in active_export
        and stored_crc == (binascii.crc32(value[:-4]) & 0xFFFFFFFF)
    )


class UsbSessionContractTests(unittest.TestCase):
    def test_boot_intent_layout_and_crc(self):
        value = make_intent()
        self.assertEqual(len(value), 60)
        self.assertTrue(valid_intent(value))
        corrupted = bytearray(value)
        corrupted[20] ^= 0x01
        self.assertFalse(valid_intent(bytes(corrupted)))

    def test_invalid_schema_target_access_and_empty_session_are_rejected(self):
        self.assertFalse(valid_intent(make_intent(target=0)))
        self.assertFalse(valid_intent(make_intent(target=3)))
        self.assertFalse(valid_intent(make_intent(access=2)))
        self.assertFalse(valid_intent(make_intent(session_id=0)))

    def test_firmware_validation_checks_every_boot_intent_guard(self):
        source = (ROOT / "main/src/usb/usb_session.c").read_text(encoding="utf-8")
        for guard in (
            "DAY_USB_BOOT_INTENT_MAGIC",
            "DAY_USB_BOOT_INTENT_SCHEMA",
            "target_valid",
            "DAY_USB_ACCESS_RW",
            "DAY_USB_ACCESS_RO",
            "session_id != 0",
            "intent_crc",
        ):
            self.assertIn(guard, source)

    def test_exit_to_maintenance_is_persisted_before_restart(self):
        source = (ROOT / "main/src/usb/usb_link.c").read_text(encoding="utf-8")
        exit_case = source.index("case DAY_USB_CMD_EXIT_MSC")
        persist = source.index("day_usb_session_prepare_serial_maintenance", exit_case)
        restart = source.index("restart_after_response", persist)
        self.assertLess(persist, restart)

    def test_end_session_never_calls_record_or_export_cleanup(self):
        source = (ROOT / "main/src/usb/usb_link.c").read_text(encoding="utf-8")
        start = source.index("case DAY_USB_CMD_END_SESSION")
        end = source.index("default:", start)
        body = source[start:end]
        self.assertIn("day_usb_session_end", body)
        self.assertNotIn("remove(", body)
        self.assertNotIn("unlink(", body)
        self.assertNotIn("record_once", body)

    def test_maintenance_boot_bypasses_portal_and_recording(self):
        source = (ROOT / "main/src/app_core.c").read_text(encoding="utf-8")
        self.assertIn("cold_boot && !maintenance_start", source)
        self.assertIn("if (!day_usb_link_maintenance_active())", source)
        self.assertIn("while (day_usb_link_maintenance_active())", source)


if __name__ == "__main__":
    unittest.main()
