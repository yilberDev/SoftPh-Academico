"""hu010 demo gateway payments

Revision ID: f697625db563
Revises: 82e81b7ab23d
Create Date: 2026-10-09 15:43:46

HU-010: an approved demo transaction becomes a payment. Two existing CHECKs of
payments are replaced (same names, stricter for what already exists):

- ck_payments_method_valid: also DEMO_GATEWAY (the internal demo gateway; no
  card or bank is involved, so it is never labelled CARD).
- ck_payments_source_valid: MANUAL payments keep their three methods
  (CASH, CARD, TRANSFER); a DEMO_GATEWAY payment always has the DEMO_GATEWAY
  method and is registered by its own resident (registered_by_user_id =
  resident_id: never attributed to the administrator).

Every existing payment is MANUAL with one of the three methods, so it already
satisfies both rules (checked by MySQL when the CHECK is added). No column,
index, row or other constraint is touched.

The downgrade refuses while a DEMO_GATEWAY payment exists (it would no longer
fit the old CHECKs), then restores the original clauses.

Written by hand (Alembic does not compare CHECK constraints); the tests verify
the real clauses in information_schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f697625db563'
down_revision: Union[str, Sequence[str], None] = '82e81b7ab23d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE = """
ALTER TABLE payments
    DROP CHECK ck_payments_method_valid,
    DROP CHECK ck_payments_source_valid,
    ADD CONSTRAINT ck_payments_method_valid CHECK (method IN ('CASH', 'CARD', 'TRANSFER', 'DEMO_GATEWAY')),
    ADD CONSTRAINT ck_payments_source_valid CHECK (
        (source = 'MANUAL' AND method IN ('CASH', 'CARD', 'TRANSFER'))
        OR (source = 'DEMO_GATEWAY' AND method = 'DEMO_GATEWAY' AND registered_by_user_id = resident_id)
    )
"""

DOWNGRADE = """
ALTER TABLE payments
    DROP CHECK ck_payments_method_valid,
    DROP CHECK ck_payments_source_valid,
    ADD CONSTRAINT ck_payments_method_valid CHECK (method IN ('CASH', 'CARD', 'TRANSFER')),
    ADD CONSTRAINT ck_payments_source_valid CHECK (source IN ('MANUAL'))
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT COUNT(*) FROM payments WHERE source <> 'MANUAL' OR method = 'DEMO_GATEWAY'")
    ).scalar_one()
    if rows:
        raise RuntimeError(f"Downgrade aborted: {rows} demo gateway payments would not fit. Nothing was changed.")
    op.execute(DOWNGRADE)
