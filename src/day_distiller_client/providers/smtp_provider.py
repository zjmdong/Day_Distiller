from __future__ import annotations

import mimetypes
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from .base import DeliveryResult


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    username: str
    sender: str
    recipient: str
    security: str = "ssl"
    timeout_seconds: float = 30.0

    def validate(self) -> None:
        if not self.host or not self.sender or not self.recipient:
            raise ValueError("SMTP host, sender and recipient are required")
        if not (1 <= self.port <= 65535):
            raise ValueError("SMTP port is invalid")
        if self.security not in {"ssl", "starttls", "none"}:
            raise ValueError("SMTP security must be ssl, starttls or none")


class SmtpMailProvider:
    def __init__(self, settings: SmtpSettings, password: str) -> None:
        settings.validate()
        self.settings = settings
        self.password = password

    def send(
        self,
        subject: str,
        plain_text: str,
        html: str,
        pdf_path: Path,
        inline_images: dict[str, Path],
        message_id: str,
    ) -> DeliveryResult:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.sender
        message["To"] = self.settings.recipient
        message["Message-ID"] = message_id
        message.set_content(plain_text)
        message.add_alternative(html, subtype="html")
        html_part = message.get_payload()[-1]
        for content_id, image_path in inline_images.items():
            mime, _encoding = mimetypes.guess_type(image_path.name)
            maintype, subtype = (mime or "image/jpeg").split("/", 1)
            html_part.add_related(
                image_path.read_bytes(),
                maintype=maintype,
                subtype=subtype,
                cid=f"<{content_id}>",
                filename=image_path.name,
            )
        message.add_attachment(
            Path(pdf_path).read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=Path(pdf_path).name,
        )

        context = ssl.create_default_context()
        if self.settings.security == "ssl":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self.settings.host, self.settings.port, timeout=self.settings.timeout_seconds, context=context
            )
        else:
            client = smtplib.SMTP(self.settings.host, self.settings.port, timeout=self.settings.timeout_seconds)
        with client:
            if self.settings.security == "starttls":
                client.starttls(context=context)
            if self.settings.username:
                client.login(self.settings.username, self.password)
            refused = client.send_message(message)
        if refused:
            return DeliveryResult(False, message_id, f"recipients refused: {refused}")
        return DeliveryResult(True, message_id, "SMTP server accepted message")

