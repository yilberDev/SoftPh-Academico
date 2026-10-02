"""HU-003: invitation token, email composition and SMTP sender (no network)."""

import hashlib
import re
import string
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import pytest

from app.core.config import Settings
from app.core.sessions import generate_session_token, hash_token
from app.services import mail as mail_module
from app.services.invitation_email import SUBJECT, activation_link, build_invitation_email, format_bogota
from app.services.invitations import is_expired
from app.services.mail import OutgoingEmail, SmtpMailSender

pytestmark = pytest.mark.anyio

TOKEN = generate_session_token()
EXPIRES = datetime(2026, 10, 8, 20, 5)  # naive UTC → 3:05 p. m. in Bogotá (UTC-5)


def compose(**overrides: Any) -> OutgoingEmail:
    values = {
        "to": "residente@example.com",
        "full_name": "Laura Gómez",
        "unit_label": "B1-TO2-APTO-104",
        "app_origin": "http://localhost:4321",
        "token": TOKEN,
        "expires_at": EXPIRES,
        **overrides,
    }
    return build_invitation_email(**values)


# --- Token ----------------------------------------------------------------------------------


def test_token_has_256_random_bits() -> None:
    tokens = {generate_session_token() for _ in range(1000)}
    assert len(tokens) == 1000
    for token in list(tokens)[:50]:
        # token_urlsafe(32): 32 random bytes → 43 URL-safe base64 characters.
        assert len(token) == 43
        assert set(token) <= set(string.ascii_letters + string.digits + "-_")


def test_only_the_sha256_is_stored() -> None:
    assert hash_token(TOKEN) == hashlib.sha256(TOKEN.encode()).hexdigest()
    assert len(hash_token(TOKEN)) == 64 and TOKEN not in hash_token(TOKEN)


# --- Email ----------------------------------------------------------------------------------


def test_subject_and_recipient() -> None:
    email = compose()
    assert email.subject == SUBJECT == "Invitación para activar tu cuenta en SoftPh-Académico"
    assert email.to == "residente@example.com"


def test_body_contains_name_unit_and_link_in_the_fragment() -> None:
    body = compose().body
    assert body.startswith("Hola, Laura Gómez:")
    assert "apartamento B1-TO2-APTO-104" in body
    assert f"http://localhost:4321/activate-account#token={TOKEN}" in body
    assert "?token=" not in body
    assert re.findall(r"https?://\S+", body) == [f"http://localhost:4321/activate-account#token={TOKEN}"]


def test_link_never_uses_the_query_string() -> None:
    link = activation_link("http://localhost:4321/", TOKEN)
    assert link == f"http://localhost:4321/activate-account#token={TOKEN}"
    assert "?" not in link


def test_expiry_in_bogota_time() -> None:
    assert "8 de octubre de 2026, 3:05 p. m. (hora de Colombia)" in compose().body


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        (datetime(2026, 10, 9, 4, 30), "8 de octubre de 2026, 11:30 p. m. (hora de Colombia)"),
        (datetime(2026, 1, 1, 5, 0), "1 de enero de 2026, 12:00 a. m. (hora de Colombia)"),
        (datetime(2026, 7, 15, 17, 0, tzinfo=UTC), "15 de julio de 2026, 12:00 p. m. (hora de Colombia)"),
    ],
)
def test_format_bogota(utc: datetime, expected: str) -> None:
    assert format_bogota(utc) == expected


def test_no_password_or_secrets_in_the_body() -> None:
    body = compose().body.lower()
    assert "contraseña:" not in body
    assert "password" not in body
    assert hash_token(TOKEN) not in body
    assert "si no esperabas esta invitación, puedes ignorar este correo." in body


# --- SMTP sender ------------------------------------------------------------------------------


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.messages: list[EmailMessage] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def send_message(self, message: EmailMessage) -> None:
        self.messages.append(message)


async def test_smtp_sender_uses_settings_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances = []
    monkeypatch.setattr(mail_module.smtplib, "SMTP", FakeSMTP)
    settings = Settings(
        db_name="x", db_user="x", db_password="x", test_db_name="x_test",
        smtp_host="127.0.0.1", smtp_port=1025, smtp_from="SoftPh <no-reply@softph.local>", smtp_timeout_seconds=3,
    )

    await SmtpMailSender(settings).send(compose())

    [smtp] = FakeSMTP.instances
    assert (smtp.host, smtp.port, smtp.timeout) == ("127.0.0.1", 1025, 3)
    [message] = smtp.messages
    assert message["From"] == "SoftPh <no-reply@softph.local>"
    assert message["To"] == "residente@example.com"
    assert message["Subject"] == SUBJECT
    assert f"#token={TOKEN}" in message.get_content()


def test_smtp_defaults_point_to_mailpit() -> None:
    settings = Settings(db_name="x", db_user="x", db_password="x", test_db_name="x_test", _env_file=None)  # type: ignore[call-arg]
    assert (settings.smtp_host, settings.smtp_port) == ("127.0.0.1", 1025)


# --- Validity ----------------------------------------------------------------------------------


def test_expiry_rule() -> None:
    now = datetime(2026, 10, 1, 12, 0)
    assert not is_expired(now + timedelta(microseconds=1), now)
    assert is_expired(now, now)
    assert is_expired(now - timedelta(days=1), now)
