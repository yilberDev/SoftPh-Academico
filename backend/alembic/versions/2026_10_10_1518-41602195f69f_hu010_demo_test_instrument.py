"""hu010 demo test instrument

Revision ID: 41602195f69f
Revises: f697625db563
Create Date: 2026-10-10 15:18:29

HU-010 (demo checkout): which fictitious test instrument resolved a demo
transaction. Nothing else changes: no index, no relation, no other column.

demo_payment_transactions.test_instrument VARCHAR(40), utf8mb4_0900_bin (as
reference and status: an id is exact), NULL allowed:
- an id of services/demo_instruments.py (TEST_CARD_APPROVE, ...), never card
  or bank data;
- every existing transaction keeps NULL (resolved before instruments existed,
  or never resolved by one), and stays valid;
- ck_demo_payment_transactions_test_instrument_resolved: a value only on
  APPROVED, REJECTED or FAILED, the results an instrument resolves; INITIATED,
  CANCELLED and EXPIRED keep NULL.

The downgrade drops the CHECK, then the column; it refuses while a transaction
has an instrument (that history would be lost). Written by hand (Alembic does
not compare CHECK constraints); the tests verify the real structure in
information_schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '41602195f69f'
down_revision: Union[str, Sequence[str], None] = 'f697625db563'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "demo_payment_transactions"
COLUMN = "test_instrument"
CHECK = "ck_demo_payment_transactions_test_instrument_resolved"

UPGRADE = f"""
ALTER TABLE {TABLE}
    ADD COLUMN {COLUMN} VARCHAR(40) COLLATE utf8mb4_0900_bin NULL,
    ADD CONSTRAINT {CHECK} CHECK ({COLUMN} IS NULL OR status IN ('APPROVED', 'REJECTED', 'FAILED'))
"""


def _column_exists(bind: sa.Connection) -> bool:
    found = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table AND COLUMN_NAME = :column"
        ),
        {"table": TABLE, "column": COLUMN},
    ).scalar_one()
    return bool(found)


def upgrade() -> None:
    if _column_exists(op.get_bind()):
        raise RuntimeError(f"Column already exists: {TABLE}.{COLUMN}. Check it manually. Nothing was changed.")
    op.execute(UPGRADE)


def downgrade() -> None:
    bind = op.get_bind()
    # Validate first: an instrument already recorded is never dropped silently.
    if not _column_exists(bind):
        raise RuntimeError(f"Downgrade aborted: column missing: {TABLE}.{COLUMN}. Nothing was changed.")
    rows = bind.execute(sa.text(f"SELECT COUNT(*) FROM {TABLE} WHERE {COLUMN} IS NOT NULL")).scalar_one()
    if rows:
        raise RuntimeError(f"Downgrade aborted: {rows} test instruments in {TABLE} would be lost. Nothing was changed.")
    op.execute(f"ALTER TABLE {TABLE} DROP CHECK {CHECK}")
    op.execute(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}")
