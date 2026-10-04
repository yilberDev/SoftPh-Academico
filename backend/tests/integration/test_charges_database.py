"""Obligations extension (f3c8fc28babe): the real structure of charges and the
rules MySQL enforces itself, verified with direct SQL (billing period, one
monthly fee per apartment and period, description by concept, origin of a late
fee, author, RESTRICT)."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

CHECK_VIOLATED = 3819
FK_PARENT_MISSING = 1452
FK_ROW_REFERENCED = 1451
DUPLICATE = 1062
NOT_NULL = 1048
NO_DEFAULT = 1364
GENERATED_COLUMN_WRITE = 3105

INSERT = (
    "INSERT INTO charges (unit_id, concept, description, amount, due_date, billing_period, source_charge_id, "
    "created_by_user_id) VALUES (:unit, :concept, :description, :amount, :due, :period, :source, :author)"
)


async def rows(db: AsyncSession, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in (await db.execute(text(sql), params)).all()]


async def rejects(db: AsyncSession, sql: str, code: int, **params: Any) -> None:
    async with db.begin_nested():
        with pytest.raises((IntegrityError, DBAPIError)) as error:
            await db.execute(text(sql), params)
    assert error.value.orig.args[0] == code, error.value.orig  # type: ignore[union-attr]


def values(ids: dict[str, int], **changes: Any) -> dict[str, Any]:
    """A valid MONTHLY_FEE of the first apartment, with the given changes."""
    return {"unit": ids["unit"], "concept": "MONTHLY_FEE", "description": None, "amount": 100000,
            "due": "2026-10-05", "period": "2026-10-01", "source": None, "author": ids["admin"]} | changes


async def insert(db: AsyncSession, ids: dict[str, int], **changes: Any) -> int:
    await db.execute(text(INSERT), values(ids, **changes))
    return int((await rows(db, "SELECT LAST_INSERT_ID()"))[0][0])


async def rejects_insert(db: AsyncSession, ids: dict[str, int], code: int, **changes: Any) -> None:
    await rejects(db, INSERT, code, **values(ids, **changes))


def late_fee(**changes: Any) -> dict[str, Any]:
    return {"concept": "LATE_FEE", "period": None, "amount": 20000, "due": "2026-11-05"} | changes


@pytest.fixture
async def ids(db_session: AsyncSession, make_user: Any) -> dict[str, int]:
    admin = await make_user("admin@example.com", password_hash="not-a-real-hash")
    community = Community(db_session, admin.id)
    unit = await community.unit(1, 1, 101)
    other_unit = await community.unit(1, 1, 102)
    return {"admin": admin.id, "unit": unit, "other_unit": other_unit}


# --- Structure -----------------------------------------------------------------------------------------------------


async def test_charge_columns(db_session: AsyncSession) -> None:
    assert await rows(
        db_session,
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, EXTRA FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'charges' ORDER BY ORDINAL_POSITION",
    ) == [
        ("id", "bigint", "NO", "auto_increment"),
        ("unit_id", "bigint", "NO", ""),
        ("concept", "varchar(20)", "NO", ""),
        ("description", "varchar(200)", "YES", ""),
        ("amount", "decimal(12,2)", "NO", ""),
        ("due_date", "date", "NO", ""),
        ("billing_period", "date", "YES", ""),
        ("source_charge_id", "bigint", "YES", ""),
        ("created_by_user_id", "bigint", "NO", ""),
        ("created_at", "datetime(6)", "NO", "DEFAULT_GENERATED"),
        ("can_originate_late_fee", "tinyint", "NO", "STORED GENERATED"),
        ("late_fee_source_link", "tinyint", "YES", "STORED GENERATED"),
    ]
    # No stored status, balance, applied amount, soft delete or version (all derived or out of scope).
    assert await rows(
        db_session,
        "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'charges' "
        "AND COLUMN_NAME IN ('status', 'deleted_at', 'version', 'updated_at', 'balance', 'applied_amount', 'account_status')",
    ) == [(0,)]


async def test_origin_foreign_key_columns(db_session: AsyncSession) -> None:
    assert await rows(
        db_session,
        "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'charges' "
        "AND CONSTRAINT_NAME = 'fk_charges_source_charge_id_charges' ORDER BY ORDINAL_POSITION",
    ) == [
        ("unit_id", "charges", "unit_id"),
        ("source_charge_id", "charges", "id"),
        ("late_fee_source_link", "charges", "can_originate_late_fee"),
    ]


async def test_generated_markers(db_session: AsyncSession, ids: dict[str, int]) -> None:
    fee = await insert(db_session, ids)
    linked = await insert(db_session, ids, **late_fee(source=fee))
    historical = await insert(db_session, ids, **late_fee())
    assert await rows(
        db_session, "SELECT id, can_originate_late_fee, late_fee_source_link FROM charges ORDER BY id"
    ) == [(fee, 1, None), (linked, 0, 1), (historical, 0, None)]
    # The application never writes them.
    await rejects(db_session, "UPDATE charges SET can_originate_late_fee = 1 WHERE id = :c", GENERATED_COLUMN_WRITE, c=linked)


# --- 1-5. Billing period -------------------------------------------------------------------------------------------


async def test_monthly_fee_needs_a_billing_period(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await rejects_insert(db_session, ids, CHECK_VIOLATED, period=None)


async def test_billing_period_is_the_first_day_of_the_month(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await rejects_insert(db_session, ids, CHECK_VIOLATED, period="2026-10-02")
    await rejects_insert(db_session, ids, CHECK_VIOLATED, period="2026-10-31")


@pytest.mark.parametrize("concept", ["LATE_FEE", "PENALTY", "OTHER"])
async def test_only_monthly_fees_have_a_billing_period(db_session: AsyncSession, ids: dict[str, int], concept: str) -> None:
    await rejects_insert(db_session, ids, CHECK_VIOLATED, concept=concept, description="Detalle", period="2026-10-01")


async def test_one_monthly_fee_per_apartment_and_period(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await insert(db_session, ids)
    await rejects_insert(db_session, ids, DUPLICATE, amount=50000, due="2026-10-20")
    # The same period in another apartment is a different monthly fee.
    await insert(db_session, ids, unit=ids["other_unit"])


async def test_monthly_fees_of_different_periods(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await insert(db_session, ids)
    await insert(db_session, ids, period="2026-11-01", due="2026-11-05")
    await insert(db_session, ids, period="2025-10-01", due="2025-10-05")
    # Other concepts have no period, so they never collide.
    for _ in range(2):
        await insert(db_session, ids, concept="PENALTY", description="Ruido", period=None)
        await insert(db_session, ids, **late_fee())
    assert await rows(db_session, "SELECT concept, COUNT(*) FROM charges GROUP BY concept ORDER BY concept") == [
        ("LATE_FEE", 2), ("MONTHLY_FEE", 3), ("PENALTY", 2)
    ]


# --- 6-8. Description by concept -----------------------------------------------------------------------------------


@pytest.mark.parametrize("concept", ["PENALTY", "OTHER"])
async def test_penalty_and_other_need_a_description(db_session: AsyncSession, ids: dict[str, int], concept: str) -> None:
    await rejects_insert(db_session, ids, CHECK_VIOLATED, concept=concept, period=None, description=None)
    await insert(db_session, ids, concept=concept, period=None, description="Uso indebido de zona común")


async def test_monthly_fee_has_no_description(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await rejects_insert(db_session, ids, CHECK_VIOLATED, description="Mensualidad")


async def test_late_fee_description_is_optional(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await insert(db_session, ids, **late_fee())
    await insert(db_session, ids, **late_fee(description="Mora de septiembre"))


# --- 9-13. Origin of a late fee ------------------------------------------------------------------------------------


async def test_historical_late_fee_without_origin(db_session: AsyncSession, ids: dict[str, int]) -> None:
    first = await insert(db_session, ids, **late_fee())
    second = await insert(db_session, ids, **late_fee())
    assert await rows(db_session, "SELECT COUNT(*) FROM charges WHERE source_charge_id IS NULL AND id IN (:a, :b)",
                      a=first, b=second) == [(2,)]


async def test_late_fee_with_an_origin_of_the_same_apartment(db_session: AsyncSession, ids: dict[str, int]) -> None:
    fee = await insert(db_session, ids)
    penalty = await insert(db_session, ids, concept="PENALTY", description="Ruido", period=None)
    other = await insert(db_session, ids, concept="OTHER", description="Llave", period=None)
    for origin in (fee, penalty, other):
        late = await insert(db_session, ids, **late_fee(source=origin))
        assert await rows(db_session, "SELECT source_charge_id FROM charges WHERE id = :c", c=late) == [(origin,)]


async def test_origin_of_another_apartment_is_rejected(db_session: AsyncSession, ids: dict[str, int]) -> None:
    foreign = await insert(db_session, ids, unit=ids["other_unit"])
    await rejects_insert(db_session, ids, FK_PARENT_MISSING, **late_fee(source=foreign))


async def test_origin_that_is_a_late_fee_is_rejected(db_session: AsyncSession, ids: dict[str, int]) -> None:
    historical = await insert(db_session, ids, **late_fee())
    await rejects_insert(db_session, ids, FK_PARENT_MISSING, **late_fee(source=historical))


async def test_missing_origin_is_rejected(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await rejects_insert(db_session, ids, FK_PARENT_MISSING, **late_fee(source=999999))


@pytest.mark.parametrize("concept", ["MONTHLY_FEE", "PENALTY", "OTHER"])
async def test_only_late_fees_have_an_origin(db_session: AsyncSession, ids: dict[str, int], concept: str) -> None:
    origin = await insert(db_session, ids, concept="PENALTY", description="Ruido", period=None)
    period = "2026-12-01" if concept == "MONTHLY_FEE" else None
    description = None if concept == "MONTHLY_FEE" else "Detalle"
    await rejects_insert(db_session, ids, CHECK_VIOLATED, concept=concept, period=period, description=description,
                         source=origin)


async def test_one_late_fee_per_origin(db_session: AsyncSession, ids: dict[str, int]) -> None:
    fee = await insert(db_session, ids)
    await insert(db_session, ids, **late_fee(source=fee))
    await rejects_insert(db_session, ids, DUPLICATE, **late_fee(source=fee, amount=5000))


async def test_a_referenced_origin_cannot_change_apartment_or_become_a_late_fee(
    db_session: AsyncSession, ids: dict[str, int]
) -> None:
    penalty = await insert(db_session, ids, concept="PENALTY", description="Ruido", period=None)
    await insert(db_session, ids, **late_fee(source=penalty))
    await rejects(db_session, "UPDATE charges SET unit_id = :u WHERE id = :c", FK_ROW_REFERENCED,
                  u=ids["other_unit"], c=penalty)
    await rejects(db_session, "UPDATE charges SET concept = 'LATE_FEE' WHERE id = :c", FK_ROW_REFERENCED, c=penalty)


# --- 14-16. Author and RESTRICT ------------------------------------------------------------------------------------


async def test_author_is_required(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await rejects_insert(db_session, ids, NOT_NULL, author=None)
    await rejects(db_session, "INSERT INTO charges (unit_id, concept, amount, due_date, billing_period) "
                              "VALUES (:u, 'MONTHLY_FEE', 1, '2026-10-05', '2026-10-01')", NO_DEFAULT, u=ids["unit"])


async def test_author_must_exist(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await rejects_insert(db_session, ids, FK_PARENT_MISSING, author=999999)


async def test_foreign_keys_restrict_deletion(db_session: AsyncSession, ids: dict[str, int]) -> None:
    assert await rows(
        db_session,
        "SELECT CONSTRAINT_NAME, REFERENCED_TABLE_NAME, DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'charges' ORDER BY 1",
    ) == [
        ("fk_charges_created_by_user_id_users", "users", "RESTRICT"),
        ("fk_charges_source_charge_id_charges", "charges", "RESTRICT"),
        ("fk_charges_unit_id_units", "units", "RESTRICT"),
    ]
    fee = await insert(db_session, ids)
    await insert(db_session, ids, **late_fee(source=fee))
    # The origin of a late fee, the author and the apartment are never deleted while referenced.
    await rejects(db_session, "DELETE FROM charges WHERE id = :c", FK_ROW_REFERENCED, c=fee)
    await rejects(db_session, "DELETE FROM users WHERE id = :a", FK_ROW_REFERENCED, a=ids["admin"])
    await rejects(db_session, "DELETE FROM units WHERE id = :u", FK_ROW_REFERENCED, u=ids["unit"])
