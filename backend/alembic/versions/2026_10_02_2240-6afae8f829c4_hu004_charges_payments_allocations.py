"""hu004 charges payments allocations

Revision ID: 6afae8f829c4
Revises: b1b38cfb7fe2
Create Date: 2026-10-02 22:40:00

HU-004: financial model (source of truth for balances).

- charges: obligations of an apartment (units.id), never of a user.
- payments: manual payments registered by the administrator, with the
  resident and apartment resolved by the backend and snapshots (resident name,
  apartment label) so an old receipt never changes.
- payment_allocations: how much of a payment covers each charge (FIFO).

Balances are never stored: pending = charges - allocations; credit (saldo a
favor) = payments - allocations. Money is DECIMAL(12,2), never FLOAT.

Indexes (only those used by real queries):
- ix_charges_unit_id_due_date_created_at: FIFO (unit, due_date, created_at,
  then id implicitly) and the foreign key to units.
- ix_payments_resident_id_payment_date: filter by resident; FK to users.
- ix_payments_unit_id: balance of an apartment; FK to units.
- ix_payments_payment_date: filter and sort by date.
- ix_payments_registered_by_user_id: FK to users (InnoDB needs one).
- uq_payment_allocations_payment_id: one allocation per (payment, charge);
  serves the FK and the applied amount of a payment.
- ix_payment_allocations_charge_id: the allocated amount of a charge; FK.

Written by hand (Alembic does not compare CHECK constraints); the tests
verify the real structure in information_schema. No existing table or row is
touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6afae8f829c4'
down_revision: Union[str, Sequence[str], None] = 'b1b38cfb7fe2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Creation order (parents first); the downgrade drops them in reverse.
TABLES = ("charges", "payments", "payment_allocations")

CONCEPTS = "('MONTHLY_FEE', 'LATE_FEE', 'PENALTY', 'OTHER')"

CREATE_CHARGES = f"""
CREATE TABLE charges (
    id BIGINT NOT NULL AUTO_INCREMENT,
    unit_id BIGINT NOT NULL,
    concept VARCHAR(20) NOT NULL,
    description VARCHAR(200) NULL,
    amount DECIMAL(12, 2) NOT NULL,
    due_date DATE NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    INDEX ix_charges_unit_id_due_date_created_at (unit_id, due_date, created_at),
    CONSTRAINT fk_charges_unit_id_units FOREIGN KEY (unit_id) REFERENCES units (id) ON DELETE RESTRICT,
    CONSTRAINT ck_charges_concept_valid CHECK (concept IN {CONCEPTS}),
    CONSTRAINT ck_charges_amount_positive CHECK (amount > 0),
    CONSTRAINT ck_charges_other_has_description CHECK (concept <> 'OTHER' OR description IS NOT NULL)
) ENGINE=InnoDB
"""

CREATE_PAYMENTS = f"""
CREATE TABLE payments (
    id BIGINT NOT NULL AUTO_INCREMENT,
    resident_id BIGINT NOT NULL,
    unit_id BIGINT NOT NULL,
    registered_by_user_id BIGINT NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    method VARCHAR(20) NOT NULL,
    concept VARCHAR(20) NOT NULL,
    concept_description VARCHAR(200) NULL,
    source VARCHAR(20) NOT NULL DEFAULT 'MANUAL',
    payment_date DATE NOT NULL,
    registered_at DATETIME(6) NOT NULL,
    resident_name_snapshot VARCHAR(150) NOT NULL,
    unit_label_snapshot VARCHAR(40) NOT NULL,
    receipt_email_status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    receipt_sent_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    INDEX ix_payments_resident_id_payment_date (resident_id, payment_date),
    INDEX ix_payments_unit_id (unit_id),
    INDEX ix_payments_payment_date (payment_date),
    INDEX ix_payments_registered_by_user_id (registered_by_user_id),
    CONSTRAINT fk_payments_resident_id_users FOREIGN KEY (resident_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_payments_unit_id_units FOREIGN KEY (unit_id) REFERENCES units (id) ON DELETE RESTRICT,
    CONSTRAINT fk_payments_registered_by_user_id_users
        FOREIGN KEY (registered_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_payments_amount_positive CHECK (amount > 0),
    CONSTRAINT ck_payments_method_valid CHECK (method IN ('CASH', 'CARD', 'TRANSFER')),
    CONSTRAINT ck_payments_concept_valid CHECK (concept IN {CONCEPTS}),
    CONSTRAINT ck_payments_description_only_for_other
        CHECK ((concept = 'OTHER') = (concept_description IS NOT NULL)),
    CONSTRAINT ck_payments_source_valid CHECK (source IN ('MANUAL')),
    CONSTRAINT ck_payments_receipt_email_status_valid
        CHECK (receipt_email_status IN ('PENDING', 'SENT', 'FAILED')),
    CONSTRAINT ck_payments_sent_has_timestamp CHECK (receipt_email_status <> 'SENT' OR receipt_sent_at IS NOT NULL)
) ENGINE=InnoDB
"""

CREATE_ALLOCATIONS = """
CREATE TABLE payment_allocations (
    id BIGINT NOT NULL AUTO_INCREMENT,
    payment_id BIGINT NOT NULL,
    charge_id BIGINT NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    CONSTRAINT uq_payment_allocations_payment_id UNIQUE (payment_id, charge_id),
    INDEX ix_payment_allocations_charge_id (charge_id),
    CONSTRAINT fk_payment_allocations_payment_id_payments
        FOREIGN KEY (payment_id) REFERENCES payments (id) ON DELETE RESTRICT,
    CONSTRAINT fk_payment_allocations_charge_id_charges
        FOREIGN KEY (charge_id) REFERENCES charges (id) ON DELETE RESTRICT,
    CONSTRAINT ck_payment_allocations_amount_positive CHECK (amount > 0)
) ENGINE=InnoDB
"""


def _existing(bind: sa.Connection) -> set[str]:
    rows = bind.execute(
        sa.text(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN :names"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"names": list(TABLES)},
    ).scalars()
    return set(rows)


def upgrade() -> None:
    present = _existing(op.get_bind())
    if present:
        raise RuntimeError(f"Tables already exist: {sorted(present)}. Check them manually. Nothing was changed.")
    op.execute(CREATE_CHARGES)
    op.execute(CREATE_PAYMENTS)
    op.execute(CREATE_ALLOCATIONS)


def downgrade() -> None:
    bind = op.get_bind()
    # Validate first: financial history is never dropped with data.
    missing = set(TABLES) - _existing(bind)
    if missing:
        raise RuntimeError(f"Downgrade aborted: tables missing: {sorted(missing)}. Nothing was changed.")
    for table in TABLES:
        rows = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        if rows:
            raise RuntimeError(f"Downgrade aborted: {rows} rows in {table} would be lost. Nothing was changed.")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
