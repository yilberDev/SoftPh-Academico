"""HU-016: restablecimiento de contraseña del residente (request → email → verify → confirm)."""

import hashlib
import re
import smtplib
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.passwords import verify_password
from app.core.sessions import SESSION_COOKIE_NAME
from app.models import AuditAction, AuditLog, User, UserRole, UserStatus
from tests.integration.conftest import PASSWORD, FakeMailSender

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]
API = "/api/v1/auth/password-reset"
RESIDENT = "residente@example.com"
NEW_PASSWORD = "una frase nueva y segura 2026"
TOKEN_RE = re.compile(r"/reset-password#token=([A-Za-z0-9_-]+)")


@pytest.fixture
async def resident(make_user: MakeUser) -> User:
    return await make_user(RESIDENT, role=UserRole.RESIDENTE)


async def request(client: AsyncClient, headers: dict[str, str], email: str = RESIDENT, **extra: Any) -> Response:
    return await client.post(f"{API}/request", json={"email": email, **extra}, headers=headers)


async def verify(client: AsyncClient, headers: dict[str, str], token: Any) -> Response:
    return await client.post(f"{API}/verify", json={"token": token}, headers=headers)


async def confirm(client: AsyncClient, headers: dict[str, str], token: str, **overrides: Any) -> Response:
    body = {"token": token, "password": NEW_PASSWORD, "password_confirmation": NEW_PASSWORD, **overrides}
    return await client.post(f"{API}/confirm", json=body, headers=headers)


async def login(client: AsyncClient, headers: dict[str, str], password: str) -> Response:
    return await client.post("/api/v1/auth/login", json={"email": RESIDENT, "password": password}, headers=headers)


def token_of(mailer: FakeMailSender, index: int = -1) -> str:
    match = TOKEN_RE.search(mailer.sent[index].body)
    assert match is not None
    return match.group(1)


async def token_for(client: AsyncClient, headers: dict[str, str], mailer: FakeMailSender) -> str:
    assert (await request(client, headers)).status_code == 202
    return token_of(mailer)


async def rows(db: AsyncSession) -> list[dict[str, Any]]:
    return [dict(r) for r in (await db.execute(text("SELECT * FROM password_reset_tokens ORDER BY id"))).mappings()]


async def actions(db: AsyncSession, *names: AuditAction) -> list[AuditLog]:
    return list((await db.scalars(select(AuditLog).where(AuditLog.action.in_(names)).order_by(AuditLog.id))).all())


def error(response: Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code
    return body


# --- Criterion 1-3: request (identify the account, without revealing it) ------------------------


async def test_request_for_a_resident(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    response = await request(client, origin_headers)

    assert response.status_code == 202
    assert response.json() == {"status": "ACCEPTED"}
    assert response.headers["cache-control"] == "no-store"
    [email] = mailer.sent
    token = token_of(mailer)
    assert email.to == RESIDENT
    assert email.subject == "Restablece tu contraseña de SoftPh-Académico"
    assert f"{get_settings().app_origin}/reset-password#token={token}" in email.body
    assert "?token=" not in email.body and "(hora de Colombia)" in email.body
    [row] = await rows(db_session)
    assert row["user_id"] == resident.id
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest() and token not in str(row)
    assert row["expires_at"] - row["created_at"] == timedelta(hours=1)
    assert (row["used_at"], row["revoked_at"]) == (None, None)
    [requested] = await actions(db_session, AuditAction.PASSWORD_RESET_REQUESTED)
    assert (requested.entity_type, requested.entity_id, requested.context) == ("user", resident.id, {"reset_id": row["id"]})


CASES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("resident", RESIDENT, {"role": UserRole.RESIDENTE}),
    ("unknown-email", "nadie@example.com", None),
    ("administrator", RESIDENT, {"role": UserRole.ADMINISTRADOR}),
    ("pending-activation", RESIDENT, {"role": UserRole.RESIDENTE, "status": UserStatus.PENDING_ACTIVATION, "password": None}),
    ("disabled", RESIDENT, {"role": UserRole.RESIDENTE, "status": UserStatus.DISABLED}),
    ("deleted", RESIDENT, {"role": UserRole.RESIDENTE, "deleted": True}),
]


@pytest.mark.parametrize(("email", "user"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
async def test_request_answers_the_same_for_everyone(
    client: AsyncClient,
    origin_headers: dict[str, str],
    make_user: MakeUser,
    mailer: FakeMailSender,
    db_session: AsyncSession,
    email: str,
    user: dict[str, Any] | None,
) -> None:
    if user is not None:
        await make_user(RESIDENT, **user)

    response = await request(client, origin_headers, email=email)

    assert response.status_code == 202
    assert response.json() == {"status": "ACCEPTED"}
    assert sorted(response.headers.keys()) == ["cache-control", "content-length", "content-type"]
    eligible = user is not None and user.get("role") == UserRole.RESIDENTE and len(user) == 1
    assert len(await rows(db_session)) == (1 if eligible else 0)
    assert len(mailer.sent) == (1 if eligible else 0)


async def test_not_eligible_accounts_are_audited_only_internally(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, db_session: AsyncSession
) -> None:
    admin = await make_user("admin@example.com")
    await request(client, origin_headers, email="admin@example.com")
    await request(client, origin_headers, email="nadie@example.com")  # unknown: nothing written

    [rejected] = await actions(db_session, AuditAction.PASSWORD_RESET_REJECTED)
    assert (rejected.entity_id, rejected.context) == (admin.id, {"reason": "not_eligible"})


async def test_email_is_normalized(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender
) -> None:
    assert (await request(client, origin_headers, email="  Residente@Example.COM ")).status_code == 202
    assert [e.to for e in mailer.sent] == [RESIDENT]


@pytest.mark.parametrize(
    ("body", "field"),
    [({}, "email"), ({"email": ""}, "email"), ({"email": "x" * 300}, "email"), ({"email": RESIDENT, "user_id": 1}, "user_id"),
     ({"email": RESIDENT, "role": "RESIDENTE"}, "role"), ({"email": RESIDENT, "token": "x"}, "token"),
     ({"email": RESIDENT, "document_number": "CC123456"}, "document_number"), ({"email": RESIDENT, "unit_id": 1}, "unit_id")],
)
async def test_request_validation(client: AsyncClient, origin_headers: dict[str, str], body: dict[str, Any], field: str) -> None:
    response = await client.post(f"{API}/request", json=body, headers=origin_headers)
    assert error(response, 422, "VALIDATION_ERROR")["details"][0]["field"] == field


async def test_new_request_revokes_the_previous_link(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    first = await token_for(client, origin_headers, mailer)
    second = await token_for(client, origin_headers, mailer)

    old, new = await rows(db_session)
    assert old["revoked_at"] is not None and new["revoked_at"] is None
    assert old["pending_user_marker"] is None and new["pending_user_marker"] == resident.id
    error(await verify(client, origin_headers, first), 410, "PASSWORD_RESET_REVOKED")
    error(await confirm(client, origin_headers, first), 410, "PASSWORD_RESET_REVOKED")
    assert (await verify(client, origin_headers, second)).status_code == 200


async def test_rate_limit_is_silent(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    responses = [await request(client, origin_headers) for _ in range(5)]

    assert {r.status_code for r in responses} == {202}
    assert len({r.text for r in responses}) == 1  # the 4th and 5th look exactly the same
    assert len(mailer.sent) == 3 and len(await rows(db_session)) == 3
    assert [a.context for a in await actions(db_session, AuditAction.PASSWORD_RESET_REJECTED)] == [
        {"reason": "rate_limited"}, {"reason": "rate_limited"}
    ]
    # The window is one hour: older requests no longer count.
    await db_session.execute(text("UPDATE password_reset_tokens SET created_at = created_at - INTERVAL 61 MINUTE, "
                                  "expires_at = expires_at - INTERVAL 61 MINUTE"))
    assert (await request(client, origin_headers)).status_code == 202
    assert len(mailer.sent) == 4


async def test_email_failure_is_audited_and_the_answer_is_the_same(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    mailer.fail_with = smtplib.SMTPServerDisconnected("connection lost")

    response = await request(client, origin_headers)

    assert (response.status_code, response.json()) == (202, {"status": "ACCEPTED"})
    [row] = await rows(db_session)
    [failed] = await actions(db_session, AuditAction.PASSWORD_RESET_EMAIL_FAILED)
    assert (failed.entity_type, failed.entity_id) == ("password_reset", row["id"])
    assert failed.context == {"reset_id": row["id"], "error": "SMTPServerDisconnected"}


# --- verify (criterion 2: a valid account can continue) ------------------------------------------


async def test_verify_valid_link(client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender) -> None:
    token = await token_for(client, origin_headers, mailer)

    response = await verify(client, origin_headers, token)

    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert set(response.json()) == {"expires_at"} and response.json()["expires_at"].endswith("Z")


@pytest.mark.parametrize("token", ["", "abc", "x" * 43, "!" * 43, "a" * 44], ids=["empty", "short", "unknown", "bad-chars", "long"])
async def test_verify_invalid_link(client: AsyncClient, origin_headers: dict[str, str], token: str) -> None:
    error(await verify(client, origin_headers, token), 404, "PASSWORD_RESET_INVALID")


async def test_verify_tampered_expired_used(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    token = await token_for(client, origin_headers, mailer)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    error(await verify(client, origin_headers, tampered), 404, "PASSWORD_RESET_INVALID")

    await db_session.execute(text("UPDATE password_reset_tokens SET expires_at = UTC_TIMESTAMP(6) - INTERVAL 1 SECOND, "
                                  "created_at = UTC_TIMESTAMP(6) - INTERVAL 2 HOUR"))
    error(await verify(client, origin_headers, token), 410, "PASSWORD_RESET_EXPIRED")

    used = await token_for(client, origin_headers, mailer)
    assert (await confirm(client, origin_headers, used)).status_code == 200
    error(await verify(client, origin_headers, used), 410, "PASSWORD_RESET_USED")


async def test_verify_when_the_account_is_no_longer_eligible(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    token = await token_for(client, origin_headers, mailer)
    await db_session.execute(text("UPDATE users SET status = 'DISABLED' WHERE id = :id"), {"id": resident.id})
    error(await verify(client, origin_headers, token), 404, "PASSWORD_RESET_INVALID")


# --- confirm (criteria 4 and 5) ---------------------------------------------------------------


async def test_reset_changes_the_password_and_closes_every_session(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    await login(client, origin_headers, PASSWORD)
    first_cookie = client.cookies[SESSION_COOKIE_NAME]
    client.cookies.clear()
    await login(client, origin_headers, PASSWORD)  # a second open session
    client.cookies.clear()
    token = await token_for(client, origin_headers, mailer)

    response = await confirm(client, origin_headers, token)

    assert response.status_code == 200
    assert response.json() == {"status": "PASSWORD_UPDATED"}
    assert "set-cookie" not in response.headers  # no new session
    stored = await db_session.scalar(text("SELECT password_hash FROM users WHERE id = :id"), {"id": resident.id})
    assert stored.startswith("$argon2id$") and verify_password(stored, NEW_PASSWORD) and not verify_password(stored, PASSWORD)
    [row] = await rows(db_session)
    assert row["used_at"] is not None and row["revoked_at"] is None and row["pending_user_marker"] is None
    open_sessions = await db_session.scalar(text("SELECT COUNT(*) FROM sessions WHERE user_id = :id AND revoked_at IS NULL"), {"id": resident.id})
    assert open_sessions == 0
    [completed] = await actions(db_session, AuditAction.PASSWORD_RESET_COMPLETED)
    assert (completed.actor_user_id, completed.entity_id) == (resident.id, resident.id)
    assert completed.context == {"reset_id": row["id"], "sessions_revoked": 2}
    client.cookies.set(SESSION_COOKIE_NAME, first_cookie)
    assert (await client.get("/api/v1/auth/session")).status_code == 401


async def test_old_password_stops_working_and_new_one_works(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender
) -> None:
    token = await token_for(client, origin_headers, mailer)
    await confirm(client, origin_headers, token)

    error(await login(client, origin_headers, PASSWORD), 401, "INVALID_CREDENTIALS")
    response = await login(client, origin_headers, NEW_PASSWORD)
    assert response.status_code == 200 and response.json()["user"]["role"] == "RESIDENTE"


async def test_a_link_works_only_once(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    resident_id = resident.id  # the service rolls back on rejection (shared test session)
    token = await token_for(client, origin_headers, mailer)
    await confirm(client, origin_headers, token)
    stored = await db_session.scalar(text("SELECT password_hash FROM users WHERE id = :id"), {"id": resident_id})

    error(await confirm(client, origin_headers, token, password="otra frase distinta 99", password_confirmation="otra frase distinta 99"),
          410, "PASSWORD_RESET_USED")

    assert await db_session.scalar(text("SELECT password_hash FROM users WHERE id = :id"), {"id": resident_id}) == stored
    assert [a.context for a in await actions(db_session, AuditAction.PASSWORD_RESET_REJECTED)] == [{"reason": "used"}]


@pytest.mark.parametrize(
    ("overrides", "field", "message"),
    [
        ({"password": "a" * 11, "password_confirmation": "a" * 11}, "password", "La contraseña debe tener entre 12 y 128 caracteres."),
        ({"password": "a" * 129, "password_confirmation": "a" * 129}, "password", "La contraseña debe tener entre 12 y 128 caracteres."),
        ({"password": " " * 12, "password_confirmation": " " * 12}, "password", "La contraseña no puede estar formada solo por espacios."),
        ({"password": f" {RESIDENT.upper()} ", "password_confirmation": f" {RESIDENT.upper()} "}, "password", "La contraseña no puede ser igual al correo electrónico."),
        ({"password_confirmation": "otra frase distinta"}, "password_confirmation", "La confirmación no coincide con la contraseña."),
        ({"user_id": 1}, "user_id", "Este campo no está permitido."),
        ({"email": RESIDENT}, "email", "Este campo no está permitido."),
        ({"role": "ADMINISTRADOR"}, "role", "Este campo no está permitido."),
        ({"resident_unit_id": 1}, "resident_unit_id", "Este campo no está permitido."),
    ],
)
async def test_invalid_new_password_changes_nothing(
    client: AsyncClient,
    origin_headers: dict[str, str],
    resident: User,
    mailer: FakeMailSender,
    db_session: AsyncSession,
    overrides: dict[str, Any],
    field: str,
    message: str,
) -> None:
    token = await token_for(client, origin_headers, mailer)

    response = await confirm(client, origin_headers, token, **overrides)

    assert error(response, 422, "VALIDATION_ERROR")["details"] == [{"field": field, "message": message}]
    assert token not in response.text
    [row] = await rows(db_session)
    assert row["used_at"] is None  # the link is still usable
    assert (await login(client, origin_headers, PASSWORD)).status_code == 200


async def test_rejected_confirmations(
    client: AsyncClient, origin_headers: dict[str, str], resident: User, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    resident_id = resident.id  # the service rolls back on rejection (shared test session)
    error(await confirm(client, origin_headers, "x" * 43), 404, "PASSWORD_RESET_INVALID")
    token = await token_for(client, origin_headers, mailer)
    await db_session.execute(text("UPDATE password_reset_tokens SET expires_at = UTC_TIMESTAMP(6) - INTERVAL 1 SECOND, "
                                  "created_at = UTC_TIMESTAMP(6) - INTERVAL 2 HOUR"))
    error(await confirm(client, origin_headers, token), 410, "PASSWORD_RESET_EXPIRED")
    token = await token_for(client, origin_headers, mailer)
    await db_session.execute(text("UPDATE users SET status = 'DISABLED' WHERE id = :id"), {"id": resident_id})
    error(await confirm(client, origin_headers, token), 404, "PASSWORD_RESET_INVALID")

    reasons = [a.context["reason"] for a in await actions(db_session, AuditAction.PASSWORD_RESET_REJECTED)]
    assert reasons == ["invalid", "expired", "user_not_eligible"]
    assert verify_password(
        await db_session.scalar(text("SELECT password_hash FROM users WHERE id = :id"), {"id": resident_id}), PASSWORD
    )


async def test_administrators_cannot_reset(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, mailer: FakeMailSender, db_session: AsyncSession
) -> None:
    await make_user("admin@example.com")
    assert (await request(client, origin_headers, email="admin@example.com")).status_code == 202
    assert mailer.sent == [] and await rows(db_session) == []
