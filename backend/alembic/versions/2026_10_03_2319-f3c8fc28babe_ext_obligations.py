"""ext obligations

Revision ID: f3c8fc28babe
Revises: 00d8bbdd687b
Create Date: 2026-10-03 23:19:00

Transversal extension of the financial module (obligations): prepares
charges (HU-004) for real obligations. No status, no stored applied amount and
no stored balance: they stay derived from payment_allocations.

- billing_period: DATE, the first day of the month. Every MONTHLY_FEE has one
  and no other concept does. UNIQUE (unit_id, billing_period) allows one
  monthly fee per apartment and period; the other concepts are NULL and NULLs
  never collide in a UNIQUE index.
- source_charge_id: the obligation that originated a LATE_FEE (optional:
  historical late fees have none). Only LATE_FEE may have one, and
  UNIQUE (source_charge_id) allows at most one late fee per origin.
- The origin must be of the same apartment and must not be a LATE_FEE. A CHECK
  cannot read another row, so this is a composite foreign key with two stored
  generated markers:
    can_originate_late_fee = 1 unless the row is a LATE_FEE (0);
    late_fee_source_link   = 1 when source_charge_id is set (NULL otherwise,
                             so a historical late fee is never checked).
  (unit_id, source_charge_id, late_fee_source_link)
    -> charges (unit_id, id, can_originate_late_fee), ON DELETE RESTRICT.
  The parent key is UNIQUE (unit_id, id, can_originate_late_fee) (MySQL 8.4
  needs a unique key for a foreign key). The markers depend on concept and
  source_charge_id only (MySQL forbids AUTO_INCREMENT columns in generated
  columns).
- created_by_user_id: the administrator who created the obligation (taken from
  the session, never from the client), FK to users RESTRICT.
- description: required for PENALTY and OTHER (replaces the HU-004 rule for
  OTHER, which it includes), optional for LATE_FEE, not allowed for MONTHLY_FEE.

Indexes:
- uq_charges_unit_id_billing_period: idempotent monthly generation.
- uq_charges_source_charge_id: one late fee per origin.
- uq_charges_unit_id_id_can_originate_late_fee: parent key of the origin FK.
- ix_charges_unit_id_source_charge_id: child index of the origin FK.
- ix_charges_created_by_user_id: FK to users (InnoDB needs one).

Existing rows cannot get an author, so the upgrade aborts if charges has rows
(there are none: charges are only created from this extension on). The
downgrade aborts too, so no obligation data is ever dropped. Each ALTER TABLE is
atomic in MySQL 8; the origin foreign key needs its own statement, and if it
fails the first one is undone. Written by hand; the tests verify the real
structure in information_schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f3c8fc28babe'
down_revision: Union[str, Sequence[str], None] = '00d8bbdd687b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE = """
ALTER TABLE charges
    ADD COLUMN billing_period DATE NULL AFTER due_date,
    ADD COLUMN source_charge_id BIGINT NULL AFTER billing_period,
    ADD COLUMN created_by_user_id BIGINT NOT NULL AFTER source_charge_id,
    ADD COLUMN can_originate_late_fee TINYINT
        GENERATED ALWAYS AS (CASE WHEN concept <> 'LATE_FEE' THEN 1 ELSE 0 END) STORED NOT NULL AFTER created_at,
    ADD COLUMN late_fee_source_link TINYINT
        GENERATED ALWAYS AS (CASE WHEN source_charge_id IS NOT NULL THEN 1 END) STORED NULL
        AFTER can_originate_late_fee,
    ADD CONSTRAINT uq_charges_unit_id_billing_period UNIQUE (unit_id, billing_period),
    ADD CONSTRAINT uq_charges_source_charge_id UNIQUE (source_charge_id),
    ADD CONSTRAINT uq_charges_unit_id_id_can_originate_late_fee UNIQUE (unit_id, id, can_originate_late_fee),
    ADD INDEX ix_charges_unit_id_source_charge_id (unit_id, source_charge_id, late_fee_source_link),
    ADD INDEX ix_charges_created_by_user_id (created_by_user_id),
    ADD CONSTRAINT fk_charges_created_by_user_id_users
        FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
    DROP CHECK ck_charges_other_has_description,
    ADD CONSTRAINT ck_charges_description_required CHECK (concept NOT IN ('PENALTY', 'OTHER') OR description IS NOT NULL),
    ADD CONSTRAINT ck_charges_monthly_fee_without_description CHECK (concept <> 'MONTHLY_FEE' OR description IS NULL),
    ADD CONSTRAINT ck_charges_period_only_for_monthly_fee
        CHECK ((concept = 'MONTHLY_FEE') = (billing_period IS NOT NULL)),
    ADD CONSTRAINT ck_charges_period_first_day CHECK (billing_period IS NULL OR DAYOFMONTH(billing_period) = 1),
    ADD CONSTRAINT ck_charges_source_only_for_late_fee CHECK (source_charge_id IS NULL OR concept = 'LATE_FEE')
"""

# MySQL cannot add this foreign key in the ALTER that creates its generated
# columns and parent key (error 1215), so it is a second statement.
ADD_SOURCE_FK = """
ALTER TABLE charges
    ADD CONSTRAINT fk_charges_source_charge_id_charges
        FOREIGN KEY (unit_id, source_charge_id, late_fee_source_link)
        REFERENCES charges (unit_id, id, can_originate_late_fee) ON DELETE RESTRICT
"""

DOWNGRADE_WITHOUT_SOURCE_FK = """
ALTER TABLE charges
    DROP FOREIGN KEY fk_charges_created_by_user_id_users,
    DROP CHECK ck_charges_description_required,
    DROP CHECK ck_charges_monthly_fee_without_description,
    DROP CHECK ck_charges_period_only_for_monthly_fee,
    DROP CHECK ck_charges_period_first_day,
    DROP CHECK ck_charges_source_only_for_late_fee,
    DROP INDEX ix_charges_unit_id_source_charge_id,
    DROP INDEX ix_charges_created_by_user_id,
    DROP INDEX uq_charges_unit_id_id_can_originate_late_fee,
    DROP INDEX uq_charges_source_charge_id,
    DROP INDEX uq_charges_unit_id_billing_period,
    DROP COLUMN late_fee_source_link,
    DROP COLUMN can_originate_late_fee,
    DROP COLUMN created_by_user_id,
    DROP COLUMN source_charge_id,
    DROP COLUMN billing_period,
    ADD CONSTRAINT ck_charges_other_has_description CHECK (concept <> 'OTHER' OR description IS NOT NULL)
"""

NEW_COLUMNS = ("billing_period", "source_charge_id", "created_by_user_id", "can_originate_late_fee", "late_fee_source_link")


def _columns(bind: sa.Connection) -> set[str]:
    rows = bind.execute(
        sa.text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'charges'"
        )
    ).scalars()
    return set(rows)


def _charges(bind: sa.Connection) -> int:
    return bind.execute(sa.text("SELECT COUNT(*) FROM charges")).scalar_one()


def upgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind) & set(NEW_COLUMNS)
    if present:
        raise RuntimeError(f"Columns already exist in charges: {sorted(present)}. Check them manually. Nothing was changed.")
    # Validate first: an existing charge has no author and maybe no period.
    rows = _charges(bind)
    if rows:
        raise RuntimeError(f"Upgrade aborted: {rows} rows in charges cannot get an author. Nothing was changed.")
    op.execute(UPGRADE)
    try:
        op.execute(ADD_SOURCE_FK)
    except Exception:
        op.execute(DOWNGRADE_WITHOUT_SOURCE_FK)  # the table is empty: undo the first statement
        raise


def downgrade() -> None:
    bind = op.get_bind()
    missing = set(NEW_COLUMNS) - _columns(bind)
    if missing:
        raise RuntimeError(f"Downgrade aborted: columns missing in charges: {sorted(missing)}. Nothing was changed.")
    # Validate first: obligation data (period, origin, author) is never dropped.
    rows = _charges(bind)
    if rows:
        raise RuntimeError(f"Downgrade aborted: {rows} rows in charges would lose data. Nothing was changed.")
    op.execute("ALTER TABLE charges DROP FOREIGN KEY fk_charges_source_charge_id_charges")
    op.execute(DOWNGRADE_WITHOUT_SOURCE_FK)
