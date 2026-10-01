"""A-1 regression: email variants cannot bypass the login lockout.

The throttle identifier and the MySQL lookup must agree on which emails are
"the same": canonical form (strip → NFC → lower) + exact comparison
(utf8mb4_0900_bin).
"""

from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditAction, AuditLog, User, UserSession
from tests.integration.conftest import PASSWORD

pytestmark = pytest.mark.anyio

MakeUser = Callable[..., Awaitable[User]]

ACCENT_VARIANTS = ["ádmin@example.com", "àdmin@example.com", "âdmin@example.com", "ädmin@example.com", "ãdmin@example.com"]
NON_EQUIVALENT = ["ádmin@example.com", "admin@exámple.com", "ａｄｍｉｎ@example.com", "admın@example.com", "ad min@example.com"]
EQUIVALENT = ["ADMIN@Example.COM", "  admin@example.com  ", "\tAdmin@example.com "]


async def login(client: AsyncClient, headers: dict[str, str], email: str, password: str = PASSWORD) -> Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=headers)


async def lock(client: AsyncClient, headers: dict[str, str], email: str = "admin@example.com") -> None:
    for _ in range(5):
        assert (await login(client, headers, email, "wrong password")).status_code == 401
    assert (await login(client, headers, email)).status_code == 429


async def test_accent_variant_cannot_log_in_while_account_is_locked(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, db_session: AsyncSession
) -> None:
    """The exact attack from the security review: must now fail."""
    await make_user(email="admin@example.com")
    await lock(client, origin_headers)

    response = await login(client, origin_headers, "ádmin@example.com")  # correct password

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert await db_session.scalar(select(func.count()).select_from(UserSession)) == 0


async def test_accent_variants_do_not_reach_the_account(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, db_session: AsyncSession
) -> None:
    await make_user(email="admin@example.com")

    for variant in ACCENT_VARIANTS:
        assert (await login(client, origin_headers, variant)).status_code == 401

    reasons = [e.context["reason"] for e in await db_session.scalars(select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_FAILED))]
    assert reasons == ["unknown_email"] * len(ACCENT_VARIANTS)


@pytest.mark.parametrize("variant", NON_EQUIVALENT)
async def test_non_equivalent_variants_never_find_the_account(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, variant: str
) -> None:
    await make_user(email="admin@example.com")

    assert (await login(client, origin_headers, variant)).status_code == 401


@pytest.mark.parametrize("variant", EQUIVALENT)
async def test_equivalent_variants_log_into_the_same_account(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, variant: str
) -> None:
    user = await make_user(email="admin@example.com")

    response = await login(client, origin_headers, variant)

    assert response.status_code == 200
    assert response.json()["user"]["id"] == user.id


@pytest.mark.parametrize("variant", EQUIVALENT)
async def test_equivalent_variants_share_the_lock(
    client: AsyncClient, origin_headers: dict[str, str], make_user: MakeUser, variant: str
) -> None:
    await make_user(email="admin@example.com")
    await lock(client, origin_headers)

    assert (await login(client, origin_headers, variant)).status_code == 429


async def test_composed_and_decomposed_forms_share_the_lock(client: AsyncClient, origin_headers: dict[str, str]) -> None:
    composed, decomposed = "josé@example.com", "josé@example.com"
    await lock(client, origin_headers, composed)

    assert (await login(client, origin_headers, decomposed)).status_code == 429


# --- database level ------------------------------------------------------------


async def test_email_column_compares_exactly(db_session: AsyncSession) -> None:
    collation = await db_session.scalar(
        text(
            "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' AND COLUMN_NAME = 'email'"
        )
    )
    assert collation == "utf8mb4_0900_bin"


@pytest.mark.parametrize("variant", ["ádmin@example.com", "ADMIN@example.com", "admin@example.com ", "ａｄｍｉｎ@example.com"])
async def test_mysql_does_not_match_variants(db_session: AsyncSession, make_user: MakeUser, variant: str) -> None:
    await make_user(email="admin@example.com")

    count = await db_session.scalar(select(func.count()).select_from(User).where(User.email == variant))

    assert count == 0


@pytest.mark.parametrize(
    ("email", "error"),
    [
        ("Admin@example.com", "ck_users_email_canonical"),  # not lower-case
        (" admin@example.com", "ck_users_email_canonical"),  # not trimmed
        ("josé@example.com", "ck_users_email_ascii"),  # not ASCII
        ("admin@example.com", "uq_users_email"),  # exact duplicate
    ],
    ids=["upper-case", "leading-space", "non-ascii", "duplicate"],
)
async def test_database_rejects_non_canonical_or_duplicate_emails(
    db_session: AsyncSession, make_user: MakeUser, email: str, error: str
) -> None:
    await make_user(email="admin@example.com")

    # asyncmy reports CHECK violations (3819) as OperationalError, UNIQUE (1062) as IntegrityError.
    with pytest.raises(DBAPIError, match=error):
        await db_session.execute(
            text("INSERT INTO users (email, full_name, role, status) VALUES (:email, 'X Y Z', 'RESIDENTE', 'DISABLED')"),
            {"email": email},
        )
