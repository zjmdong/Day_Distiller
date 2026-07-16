from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import DeliveryResult


@dataclass(frozen=True)
class ResendSettings:
    sender: str
    recipient: str
    base_url: str = "https://api.resend.com"
    timeout_seconds: float = 30.0

    def validate(self) -> None:
        if not self.sender.strip() or not self.recipient.strip():
            raise ValueError("Resend sender and recipient are required")
        if not self.base_url.startswith(("https://", "http://")):
            raise ValueError("Resend Base URL must be an HTTP(S) URL")


class ResendMailProvider:
    def __init__(
        self,
        settings: ResendSettings,
        api_key: str,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        settings.validate()
        if not api_key.strip():
            raise ValueError("Resend API Key is required")
        self.settings = settings
        self.api_key = api_key
        self.opener = opener

    def send(
        self,
        subject: str,
        plain_text: str,
        html: str,
        pdf_path: Path,
        inline_images: dict[str, Path],
        message_id: str,
    ) -> DeliveryResult:
        attachments = [
            {
                "filename": Path(pdf_path).name,
                "content": base64.b64encode(Path(pdf_path).read_bytes()).decode("ascii"),
                "content_disposition": "attachment",
            }
        ]
        for content_id, image_path in inline_images.items():
            attachments.append(
                {
                    "filename": Path(image_path).name,
                    "content": base64.b64encode(Path(image_path).read_bytes()).decode("ascii"),
                    "content_id": content_id,
                    "content_disposition": "inline",
                }
            )
        payload = json.dumps(
            {
                "from": self.settings.sender,
                "to": [self.settings.recipient],
                "subject": subject,
                "text": plain_text,
                "html": html,
                "attachments": attachments,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        idempotency_key = message_id.strip("<>").replace("@", "-")[:256]
        request = Request(
            self.settings.base_url.rstrip("/") + "/emails",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
        )
        try:
            with self.opener(request, timeout=self.settings.timeout_seconds) as response:
                body = response.read().decode("utf-8")
                status = int(getattr(response, "status", 200))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Resend rejected the email ({exc.code}): {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Cannot reach Resend: {exc.reason}") from exc
        if not 200 <= status < 300:
            raise RuntimeError(f"Resend rejected the email ({status}): {body}")
        try:
            resend_id = str(json.loads(body)["id"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Resend response did not contain an email id") from exc
        return DeliveryResult(True, message_id, f"Resend accepted email: {resend_id}")
