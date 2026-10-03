"""HU-004: the real structure of charges, payments and payment_allocations, and the
rules MySQL enforces itself (DECIMAL money, CHECKs, foreign keys, indexes)."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

TABLES = ("charges", "payments", "payment_allocations")


async def rows(db: AsyncSession, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in (await db.execute(text(sql), params)).all()]


async def test_money_is_decimal_never_float(db_session: AsyncSession) -> None:
    assert await rows(
        db_session,
        "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN ('charges', 'payments', 'payment_allocations') AND COLUMN_NAME = 'amount' ORDER BY 1",
    ) == [("charges", "amount", "decimal(12,2)"), ("payment_allocations", "amount", "decimal(12,2)"),
          ("payments", "amount", "decimal(12,2)")]
    assert await rows(
        db_session,
        "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN ('charges', 'payments', 'payment_allocations') AND DATA_TYPE IN ('float', 'double')",
    ) == [(0,)]


async def test_payment_columns(db_session: AsyncSession) -> None:
    assert await rows(
        db_session,
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payments' ORDER BY ORDINAL_POSITION",
    ) == [
        ("id", "bigint", "NO", None),
        ("resident_id", "bigint", "NO", None),
        ("unit_id", "bigint", "NO", None),
        ("registered_by_user_id", "bigint", "NO", None),
        ("amount", "decimal(12,2)", "NO", None),
        ("method", "varchar(20)", "NO", None),
        ("concept", "varchar(20)", "NO", None),
        ("concept_description", "varchar(200)", "YES", None),
        ("source", "varchar(20)", "NO", "MANUAL"),
        ("payment_date", "date", "NO", None),
        ("registered_at", "datetime(6)", "NO", None),
        ("resident_name_snapshot", "varchar(150)", "NO", None),
        ("unit_label_snapshot", "varchar(40)", "NO", None),
        ("receipt_email_status", "varchar(20)", "NO", "PENDING"),
        ("receipt_sent_at", "datetime(6)", "YES", None),
        ("created_at", "datetime(6)", "NO", "CURRENT_TIMESTAMP(6)"),
    ]


async def test_constraints_indexes_and_foreign_keys(db_session: AsyncSession) -> None:
    assert await rows(
        db_session,
        "SELECT tc.TABLE_NAME, cc.CONSTRAINT_NAME FROM information_schema.CHECK_CONSTRAINTS cc "
        "JOIN information_schema.TABLE_CONSTRAINTS tc ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA "
        "AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME WHERE cc.CONSTRAINT_SCHEMA = DATABASE() "
        "AND tc.TABLE_NAME IN ('charges', 'payments', 'payment_allocations') AND tc.ENFORCED = 'YES' ORDER BY 1, 2",
    ) == [
        ("charges", "ck_charges_amount_positive"), ("charges", "ck_charges_concept_valid"),
        ("charges", "ck_charges_other_has_description"),
        ("payment_allocations", "ck_payment_allocations_amount_positive"),
        ("payments", "ck_payments_amount_positive"), ("payments", "ck_payments_concept_valid"),
        ("payments", "ck_payments_description_only_for_other"), ("payments", "ck_payments_method_valid"),
        ("payments", "ck_payments_receipt_email_status_valid"), ("payments", "ck_payments_sent_has_timestamp"),
        ("payments", "ck_payments_source_valid"),
    ]
    assert await rows(
        db_session,
        "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN ('charges', 'payments', 'payment_allocations') GROUP BY 1, 2, 3 ORDER BY 1, 2",
    ) == [
        ("charges", "ix_charges_unit_id_due_date_created_at", 1, "unit_id,due_date,created_at"),
        ("charges", "PRIMARY", 0, "id"),
        ("payment_allocations", "ix_payment_allocations_charge_id", 1, "charge_id"),
        ("payment_allocations", "PRIMARY", 0, "id"),
        ("payment_allocations", "uq_payment_allocations_payment_id", 0, "payment_id,charge_id"),
        ("payments", "ix_payments_payment_date", 1, "payment_date"),
        ("payments", "ix_payments_registered_by_user_id", 1, "registered_by_user_id"),
        ("payments", "ix_payments_resident_id_payment_date", 1, "resident_id,payment_date"),
        ("payments", "ix_payments_unit_id", 1, "unit_id"),
        ("payments", "PRIMARY", 0, "id"),
    ]
    assert await rows(
        db_session,
        "SELECT rc.TABLE_NAME, rc.CONSTRAINT_NAME, rc.REFERENCED_TABLE_NAME, rc.DELETE_RULE "
        "FROM information_schema.REFERENTIAL_CONSTRAINTS rc WHERE rc.CONSTRAINT_SCHEMA = DATABASE() "
        "AND rc.TABLE_NAME IN ('charges', 'payments', 'payment_allocations') ORDER BY 1, 2",
    ) == [
        ("charges", "fk_charges_unit_id_units", "units", "RESTRICT"),
        ("payment_allocations", "fk_payment_allocations_charge_id_charges", "charges", "RESTRICT"),
        ("payment_allocations", "fk_payment_allocations_payment_id_payments", "payments", "RESTRICT"),
        ("payments", "fk_payments_registered_by_user_id_users", "users", "RESTRICT"),
        ("payments", "fk_payments_resident_id_users", "users", "RESTRICT"),
        ("payments", "fk_payments_unit_id_units", "units", "RESTRICT"),
    ]


@pytest.fixture
async def ids(db_session: AsyncSession, make_user: Any) -> dict[str, int]:
    admin = await make_user("admin@example.com", password_hash="not-a-real-hash")
    community = Community(db_session, admin.id)
    resident = await community.resident("Laura Gómez", "1012345678", (1, 1, 101))
    unit_id = (await rows(db_session, "SELECT unit_id FROM resident_units WHERE user_id = :u", u=resident))[0][0]
    await db_session.execute(text(
        "INSERT INTO charges (unit_id, concept, amount, due_date) VALUES (:u, 'MONTHLY_FEE', 100000, '2026-01-05')"), {"u": unit_id})
    charge_id = (await rows(db_session, "SELECT MAX(id) FROM charges"))[0][0]
    await db_session.execute(text(
        "INSERT INTO payments (resident_id, unit_id, registered_by_user_id, amount, method, concept, payment_date, "
        "registered_at, resident_name_snapshot, unit_label_snapshot) VALUES (:r, :u, :a, 50000, 'CASH', 'MONTHLY_FEE', "
        "'2026-01-10', UTC_TIMESTAMP(6), 'Laura Gómez', 'B1-TO1-APTO-101')"), {"r": resident, "u": unit_id, "a": admin.id})
    payment_id = (await rows(db_session, "SELECT MAX(id) FROM payments"))[0][0]
    return {"admin": admin.id, "resident": resident, "unit": unit_id, "charge": charge_id, "payment": payment_id}


async def rejects(db: AsyncSession, sql: str, code: int, **params: Any) -> None:
    async with db.begin_nested():
        with pytest.raises((IntegrityError, DBAPIError)) as error:
            await db.execute(text(sql), params)
    assert error.value.orig.args[0] == code, error.value.orig  # type: ignore[union-attr]


CHECK_VIOLATED = 3819
FK_PARENT_MISSING = 1452
FK_ROW_REFERENCED = 1451
DUPLICATE = 1062


async def test_check_constraints(db_session: AsyncSession, ids: dict[str, int]) -> None:
    u, p = ids["unit"], ids["payment"]
    await rejects(db_session, "INSERT INTO charges (unit_id, concept, amount, due_date) VALUES (:u, 'MONTHLY_FEE', 0, '2026-01-01')", CHECK_VIOLATED, u=u)
    await rejects(db_session, "INSERT INTO charges (unit_id, concept, amount, due_date) VALUES (:u, 'RENT', 1, '2026-01-01')", CHECK_VIOLATED, u=u)
    await rejects(db_session, "INSERT INTO charges (unit_id, concept, amount, due_date) VALUES (:u, 'OTHER', 1, '2026-01-01')", CHECK_VIOLATED, u=u)
    await rejects(db_session, "UPDATE payments SET amount = -1 WHERE id = :p", CHECK_VIOLATED, p=p)
    await rejects(db_session, "UPDATE payments SET method = 'CHECK' WHERE id = :p", CHECK_VIOLATED, p=p)
    await rejects(db_session, "UPDATE payments SET concept = 'OTHER' WHERE id = :p", CHECK_VIOLATED, p=p)  # without description
    await rejects(db_session, "UPDATE payments SET concept_description = 'x' WHERE id = :p", CHECK_VIOLATED, p=p)  # not OTHER
    await rejects(db_session, "UPDATE payments SET source = 'GATEWAY' WHERE id = :p", CHECK_VIOLATED, p=p)
    await rejects(db_session, "UPDATE payments SET receipt_email_status = 'SENT' WHERE id = :p", CHECK_VIOLATED, p=p)
    await rejects(db_session, "INSERT INTO payment_allocations (payment_id, charge_id, amount) VALUES (:p, :c, 0)", CHECK_VIOLATED, p=p, c=ids["charge"])


async def test_foreign_keys_and_uniqueness(db_session: AsyncSession, ids: dict[str, int]) -> None:
    p, c = ids["payment"], ids["charge"]
    await rejects(db_session, "INSERT INTO charges (unit_id, concept, amount, due_date) VALUES (999999, 'MONTHLY_FEE', 1, '2026-01-01')", FK_PARENT_MISSING)
    await rejects(db_session, "INSERT INTO payment_allocations (payment_id, charge_id, amount) VALUES (:p, 999999, 1)", FK_PARENT_MISSING, p=p)
    await db_session.execute(text("INSERT INTO payment_allocations (payment_id, charge_id, amount) VALUES (:p, :c, 1)"), {"p": p, "c": c})
    await rejects(db_session, "INSERT INTO payment_allocations (payment_id, charge_id, amount) VALUES (:p, :c, 1)", DUPLICATE, p=p, c=c)
    # Financial history is never deleted by cascade: RESTRICT everywhere.
    await rejects(db_session, "DELETE FROM charges WHERE id = :c", FK_ROW_REFERENCED, c=c)
    await rejects(db_session, "DELETE FROM payments WHERE id = :p", FK_ROW_REFERENCED, p=p)
    await rejects(db_session, "DELETE FROM units WHERE id = :u", FK_ROW_REFERENCED, u=ids["unit"])


async def test_amounts_keep_two_decimals_exactly(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await db_session.execute(text("UPDATE payments SET amount = 0.01 WHERE id = :p"), {"p": ids["payment"]})
    assert (await rows(db_session, "SELECT amount FROM payments WHERE id = :p", p=ids["payment"])) == [(Decimal("0.01"),)]
    await db_session.execute(text("UPDATE charges SET amount = 9999999999.99, due_date = :d WHERE id = :c"),
                             {"c": ids["charge"], "d": date(2026, 2, 1)})
    assert (await rows(db_session, "SELECT amount FROM charges WHERE id = :c", c=ids["charge"])) == [(Decimal("9999999999.99"),)]
    # A value that does not fit DECIMAL(12,2) is an error in strict mode, never truncated.
    await rejects(db_session, "UPDATE charges SET amount = 99999999999.00 WHERE id = :c", 1264, c=ids["charge"])
