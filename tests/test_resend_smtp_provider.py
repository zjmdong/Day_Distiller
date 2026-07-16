import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from day_distiller_client.providers.smtp_provider import SmtpMailProvider, SmtpSettings


class _SmtpClient:
    instances = []

    def __init__(self, host, port, **kwargs):
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.login_args = None
        self.message = None
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.message = message
        return {}


class ResendSmtpProviderTests(unittest.TestCase):
    def test_uses_resend_smtp_credentials_and_mime_attachments(self) -> None:
        _SmtpClient.instances.clear()
        provider = SmtpMailProvider(
            SmtpSettings(
                host="smtp.resend.com",
                port=465,
                security="ssl",
                username="resend",
                sender="Day Distiller <daily@example.com>",
                recipient="me@example.net",
            ),
            "re_test",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "daily.pdf"
            image = root / "panel.jpg"
            pdf.write_bytes(b"pdf")
            image.write_bytes(b"jpeg")
            with patch("smtplib.SMTP_SSL", _SmtpClient):
                result = provider.send(
                    "日报",
                    "plain",
                    '<img src="cid:panel-1">',
                    pdf,
                    {"panel-1": image},
                    "<day-distiller-job@example.local>",
                )

        client = _SmtpClient.instances[0]
        self.assertTrue(result.accepted)
        self.assertEqual((client.host, client.port), ("smtp.resend.com", 465))
        self.assertEqual(client.login_args, ("resend", "re_test"))
        self.assertEqual(client.message["Message-ID"], "<day-distiller-job@example.local>")
        self.assertEqual(
            client.message["Resend-Idempotency-Key"], "day-distiller-job-example.local"
        )
        attachment_names = {
            part.get_filename() for part in client.message.iter_attachments()
        }
        self.assertEqual(attachment_names, {"daily.pdf", "panel.jpg"})
        inline = next(
            part for part in client.message.walk() if part.get("Content-ID") == "<panel-1>"
        )
        self.assertEqual(inline.get_content_disposition(), "inline")
        self.assertEqual(inline["Content-Location"], "panel.jpg")
