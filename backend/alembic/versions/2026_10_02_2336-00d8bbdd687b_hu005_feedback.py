"""hu005 feedback

Revision ID: 00d8bbdd687b
Revises: 6afae8f829c4
Create Date: 2026-10-02 23:36:00

HU-005: feedback of residents, reviewed and managed by the administrator.

- The original message (subject, message) is never updated by the
  application; the subject is optional (HU-009).
- status PENDING | RESOLVED only. RESOLVED always has resolved_at and a
  resolution_note (internal), separate from admin_response (sent to the resident).
- version: optimistic locking of the response and the resolution.
- The history of actions is audit_logs (entity_type = 'feedback'); no
  separate history table.

Indexes (only those used by real queries):
- ix_feedback_resident_id: foreign key; a resident's feedback (HU-009).
- ix_feedback_status_created_at: filter by status, newest first.
- ix_feedback_created_at: default order of the list (newest first).

Written by hand (Alembic does not compare CHECK constraints); the tests verify
the real structure in information_schema. No existing table or row is touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '00d8bbdd687b'
down_revision: Union[str, Sequence[str], None] = '6afae8f829c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "feedback"

CREATE = """
CREATE TABLE feedback (
    id BIGINT NOT NULL AUTO_INCREMENT,
    resident_id BIGINT NOT NULL,
    subject VARCHAR(150) NULL,
    message VARCHAR(2000) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    admin_response VARCHAR(2000) NULL,
    resolution_note VARCHAR(2000) NULL,
    version INT UNSIGNED NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    resolved_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    INDEX ix_feedback_resident_id (resident_id),
    INDEX ix_feedback_status_created_at (status, created_at),
    INDEX ix_feedback_created_at (created_at),
    CONSTRAINT fk_feedback_resident_id_users FOREIGN KEY (resident_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_feedback_status_valid CHECK (status IN ('PENDING', 'RESOLVED')),
    CONSTRAINT ck_feedback_version_positive CHECK (version >= 1),
    CONSTRAINT ck_feedback_message_not_blank CHECK (CHAR_LENGTH(TRIM(message)) > 0),
    CONSTRAINT ck_feedback_subject_not_blank CHECK (subject IS NULL OR CHAR_LENGTH(TRIM(subject)) > 0),
    CONSTRAINT ck_feedback_resolved_has_timestamp CHECK ((status = 'RESOLVED') = (resolved_at IS NOT NULL)),
    CONSTRAINT ck_feedback_resolved_has_note CHECK (status <> 'RESOLVED' OR resolution_note IS NOT NULL),
    CONSTRAINT ck_feedback_resolved_after_creation CHECK (resolved_at IS NULL OR resolved_at >= created_at)
) ENGINE=InnoDB
"""


def _table_exists(bind: sa.Connection) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :name"
            ),
            {"name": TABLE},
        ).scalar_one()
    )


def upgrade() -> None:
    if _table_exists(op.get_bind()):
        raise RuntimeError(f"Table {TABLE} already exists. Check it manually. Nothing was changed.")
    op.execute(CREATE)


def downgrade() -> None:
    bind = op.get_bind()
    # Validate first: feedback of residents is never dropped with data.
    if not _table_exists(bind):
        raise RuntimeError(f"Downgrade aborted: table {TABLE} is missing. Nothing was changed.")
    rows = bind.execute(sa.text(f"SELECT COUNT(*) FROM {TABLE}")).scalar_one()
    if rows:
        raise RuntimeError(f"Downgrade aborted: {rows} rows would be lost. Nothing was changed.")
    op.execute(f"DROP TABLE {TABLE}")
