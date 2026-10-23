"""HU-010 (persistence): the real structure of demo_payment_transactions and the rules MySQL
enforces itself (DECIMAL money, CHECKs, UNIQUE, foreign keys RESTRICT), including the test
instrument of the demo checkout (migration 41602195f69f, the head: its chain, round trip,
existing rows and protected downgrade are at the end; they only ever run on the *_test database).

Every rule here is checked with raw SQL against MySQL: the model has no Python
validation of its own (its agreement with the database is `alembic check`, in
test_demo_payment_transactions_migration.py).
"""

from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utc_now
from app.models import DemoPaymentTransaction, DemoPaymentTransactionStatus
from tests.integration.conftest import alembic_config
from tests.integration.test_demo_gateway_payments_migration import SETUP, current_revision, run_sql
from tests.integration.test_payments_database import (
    CHECK_VIOLATED,
    DUPLICATE,
    FK_PARENT_MISSING,
    FK_ROW_REFERENCED,
    rejects,
    rows,
)
from tests.integration.test_residents_api import Community

pytestmark = pytest.mark.anyio

TABLE = "demo_payment_transactions"
DATA_TOO_LONG = 1406
OUT_OF_RANGE = 1264

INSERT = (
    "INSERT INTO demo_payment_transactions (resident_id, charge_id, amount, reference, status, expires_at, "
    "finished_at, payment_id) VALUES (:r, :c, :amount, :ref, :status, "
    "UTC_TIMESTAMP(6) + INTERVAL 15 MINUTE, :finished, :p)"
)
WITH_INSTRUMENT = INSERT.replace("payment_id)", "payment_id, test_instrument)").replace(":p)", ":p, :instrument)")


@pytest.fixture
async def ids(db_session: AsyncSession, make_user: Any) -> dict[str, int]:
    """A resident with an obligation and two payments of their apartment (as HU-004 stores them)."""
    admin = await make_user("admin@example.com", password_hash="not-a-real-hash")
    community = Community(db_session, admin.id)
    resident = await community.resident("Laura Gómez", "1012345678", (1, 1, 101))
    unit_id = (await rows(db_session, "SELECT unit_id FROM resident_units WHERE user_id = :u", u=resident))[0][0]
    await db_session.execute(text(
        "INSERT INTO charges (unit_id, concept, amount, due_date, billing_period, created_by_user_id) "
        "VALUES (:u, 'MONTHLY_FEE', 100000, '2026-10-05', '2026-10-01', :a)"), {"u": unit_id, "a": admin.id})
    charge_id = (await rows(db_session, "SELECT MAX(id) FROM charges"))[0][0]
    payments = []
    for _ in range(2):
        await db_session.execute(text(
            "INSERT INTO payments (resident_id, unit_id, registered_by_user_id, amount, method, concept, payment_date, "
            "registered_at, resident_name_snapshot, unit_label_snapshot) VALUES (:r, :u, :a, 50000, 'CARD', "
            "'MONTHLY_FEE', '2026-10-09', UTC_TIMESTAMP(6), 'Laura Gómez', 'B1-TO1-APTO-101')"),
            {"r": resident, "u": unit_id, "a": admin.id})
        payments.append((await rows(db_session, "SELECT MAX(id) FROM payments"))[0][0])
    return {"admin": admin.id, "resident": resident, "unit": unit_id, "charge": charge_id,
            "payment": payments[0], "other_payment": payments[1]}


def values(ids: dict[str, int], **changes: Any) -> dict[str, Any]:
    """A valid INITIATED transaction, with the given changes."""
    return {"r": ids["resident"], "c": ids["charge"], "amount": "50000.00", "ref": "DEMO-0001",
            "status": "INITIATED", "finished": None, "p": None} | changes


async def insert(db: AsyncSession, **params: Any) -> int:
    await db.execute(text(INSERT), params)
    return (await rows(db, "SELECT MAX(id) FROM demo_payment_transactions"))[0][0]


FINISHED = "2026-10-09 15:30:00"


# --- Structure -----------------------------------------------------------------------------------------------


async def test_columns(db_session: AsyncSession) -> None:
    assert await rows(
        db_session,
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLLATION_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t ORDER BY ORDINAL_POSITION", t=TABLE,
    ) == [
        ("id", "bigint", "NO", None, None),
        ("resident_id", "bigint", "NO", None, None),
        ("charge_id", "bigint", "NO", None, None),
        ("amount", "decimal(12,2)", "NO", None, None),  # as every amount of the financial model
        ("reference", "varchar(100)", "NO", None, "utf8mb4_0900_bin"),
        ("status", "varchar(20)", "NO", None, "utf8mb4_0900_bin"),
        ("created_at", "datetime(6)", "NO", "CURRENT_TIMESTAMP(6)", None),
        ("expires_at", "datetime(6)", "NO", None, None),
        ("finished_at", "datetime(6)", "YES", None, None),
        ("payment_id", "bigint", "YES", None, None),
        ("test_instrument", "varchar(40)", "YES", None, "utf8mb4_0900_bin"),  # 41602195f69f: an id, NULL allowed
    ]
    # Nothing derived (balances, applied amounts) and no card data: only the columns above.


async def test_constraints_indexes_and_foreign_keys(db_session: AsyncSession) -> None:
    assert await rows(
        db_session,
        "SELECT cc.CONSTRAINT_NAME, cc.CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS cc "
        "JOIN information_schema.TABLE_CONSTRAINTS tc ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA "
        "AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME WHERE cc.CONSTRAINT_SCHEMA = DATABASE() "
        "AND tc.TABLE_NAME = :t AND tc.ENFORCED = 'YES' ORDER BY 1", t=TABLE,
    ) == [
        ("ck_demo_payment_transactions_amount_positive", "(`amount` > 0)"),
        ("ck_demo_payment_transactions_approved_has_payment",
         "((`status` = _utf8mb4\\'APPROVED\\') = (`payment_id` is not null))"),
        ("ck_demo_payment_transactions_expires_after_creation", "(`expires_at` > `created_at`)"),
        ("ck_demo_payment_transactions_finished_unless_initiated",
         "((`status` = _utf8mb4\\'INITIATED\\') = (`finished_at` is null))"),
        ("ck_demo_payment_transactions_reference_not_blank", "(char_length(trim(`reference`)) > 0)"),
        ("ck_demo_payment_transactions_status_valid",
         "(`status` in (_utf8mb4\\'INITIATED\\',_utf8mb4\\'APPROVED\\',_utf8mb4\\'REJECTED\\',"
         "_utf8mb4\\'FAILED\\',_utf8mb4\\'CANCELLED\\',_utf8mb4\\'EXPIRED\\'))"),
        ("ck_demo_payment_transactions_test_instrument_resolved",
         ("((`test_instrument` is null) or (`status` in (_utf8mb4\\'APPROVED\\',_utf8mb4\\'REJECTED\\',"
          "_utf8mb4\\'FAILED\\')))")),
    ]
    assert await rows(
        db_session,
        "SELECT INDEX_NAME, NON_UNIQUE, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t GROUP BY 1, 2 ORDER BY 1", t=TABLE,
    ) == [
        ("ix_demo_payment_transactions_charge_id", 1, "charge_id"),
        ("ix_demo_payment_transactions_resident_id", 1, "resident_id"),
        ("PRIMARY", 0, "id"),
        ("uq_demo_payment_transactions_payment_id", 0, "payment_id"),
        ("uq_demo_payment_transactions_reference", 0, "reference"),
    ]
    # Financial history is never deleted (nor changed) by cascade.
    assert await rows(
        db_session,
        "SELECT CONSTRAINT_NAME, REFERENCED_TABLE_NAME, DELETE_RULE, UPDATE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = :t ORDER BY 1", t=TABLE,
    ) == [
        ("fk_demo_payment_transactions_charge_id_charges", "charges", "RESTRICT", "NO ACTION"),
        ("fk_demo_payment_transactions_payment_id_payments", "payments", "RESTRICT", "NO ACTION"),
        ("fk_demo_payment_transactions_resident_id_users", "users", "RESTRICT", "NO ACTION"),
    ]
    assert await rows(
        db_session,
        "SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY 1", t=TABLE,
    ) == [("charge_id", "charges", "id"), ("payment_id", "payments", "id"), ("resident_id", "users", "id")]


# --- Rules enforced by MySQL ------------------------------------------------------------------------------------


async def test_every_status_the_model_knows_is_accepted(db_session: AsyncSession, ids: dict[str, int]) -> None:
    for number, status in enumerate(DemoPaymentTransactionStatus):
        approved = status == DemoPaymentTransactionStatus.APPROVED
        await insert(db_session, **values(
            ids, ref=f"DEMO-{number}", status=status.value,
            finished=None if status == DemoPaymentTransactionStatus.INITIATED else FINISHED,
            p=ids["payment"] if approved else None,
        ))
    assert await rows(db_session, "SELECT status FROM demo_payment_transactions ORDER BY id") == [
        ("INITIATED",), ("APPROVED",), ("REJECTED",), ("FAILED",), ("CANCELLED",), ("EXPIRED",)
    ]


@pytest.mark.parametrize("status", ["", "PENDING", "COMPLETED", "approved", "Initiated", "APPROVED "])
async def test_a_status_outside_the_set_is_rejected(db_session: AsyncSession, ids: dict[str, int], status: str) -> None:
    await rejects(db_session, INSERT, CHECK_VIOLATED, **values(ids, status=status, finished=FINISHED))


@pytest.mark.parametrize("amount", ["0", "0.00", "-0.01", "-50000"])
async def test_amount_zero_or_negative_is_rejected(db_session: AsyncSession, ids: dict[str, int], amount: str) -> None:
    await rejects(db_session, INSERT, CHECK_VIOLATED, **values(ids, amount=amount))


async def test_amount_keeps_two_decimals_exactly(db_session: AsyncSession, ids: dict[str, int]) -> None:
    first = await insert(db_session, **values(ids, amount="0.01", ref="DEMO-A"))
    second = await insert(db_session, **values(ids, amount="9999999999.99", ref="DEMO-B"))
    assert await rows(db_session, "SELECT amount FROM demo_payment_transactions WHERE id IN (:a, :b) ORDER BY id",
                      a=first, b=second) == [(Decimal("0.01"),), (Decimal("9999999999.99"),)]
    # Too large for DECIMAL(12,2): an error in strict mode, never truncated.
    await rejects(db_session, INSERT, OUT_OF_RANGE, **values(ids, amount="99999999999.00", ref="DEMO-C"))


async def test_reference_is_unique_exact_and_never_blank(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await insert(db_session, **values(ids, ref="DEMO-0001"))
    await rejects(db_session, INSERT, DUPLICATE, **values(ids, ref="DEMO-0001"))
    # Binary collation: another case is another reference.
    await insert(db_session, **values(ids, ref="demo-0001"))
    for blank in ("", "   "):
        await rejects(db_session, INSERT, CHECK_VIOLATED, **values(ids, ref=blank))
    await insert(db_session, **values(ids, ref="R" * 100))
    await rejects(db_session, INSERT, DATA_TOO_LONG, **values(ids, ref="S" * 101))
    await rejects(db_session, INSERT.replace(":ref", "NULL"), 1048, **values(ids))  # NOT NULL


async def test_a_payment_is_linked_to_one_transaction_at_most(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await insert(db_session, **values(ids, ref="DEMO-1", status="APPROVED", finished=FINISHED, p=ids["payment"]))
    await rejects(db_session, INSERT, DUPLICATE,
                  **values(ids, ref="DEMO-2", status="APPROVED", finished=FINISHED, p=ids["payment"]))
    # Another payment can be linked; many transactions without a payment can coexist (NULL never collides).
    await insert(db_session, **values(ids, ref="DEMO-3", status="APPROVED", finished=FINISHED, p=ids["other_payment"]))
    await insert(db_session, **values(ids, ref="DEMO-4", status="REJECTED", finished=FINISHED))
    await insert(db_session, **values(ids, ref="DEMO-5", status="FAILED", finished=FINISHED))
    assert await rows(db_session, "SELECT COUNT(*) FROM demo_payment_transactions WHERE payment_id IS NULL") == [(2,)]


@pytest.mark.parametrize("status", ["INITIATED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED"])
async def test_only_an_approved_transaction_has_a_payment(db_session: AsyncSession, ids: dict[str, int], status: str) -> None:
    finished = None if status == "INITIATED" else FINISHED
    await rejects(db_session, INSERT, CHECK_VIOLATED, **values(ids, status=status, finished=finished, p=ids["payment"]))


async def test_an_approved_transaction_always_has_its_payment(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await rejects(db_session, INSERT, CHECK_VIOLATED, **values(ids, status="APPROVED", finished=FINISHED))
    # Approval sets the status, the payment and the end at once (a single UPDATE).
    transaction = await insert(db_session, **values(ids))
    await db_session.execute(text(
        "UPDATE demo_payment_transactions SET status = 'APPROVED', payment_id = :p, finished_at = UTC_TIMESTAMP(6) "
        "WHERE id = :id"), {"p": ids["payment"], "id": transaction})
    # It can never lose its payment afterwards.
    await rejects(db_session, "UPDATE demo_payment_transactions SET payment_id = NULL WHERE id = :id",
                  CHECK_VIOLATED, id=transaction)
    await rejects(db_session, "UPDATE demo_payment_transactions SET status = 'FAILED' WHERE id = :id",
                  CHECK_VIOLATED, id=transaction)


@pytest.mark.parametrize("status", ["APPROVED", "REJECTED", "FAILED", "CANCELLED", "EXPIRED"])
async def test_a_finished_transaction_has_its_end(db_session: AsyncSession, ids: dict[str, int], status: str) -> None:
    payment = ids["payment"] if status == "APPROVED" else None
    await rejects(db_session, INSERT, CHECK_VIOLATED, **values(ids, status=status, finished=None, p=payment))


async def test_a_pending_transaction_has_no_end(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await rejects(db_session, INSERT, CHECK_VIOLATED, **values(ids, status="INITIATED", finished=FINISHED))


async def test_expiry_is_after_creation(db_session: AsyncSession, ids: dict[str, int]) -> None:
    sql = ("INSERT INTO demo_payment_transactions (resident_id, charge_id, amount, reference, status, created_at, "
           "expires_at) VALUES (:r, :c, 1, :ref, 'INITIATED', '2026-10-09 15:00:00', :expires)")
    for expires in ("2026-10-09 15:00:00", "2026-10-09 14:59:59"):
        await rejects(db_session, sql, CHECK_VIOLATED, r=ids["resident"], c=ids["charge"], ref="DEMO-X", expires=expires)
    await db_session.execute(text(sql), {"r": ids["resident"], "c": ids["charge"], "ref": "DEMO-X",
                                         "expires": "2026-10-09 15:15:00"})


async def test_foreign_keys_reject_what_does_not_exist(db_session: AsyncSession, ids: dict[str, int]) -> None:
    missing = 999_999_999
    await rejects(db_session, INSERT, FK_PARENT_MISSING, **values(ids, r=missing))
    await rejects(db_session, INSERT, FK_PARENT_MISSING, **values(ids, c=missing))
    await rejects(db_session, INSERT, FK_PARENT_MISSING,
                  **values(ids, status="APPROVED", finished=FINISHED, p=missing))


async def test_history_is_never_deleted_by_cascade(db_session: AsyncSession, ids: dict[str, int]) -> None:
    transaction = await insert(db_session, **values(ids, status="APPROVED", finished=FINISHED, p=ids["payment"]))
    await rejects(db_session, "DELETE FROM payments WHERE id = :p", FK_ROW_REFERENCED, p=ids["payment"])
    await rejects(db_session, "DELETE FROM charges WHERE id = :c", FK_ROW_REFERENCED, c=ids["charge"])
    await rejects(db_session, "DELETE FROM users WHERE id = :r", FK_ROW_REFERENCED, r=ids["resident"])
    assert await rows(db_session, "SELECT COUNT(*) FROM demo_payment_transactions WHERE id = :id", id=transaction) == [(1,)]


async def test_the_model_maps_the_table(db_session: AsyncSession, ids: dict[str, int]) -> None:
    """The ORM writes and reads the table as created by the migration (defaults, UTC, Decimal)."""
    now = utc_now()
    transaction = DemoPaymentTransaction(
        resident_id=ids["resident"], charge_id=ids["charge"], amount=Decimal("12345.67"), reference="DEMO-ORM",
        status=DemoPaymentTransactionStatus.INITIATED, expires_at=now + timedelta(minutes=15),
    )
    db_session.add(transaction)
    await db_session.flush()
    await db_session.refresh(transaction)
    assert transaction.amount == Decimal("12345.67")
    assert transaction.status == "INITIATED" and transaction.finished_at is None and transaction.payment_id is None
    # created_at is set by MySQL (UTC session time zone), close to the application's UTC clock.
    assert abs(transaction.created_at - now) < timedelta(minutes=1)
    assert transaction.expires_at == now + timedelta(minutes=15)


# --- The test instrument of the demo checkout (41602195f69f) ----------------------------------------------------

NEW_REVISION = "41602195f69f"
PREVIOUS_REVISION = "f697625db563"


def resolved(ids: dict[str, int], status: str, **changes: Any) -> dict[str, Any]:
    """A finished transaction with the given status (and its payment when APPROVED)."""
    payment = ids["payment"] if status == "APPROVED" else None
    return values(ids, status=status, finished=FINISHED, p=payment) | changes


@pytest.mark.parametrize("status", ["APPROVED", "REJECTED", "FAILED"])
async def test_a_resolved_transaction_keeps_its_instrument(db_session: AsyncSession, ids: dict[str, int],
                                                           status: str) -> None:
    await db_session.execute(text(WITH_INSTRUMENT), resolved(ids, status, instrument="TEST_CARD_APPROVE"))
    assert await rows(db_session, "SELECT status, test_instrument FROM demo_payment_transactions") == [
        (status, "TEST_CARD_APPROVE")
    ]


@pytest.mark.parametrize("status", ["INITIATED", "CANCELLED", "EXPIRED"])
async def test_an_unresolved_transaction_has_no_instrument(db_session: AsyncSession, ids: dict[str, int],
                                                           status: str) -> None:
    finished = None if status == "INITIATED" else FINISHED
    await rejects(db_session, WITH_INSTRUMENT, CHECK_VIOLATED,
                  **values(ids, status=status, finished=finished, instrument="TEST_CARD_APPROVE"))
    await db_session.execute(text(WITH_INSTRUMENT), values(ids, status=status, finished=finished, instrument=None))
    assert await rows(db_session, "SELECT status, test_instrument FROM demo_payment_transactions") == [(status, None)]


async def test_without_an_instrument_every_status_stays_valid(db_session: AsyncSession, ids: dict[str, int]) -> None:
    # As every transaction written before 41602195f69f: the column is simply NULL.
    for number, status in enumerate(DemoPaymentTransactionStatus):
        finished = None if status == DemoPaymentTransactionStatus.INITIATED else FINISHED
        payment = ids["payment"] if status == DemoPaymentTransactionStatus.APPROVED else None
        await insert(db_session, **values(ids, ref=f"DEMO-{number}", status=status.value, finished=finished, p=payment))
    assert await rows(db_session, "SELECT status, test_instrument FROM demo_payment_transactions ORDER BY id") == [
        (status.value, None) for status in DemoPaymentTransactionStatus
    ]


async def test_the_instrument_comes_with_the_result(db_session: AsyncSession, ids: dict[str, int]) -> None:
    transaction = await insert(db_session, **values(ids))
    # Never on a pending transaction...
    await rejects(db_session, "UPDATE demo_payment_transactions SET test_instrument = 'TEST_CARD_APPROVE' WHERE id = :id",
                  CHECK_VIOLATED, id=transaction)
    # ...but together with its result, in one UPDATE.
    await db_session.execute(text(
        "UPDATE demo_payment_transactions SET status = 'REJECTED', finished_at = UTC_TIMESTAMP(6), "
        "test_instrument = 'TEST_CARD_INSUFFICIENT_FUNDS' WHERE id = :id"), {"id": transaction})
    assert await rows(db_session, "SELECT status, test_instrument FROM demo_payment_transactions") == [
        ("REJECTED", "TEST_CARD_INSUFFICIENT_FUNDS")
    ]


async def test_the_instrument_is_exact_and_short(db_session: AsyncSession, ids: dict[str, int]) -> None:
    await db_session.execute(text(WITH_INSTRUMENT), resolved(ids, "REJECTED", ref="DEMO-1", instrument="I" * 40))
    await rejects(db_session, WITH_INSTRUMENT, DATA_TOO_LONG, **resolved(ids, "REJECTED", ref="DEMO-2", instrument="J" * 41))
    await db_session.execute(text(WITH_INSTRUMENT), resolved(ids, "FAILED", ref="DEMO-3", instrument="TEST_CARD_APPROVE"))
    # Binary collation: another case is another id.
    assert await rows(db_session, "SELECT COUNT(*) FROM demo_payment_transactions "
                                  "WHERE test_instrument = 'test_card_approve'") == [(0,)]


async def test_the_model_and_the_database_agree(db_session: AsyncSession) -> None:
    table = DemoPaymentTransaction.__table__
    checks = {str(c.name) for c in table.constraints if isinstance(c, CheckConstraint)}
    assert {name for (name,) in await rows(
        db_session,
        "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() "
        "AND TABLE_NAME = :t AND CONSTRAINT_TYPE = 'CHECK'", t=TABLE,
    )} == checks
    column = table.c.test_instrument
    assert (column.nullable, column.type.length, column.type.collation) == (True, 40, "utf8mb4_0900_bin")  # type: ignore[attr-defined]
    assert await rows(
        db_session,
        "SELECT IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH, COLLATION_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = 'test_instrument'", t=TABLE,
    ) == [("YES", 40, "utf8mb4_0900_bin")]


# Migration 41602195f69f itself: committed rows on the *_test database, always left at head.

CHARGE = (
    "INSERT INTO charges (unit_id, concept, amount, due_date, billing_period, created_by_user_id) "
    "SELECT units.id, 'MONTHLY_FEE', 100000, '2026-10-05', '2026-10-01', users.id FROM units, users "
    "WHERE users.role = 'ADMINISTRADOR'"
)


def old_transaction(reference: str, status: str, finished: str) -> str:
    """A transaction as written before 41602195f69f (no test_instrument column)."""
    return (
        "INSERT INTO demo_payment_transactions (resident_id, charge_id, amount, reference, status, expires_at, "
        f"finished_at) SELECT users.id, charges.id, 1000, '{reference}', '{status}', "
        f"UTC_TIMESTAMP(6) + INTERVAL 15 MINUTE, {finished} FROM users, charges WHERE users.role = 'RESIDENTE'"
    )


def structure(url: URL) -> tuple[int, int]:
    """(test_instrument columns, its CHECKs): (1, 1) after the migration, (0, 0) before it."""
    [columns, checks] = run_sql(
        url,
        "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'demo_payment_transactions' AND COLUMN_NAME = 'test_instrument'",
        "SELECT COUNT(*) FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() "
        "AND CONSTRAINT_NAME = 'ck_demo_payment_transactions_test_instrument_resolved'",
    )
    return columns[0][0], checks[0][0]


@pytest.fixture
def before_instruments(migrated_test_database: URL) -> Iterator[URL]:
    """The database at f697625db563 with a rejected and a pending transaction, as they existed before."""
    assert migrated_test_database.database is not None and migrated_test_database.database.endswith("_test")
    config = alembic_config(migrated_test_database)
    command.downgrade(config, PREVIOUS_REVISION)
    try:
        run_sql(migrated_test_database, *SETUP, CHARGE,
                old_transaction("DEMO-OLD-1", "REJECTED", "UTC_TIMESTAMP(6)"),
                old_transaction("DEMO-OLD-2", "INITIATED", "NULL"))
        yield migrated_test_database
    finally:
        run_sql(migrated_test_database, "DELETE FROM demo_payment_transactions", "DELETE FROM charges",
                "DELETE FROM units", "DELETE FROM towers", "DELETE FROM blocks", "DELETE FROM users")
        command.upgrade(config, "head")


def test_the_instrument_migration_is_the_head(migrated_test_database: URL) -> None:
    script = ScriptDirectory.from_config(alembic_config(migrated_test_database))
    assert script.get_revision(NEW_REVISION).down_revision == PREVIOUS_REVISION
    # HU-017 (d33ea6140186) comes after it; test_notifications_migration.py checks the head.
    assert NEW_REVISION in {revision.revision for revision in script.walk_revisions()}
    assert NEW_REVISION in {revision.down_revision for revision in script.walk_revisions()}


def test_existing_transactions_stay_valid_with_null(before_instruments: URL) -> None:
    url = before_instruments
    assert structure(url) == (0, 0) and current_revision(url) == PREVIOUS_REVISION
    [before] = run_sql(url, "SELECT id, reference, status, finished_at, payment_id FROM demo_payment_transactions "
                            "ORDER BY id")

    command.upgrade(alembic_config(url), NEW_REVISION)

    assert structure(url) == (1, 1) and current_revision(url) == NEW_REVISION
    [after] = run_sql(url, "SELECT id, reference, status, finished_at, payment_id, test_instrument "
                           "FROM demo_payment_transactions ORDER BY id")
    assert [row[:5] for row in after] == before and [row[5] for row in after] == [None, None]
    # The pending one goes on without an instrument (cancelled), as the rules always allowed.
    run_sql(url, "UPDATE demo_payment_transactions SET status = 'CANCELLED', finished_at = UTC_TIMESTAMP(6) "
                 "WHERE reference = 'DEMO-OLD-2'")
    assert run_sql(url, "SELECT reference, status, test_instrument FROM demo_payment_transactions ORDER BY id") == [[
        ("DEMO-OLD-1", "REJECTED", None), ("DEMO-OLD-2", "CANCELLED", None),
    ]]


def test_round_trip_and_a_protected_downgrade(before_instruments: URL) -> None:
    url, config = before_instruments, alembic_config(before_instruments)
    command.upgrade(config, NEW_REVISION)
    run_sql(url, "UPDATE demo_payment_transactions SET test_instrument = 'TEST_CARD_INSUFFICIENT_FUNDS' "
                 "WHERE reference = 'DEMO-OLD-1'")  # committed: a rejected transaction resolved by an instrument

    with pytest.raises(RuntimeError, match="1 test instruments in demo_payment_transactions would be lost.*"
                                           "Nothing was changed"):
        command.downgrade(config, PREVIOUS_REVISION)
    assert structure(url) == (1, 1) and current_revision(url) == NEW_REVISION
    assert run_sql(url, "SELECT test_instrument FROM demo_payment_transactions WHERE reference = 'DEMO-OLD-1'") == [
        [("TEST_CARD_INSUFFICIENT_FUNDS",)]
    ]

    run_sql(url, "UPDATE demo_payment_transactions SET test_instrument = NULL")
    command.downgrade(config, PREVIOUS_REVISION)  # the CHECK first, then the column
    assert structure(url) == (0, 0) and current_revision(url) == PREVIOUS_REVISION
    command.upgrade(config, NEW_REVISION)
    assert structure(url) == (1, 1) and current_revision(url) == NEW_REVISION
    assert run_sql(url, "SELECT reference, status, test_instrument FROM demo_payment_transactions ORDER BY id") == [[
        ("DEMO-OLD-1", "REJECTED", None), ("DEMO-OLD-2", "INITIATED", None),
    ]]
