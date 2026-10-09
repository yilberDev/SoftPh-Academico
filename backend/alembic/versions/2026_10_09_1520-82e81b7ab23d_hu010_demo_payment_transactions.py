"""hu010 demo payment transactions

Revision ID: 82e81b7ab23d
Revises: 3e565db40412
Create Date: 2026-10-09 15:20:47

HU-010 (persistence only): the transactions of the internal demo payment
gateway. No real money, no external provider, no card data. payments keeps
only completed payments; a rejected, failed, cancelled or expired transaction
is kept here and never has a payment (nor a receipt).

demo_payment_transactions:
- resident_id: the resident who pays (users.id).
- charge_id: the one obligation selected (charges.id); the apartment is the
  charge's and is not repeated here.
- amount DECIMAL(12,2) > 0. Nothing derived is stored (balances, applied
  amounts): they come from charges, payments and payment_allocations.
- reference VARCHAR(100): the demo reference (never a real provider's),
  UNIQUE, never blank; it fits payments.transaction_reference.
- status: INITIATED | APPROVED | REJECTED | FAILED | CANCELLED | EXPIRED.
  reference and status use the binary collation utf8mb4_0900_bin (as
  feedback.category): exact uniqueness, and the CHECK rejects 'approved'.
- created_at, expires_at (set by the service: creation + 15 minutes; the
  database only requires expires_at > created_at), finished_at: NULL exactly
  while INITIATED. All UTC, as every DATETIME(6) of the schema.
- payment_id: the payment created on approval, present exactly when APPROVED;
  UNIQUE, so a payment belongs to one transaction at most.

Foreign keys RESTRICT: financial history is never deleted by cascade.
Indexes: the foreign keys' (resident_id, charge_id; payment_id is served by its
UNIQUE index) and the UNIQUE reference. payments, payment_allocations and
charges are not touched.

Written by hand (Alembic does not compare CHECK constraints); the tests verify
the real structure in information_schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '82e81b7ab23d'
down_revision: Union[str, Sequence[str], None] = '3e565db40412'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "demo_payment_transactions"

CREATE = """
CREATE TABLE demo_payment_transactions (
    id BIGINT NOT NULL AUTO_INCREMENT,
    resident_id BIGINT NOT NULL,
    charge_id BIGINT NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    reference VARCHAR(100) COLLATE utf8mb4_0900_bin NOT NULL,
    status VARCHAR(20) COLLATE utf8mb4_0900_bin NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    expires_at DATETIME(6) NOT NULL,
    finished_at DATETIME(6) NULL,
    payment_id BIGINT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_demo_payment_transactions_reference UNIQUE (reference),
    CONSTRAINT uq_demo_payment_transactions_payment_id UNIQUE (payment_id),
    INDEX ix_demo_payment_transactions_resident_id (resident_id),
    INDEX ix_demo_payment_transactions_charge_id (charge_id),
    CONSTRAINT fk_demo_payment_transactions_resident_id_users
        FOREIGN KEY (resident_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_demo_payment_transactions_charge_id_charges
        FOREIGN KEY (charge_id) REFERENCES charges (id) ON DELETE RESTRICT,
    CONSTRAINT fk_demo_payment_transactions_payment_id_payments
        FOREIGN KEY (payment_id) REFERENCES payments (id) ON DELETE RESTRICT,
    CONSTRAINT ck_demo_payment_transactions_amount_positive CHECK (amount > 0),
    CONSTRAINT ck_demo_payment_transactions_status_valid
        CHECK (status IN ('INITIATED', 'APPROVED', 'REJECTED', 'FAILED', 'CANCELLED', 'EXPIRED')),
    CONSTRAINT ck_demo_payment_transactions_reference_not_blank CHECK (CHAR_LENGTH(TRIM(reference)) > 0),
    CONSTRAINT ck_demo_payment_transactions_expires_after_creation CHECK (expires_at > created_at),
    CONSTRAINT ck_demo_payment_transactions_finished_unless_initiated
        CHECK ((status = 'INITIATED') = (finished_at IS NULL)),
    CONSTRAINT ck_demo_payment_transactions_approved_has_payment
        CHECK ((status = 'APPROVED') = (payment_id IS NOT NULL))
) ENGINE=InnoDB
"""


def _exists(bind: sa.Connection) -> bool:
    found = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :name"
        ),
        {"name": TABLE},
    ).scalar_one()
    return bool(found)


def upgrade() -> None:
    if _exists(op.get_bind()):
        raise RuntimeError(f"Table already exists: {TABLE}. Check it manually. Nothing was changed.")
    op.execute(CREATE)


def downgrade() -> None:
    bind = op.get_bind()
    # Validate first: the transactions (financial history) are never dropped with data.
    if not _exists(bind):
        raise RuntimeError(f"Downgrade aborted: table missing: {TABLE}. Nothing was changed.")
    rows = bind.execute(sa.text(f"SELECT COUNT(*) FROM {TABLE}")).scalar_one()
    if rows:
        raise RuntimeError(f"Downgrade aborted: {rows} rows in {TABLE} would be lost. Nothing was changed.")
    op.execute(f"DROP TABLE {TABLE}")
