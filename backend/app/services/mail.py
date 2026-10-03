"""Outgoing email (SMTP). Development sends to Mailpit; real email is never sent from development.

Routes never build emails: services compose an OutgoingEmail and hand it to a
MailSender. Tests replace the sender through the get_mail_sender dependency.
Nothing here logs message bodies (they may contain one-time tokens).
"""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

import anyio

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str  # e.g. "application/pdf"
    data: bytes


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str  # plain text
    attachments: tuple[Attachment, ...] = ()


class MailSender(Protocol):
    async def send(self, email: OutgoingEmail) -> None:
        """Delivers the email or raises. Never logs the body."""


class SmtpMailSender:
    """Standard-library SMTP client, run in a worker thread (smtplib blocks)."""

    def __init__(self, settings: Settings) -> None:
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._sender = settings.smtp_from
        self._timeout = settings.smtp_timeout_seconds

    async def send(self, email: OutgoingEmail) -> None:
        await anyio.to_thread.run_sync(self._send_blocking, email)

    def _send_blocking(self, email: OutgoingEmail) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = email.to
        message["Subject"] = email.subject
        message.set_content(email.body)
        for attachment in email.attachments:
            maintype, subtype = attachment.content_type.split("/", 1)
            message.add_attachment(attachment.data, maintype=maintype, subtype=subtype, filename=attachment.filename)
        # The timeout applies to the connection and to every SMTP command.
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            smtp.send_message(message)


def get_mail_sender() -> MailSender:
    return SmtpMailSender(get_settings())
