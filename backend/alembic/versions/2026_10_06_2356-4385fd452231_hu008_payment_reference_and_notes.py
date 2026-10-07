"""hu008 payment reference and notes

Revision ID: 4385fd452231
Revises: f3c8fc28babe
Create Date: 2026-10-06 23:56:34

HU-008 (criterion 3.1): the detail of a payment shows its transaction reference
and the administrator's notes. Two optional columns of payments, entered by the
administrator when registering a manual payment (HU-004):

- transaction_reference VARCHAR(100) NULL: the external reference of the
  transaction (bank transfer, card voucher...). NULL when there is none; never
  invented and never the receipt number (RC-00000001 stays derived from id).
  The electronic payment (HU-010) will reuse it.
- notes VARCHAR(500) NULL: the administrator's comments on the payment.

Existing payments keep both NULL (no backfill). The CHECKs only forbid a blank
value: "nothing" is always NULL. No index: neither column is filtered or sorted.
payment_allocations, the payment date (payment_date) and registered_at are not
touched.

Written by hand (Alembic does not compare CHECK constraints); the tests verify
the real structure in information_schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4385fd452231'
down_revision: Union[str, Sequence[str], None] = 'f3c8fc28babe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_COLUMNS = ("transaction_reference", "notes")

UPGRADE = """
ALTER TABLE payments
    ADD COLUMN transaction_reference VARCHAR(100) NULL AFTER source,
    ADD COLUMN notes VARCHAR(500) NULL AFTER transaction_reference,
    ADD CONSTRAINT ck_payments_transaction_reference_not_blank
        CHECK (transaction_reference IS NULL OR CHAR_LENGTH(TRIM(transaction_reference)) > 0),
    ADD CONSTRAINT ck_payments_notes_not_blank
        CHECK (notes IS NULL OR CHAR_LENGTH(TRIM(notes)) > 0)
"""

DOWNGRADE = """
ALTER TABLE payments
    DROP CHECK ck_payments_transaction_reference_not_blank,
    DROP CHECK ck_payments_notes_not_blank,
    DROP COLUMN transaction_reference,
    DROP COLUMN notes
"""


def _columns(bind: sa.Connection) -> set[str]:
    rows = bind.execute(
        sa.text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'payments'"
        )
    ).scalars()
    return set(rows)


def upgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind) & set(NEW_COLUMNS)
    if present:
        raise RuntimeError(f"Columns already exist in payments: {sorted(present)}. Check them manually. Nothing was changed.")
    op.execute(UPGRADE)


def downgrade() -> None:
    bind = op.get_bind()
    missing = set(NEW_COLUMNS) - _columns(bind)
    if missing:
        raise RuntimeError(f"Downgrade aborted: columns missing in payments: {sorted(missing)}. Nothing was changed.")
    rows = bind.execute(
        sa.text("SELECT COUNT(*) FROM payments WHERE transaction_reference IS NOT NULL OR notes IS NOT NULL")
    ).scalar_one()
    if rows:
        raise RuntimeError(f"Downgrade aborted: {rows} rows in payments would lose data. Nothing was changed.")
    op.execute(DOWNGRADE)
