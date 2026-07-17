import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


class LedControllerContractTests(unittest.TestCase):
    def test_only_led_controller_writes_strip(self):
        writers = []
        for source in (ROOT / "main").rglob("*.c"):
            text = source.read_text(encoding="utf-8")
            if "led_strip_set_pixel" in text or "led_strip_refresh" in text:
                writers.append(source.relative_to(ROOT).as_posix())
        self.assertEqual(writers, ["main/src/drivers/led_status.c"])

    def test_controller_has_priority_states_and_30fps_breathing(self):
        source = read("main/src/drivers/led_status.c")
        for token in ("battery_lock", "fatal_error", "recording", "preview",
                      "usb_handshake", "usb_enumerated", "base_mode"):
            self.assertIn(token, source)
        self.assertIn("ms%2400", source)
        self.assertRegex(source, r"pdMS_TO_TICKS\(33\)")
        self.assertIn("scale_channel", source)

    def test_preview_is_strict_and_volatile(self):
        protocol = read("main/src/usb/usb_protocol.c")
        link = read("main/src/usb/usb_link.c")
        self.assertIn("duration<250 || duration>5000", protocol)
        self.assertIn("brightness<5 || brightness>100", protocol)
        handler = link[link.index("case DAY_USB_CMD_PREVIEW_LED"):]
        handler = handler[:handler.index("case DAY_USB_CMD_EXIT_MSC")]
        self.assertIn("day_led_preview", handler)
        self.assertNotIn("day_settings_", handler)
        self.assertNotIn("nvs", handler.lower())
        self.assertIn('"led_preview"', link)

    def test_frozen_commands_remain_unchanged(self):
        header = read("main/include/usb_protocol.h")
        for name, value in {
            "HELLO": 1, "PING": 2, "GET_STATUS": 3, "ENTER_MSC": 4,
            "EXIT_MSC": 5, "BEGIN_EXPORT": 6, "COMMIT_EXPORT_DELETE": 7,
            "ABORT_EXPORT": 8, "END_SESSION": 9, "GET_EXPORT_STATUS": 10,
            "LIST_RECORD_DATES": 11,
        }.items():
            self.assertRegex(header, rf"DAY_USB_CMD_{name}\s*=\s*{value}\b")
        self.assertIn("#define DAY_USB_PROTO_VERSION 1", header)


if __name__ == "__main__":
    unittest.main()
