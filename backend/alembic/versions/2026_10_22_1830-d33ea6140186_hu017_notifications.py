"""hu017 notifications

Revision ID: d33ea6140186
Revises: 41602195f69f
Create Date: 2026-10-22 18:30:00

HU-017 (CU-017): the notifications sent to a resident and the result of each delivery. Until
now each flow kept its own trace (payments.receipt_email_status, the FEEDBACK_NOTIFICATION_*
audit entries); CU-017 asks for the event to be identified, sent, its result recorded (6), its
error recorded (3a) and the event kept for follow-up (4a), and HU-017 for the resident to receive
them: one table, nothing else changes.

notifications:
- resident_id: the resident told (FK users, RESTRICT: history is never deleted by cascade).
- event_type: PAYMENT_REGISTERED | FEEDBACK_RESPONDED | FEEDBACK_RESOLVED.
- entity_type, entity_id: the record it is about ("payment" / "feedback"); no FK (two tables).
- event_key: UNIQUE, binary collation: one notification per event, also when retried.
- title, message: what the resident reads (never secrets, internal notes or other people's data).
- email_status: PENDING | SENT | FAILED | NO_CONTACT | NOT_REQUIRED; email_error (the error type) only
  when FAILED; sent_at present exactly when SENT. NOT_REQUIRED: an event without email (an approved
  demo payment, HU-010 never emails), shown in the resident's panel only. created_at: UTC, as every
  DATETIME(6) of the schema.
Index (resident_id, created_at): the resident's list, newest first; it also serves the FK.

Written by hand (Alembic does not compare CHECK constraints); the tests verify the real
structure in information_schema. No existing table or row is touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd33ea6140186'
down_revision: Union[str, Sequence[str], None] = '41602195f69f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "notifications"

CREATE = """
CREATE TABLE notifications (
    id BIGINT NOT NULL AUTO_INCREMENT,
    resident_id BIGINT NOT NULL,
    event_type VARCHAR(40) NOT NULL,
    entity_type VARCHAR(40) NOT NULL,
    entity_id BIGINT NOT NULL,
    event_key VARCHAR(120) COLLATE utf8mb4_0900_bin NOT NULL,
    title VARCHAR(150) NOT NULL,
    message VARCHAR(500) NOT NULL,
    email_status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    email_error VARCHAR(64) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    sent_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_notifications_event_key UNIQUE (event_key),
    INDEX ix_notifications_resident_id_created_at (resident_id, created_at),
    CONSTRAINT fk_notifications_resident_id_users
        FOREIGN KEY (resident_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_notifications_event_type_valid
        CHECK (event_type IN ('PAYMENT_REGISTERED', 'FEEDBACK_RESPONDED', 'FEEDBACK_RESOLVED')),
    CONSTRAINT ck_notifications_email_status_valid
        CHECK (email_status IN ('PENDING', 'SENT', 'FAILED', 'NO_CONTACT', 'NOT_REQUIRED')),
    CONSTRAINT ck_notifications_sent_has_timestamp CHECK ((email_status = 'SENT') = (sent_at IS NOT NULL)),
    CONSTRAINT ck_notifications_error_only_when_failed CHECK (email_status = 'FAILED' OR email_error IS NULL),
    CONSTRAINT ck_notifications_title_not_blank CHECK (CHAR_LENGTH(TRIM(title)) > 0)
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
    # Validate first: notifications already recorded (the residents' history) are never dropped.
    if not _exists(bind):
        raise RuntimeError(f"Downgrade aborted: table missing: {TABLE}. Nothing was changed.")
    rows = bind.execute(sa.text(f"SELECT COUNT(*) FROM {TABLE}")).scalar_one()
    if rows:
        raise RuntimeError(f"Downgrade aborted: {rows} rows in {TABLE} would be lost. Nothing was changed.")
    op.execute(f"DROP TABLE {TABLE}")
