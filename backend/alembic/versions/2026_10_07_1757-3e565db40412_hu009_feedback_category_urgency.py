"""hu009 feedback category and urgency

Revision ID: 3e565db40412
Revises: 4385fd452231
Create Date: 2026-10-07 17:57:18

HU-009 (criteria 2.2 and 2.3): the resident chooses a category (queja,
sugerencia, solicitud u otros) and an urgency (alta, media o baja) for each
feedback. Two columns of feedback:

- category VARCHAR(20) NULL: COMPLAINT | SUGGESTION | REQUEST | OTHER.
- urgency VARCHAR(20) NULL: HIGH | MEDIUM | LOW.

Both use the binary collation utf8mb4_0900_bin (as users.email): with the
default case-insensitive collation, the CHECK would also accept 'complaint'.

Both are NULL only for the feedback stored before HU-009 (HU-005): nothing is
invented for them (no DEFAULT, no backfill). Every feedback created by HU-009
has both; create_feedback requires them, and the database only accepts both or
neither (ck_feedback_category_urgency_together). No index: neither column is
filtered or sorted yet. The other columns and their rules are not touched.

Written by hand (Alembic does not compare CHECK constraints); the tests verify
the real structure in information_schema.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '3e565db40412'
down_revision: Union[str, Sequence[str], None] = '4385fd452231'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_COLUMNS = ("category", "urgency")

UPGRADE = """
ALTER TABLE feedback
    ADD COLUMN category VARCHAR(20) COLLATE utf8mb4_0900_bin NULL AFTER message,
    ADD COLUMN urgency VARCHAR(20) COLLATE utf8mb4_0900_bin NULL AFTER category,
    ADD CONSTRAINT ck_feedback_category_valid
        CHECK (category IS NULL OR category IN ('COMPLAINT', 'SUGGESTION', 'REQUEST', 'OTHER')),
    ADD CONSTRAINT ck_feedback_urgency_valid
        CHECK (urgency IS NULL OR urgency IN ('HIGH', 'MEDIUM', 'LOW')),
    ADD CONSTRAINT ck_feedback_category_urgency_together
        CHECK ((category IS NULL) = (urgency IS NULL))
"""

DOWNGRADE = """
ALTER TABLE feedback
    DROP CHECK ck_feedback_category_valid,
    DROP CHECK ck_feedback_urgency_valid,
    DROP CHECK ck_feedback_category_urgency_together,
    DROP COLUMN category,
    DROP COLUMN urgency
"""


def _columns(bind: sa.Connection) -> set[str]:
    rows = bind.execute(
        sa.text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'feedback'"
        )
    ).scalars()
    return set(rows)


def upgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind) & set(NEW_COLUMNS)
    if present:
        raise RuntimeError(f"Columns already exist in feedback: {sorted(present)}. Check them manually. Nothing was changed.")
    op.execute(UPGRADE)


def downgrade() -> None:
    bind = op.get_bind()
    missing = set(NEW_COLUMNS) - _columns(bind)
    if missing:
        raise RuntimeError(f"Downgrade aborted: columns missing in feedback: {sorted(missing)}. Nothing was changed.")
    # The residents' choices are never dropped: only a table without them goes back.
    rows = bind.execute(
        sa.text("SELECT COUNT(*) FROM feedback WHERE category IS NOT NULL OR urgency IS NOT NULL")
    ).scalar_one()
    if rows:
        raise RuntimeError(f"Downgrade aborted: {rows} rows in feedback would lose data. Nothing was changed.")
    op.execute(DOWNGRADE)
