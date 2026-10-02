"""HU-003: the real SMTP sender delivers the invitation to Mailpit (development).

Skipped when Mailpit is not running. The test message is deleted afterwards.
"""

import uuid
from datetime import datetime

import httpx
import pytest

from app.core.config import get_settings
from app.core.sessions import generate_session_token
from app.services.invitation_email import build_invitation_email
from app.services.mail import SmtpMailSender

pytestmark = pytest.mark.anyio

MAILPIT_API = "http://127.0.0.1:8025/api/v1"


def mailpit_available() -> bool:
    try:
        return httpx.get(f"{MAILPIT_API}/info", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not mailpit_available(), reason="Mailpit is not running on 127.0.0.1:8025")
async def test_invitation_reaches_mailpit() -> None:
    settings = get_settings()
    assert (settings.smtp_host, settings.smtp_port) == ("127.0.0.1", 1025)  # never a real server
    recipient = f"hu003-{uuid.uuid4().hex[:12]}@example.com"
    token = generate_session_token()
    email = build_invitation_email(
        to=recipient,
        full_name="Laura Gómez",
        unit_label="B1-TO2-APTO-104",
        app_origin=settings.app_origin,
        token=token,
        expires_at=datetime(2026, 10, 8, 20, 5),
    )

    await SmtpMailSender(settings).send(email)

    async with httpx.AsyncClient(base_url=MAILPIT_API, timeout=5) as mailpit:
        found = (await mailpit.get("/search", params={"query": f'to:"{recipient}"'})).json()
        try:
            assert found["messages_count"] == 1
            message_id = found["messages"][0]["ID"]
            message = (await mailpit.get(f"/message/{message_id}")).json()
            assert message["Subject"] == "Invitación para activar tu cuenta en SoftPh-Académico"
            assert message["To"][0]["Address"] == recipient
            assert f"/activate-account#token={token}" in message["Text"]
            assert "?token=" not in message["Text"]
            assert "B1-TO2-APTO-104" in message["Text"]
        finally:
            ids = [m["ID"] for m in found.get("messages", [])]
            if ids:
                await mailpit.request("DELETE", "/messages", json={"IDs": ids})
