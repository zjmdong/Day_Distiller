from __future__ import annotations

from enum import StrEnum


SERVICE_NAME = "DayDistillerV2"


class CredentialName(StrEnum):
    OPENAI_API_KEY = "openai_api_key"
    SMTP_PASSWORD = "smtp_password"


class CredentialStore:
    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self.service_name = service_name

    def get(self, name: CredentialName | str) -> str | None:
        import keyring

        return keyring.get_password(self.service_name, str(name))

    def set(self, name: CredentialName | str, secret: str) -> None:
        if not secret:
            raise ValueError("secret cannot be empty")
        import keyring

        keyring.set_password(self.service_name, str(name), secret)

    def delete(self, name: CredentialName | str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self.service_name, str(name))
        except PasswordDeleteError:
            pass

