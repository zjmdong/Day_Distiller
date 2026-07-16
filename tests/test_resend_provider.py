import base64
import json
import tempfile
import unittest
from pathlib import Path

from day_distiller_client.providers.resend_provider import ResendMailProvider, ResendSettings


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"id":"email_123"}'


class ResendProviderTests(unittest.TestCase):
    def test_sends_inline_images_pdf_and_idempotency_key(self) -> None:
        captured = {}

        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _Response()

        provider = ResendMailProvider(
            ResendSettings("Day Distiller <daily@example.com>", "me@example.net"),
            "re_test",
            opener,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "daily.pdf"
            image = root / "panel.jpg"
            pdf.write_bytes(b"pdf")
            image.write_bytes(b"jpeg")
            result = provider.send(
                "日报", "plain", '<img src="cid:panel-1">', pdf, {"panel-1": image},
                "<day-distiller-job@example.local>",
            )
        payload = json.loads(captured["request"].data)
        self.assertTrue(result.accepted)
        self.assertEqual(payload["attachments"][0]["content"], base64.b64encode(b"pdf").decode())
        self.assertEqual(payload["attachments"][1]["content_id"], "panel-1")
        self.assertEqual(captured["request"].headers["Idempotency-key"], "day-distiller-job-example.local")


if __name__ == "__main__":
    unittest.main()
