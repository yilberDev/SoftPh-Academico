"""hu016 password reset tokens

Revision ID: b1b38cfb7fe2
Revises: 4cfaeb7a0be8
Create Date: 2026-10-02 13:49:00

HU-016: password reset tokens of residents.

- Only SHA-256(token) is stored (UNIQUE token_hash). A token is pending while
  used_at and revoked_at are NULL, and valid until expires_at (1 hour).
- pending_user_marker is a VIRTUAL generated column (user_id while pending,
  NULL afterwards); its UNIQUE index allows one pending reset per user.
- ix_password_reset_tokens_user_id_created_at serves the foreign key and the
  rate limit (requests of a user within a window).

Written by hand (Alembic does not compare CHECK constraints and only partially
handles generated columns); the tests verify the real structure in
information_schema. No existing table or row is touched.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b1b38cfb7fe2'
down_revision: Union[str, Sequence[str], None] = '4cfaeb7a0be8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "password_reset_tokens"

CREATE = """
CREATE TABLE password_reset_tokens (
    id BIGINT NOT NULL AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    token_hash CHAR(64) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    expires_at DATETIME(6) NOT NULL,
    used_at DATETIME(6) NULL,
    revoked_at DATETIME(6) NULL,
    pending_user_marker BIGINT
        GENERATED ALWAYS AS (CASE WHEN used_at IS NULL AND revoked_at IS NULL THEN user_id END) VIRTUAL,
    PRIMARY KEY (id),
    CONSTRAINT uq_password_reset_tokens_token_hash UNIQUE (token_hash),
    CONSTRAINT uq_password_reset_tokens_pending_user UNIQUE (pending_user_marker),
    INDEX ix_password_reset_tokens_user_id_created_at (user_id, created_at),
    CONSTRAINT fk_password_reset_tokens_user_id_users
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_password_reset_tokens_expires_after_creation CHECK (expires_at > created_at),
    CONSTRAINT ck_password_reset_tokens_used_or_revoked CHECK (used_at IS NULL OR revoked_at IS NULL)
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
    # Validate first: reset history (used/revoked tokens) is never dropped with data.
    if not _table_exists(bind):
        raise RuntimeError(f"Downgrade aborted: table {TABLE} is missing. Nothing was changed.")
    rows = bind.execute(sa.text(f"SELECT COUNT(*) FROM {TABLE}")).scalar_one()
    if rows:
        raise RuntimeError(f"Downgrade aborted: {rows} rows would be lost. Nothing was changed.")
    op.execute(f"DROP TABLE {TABLE}")
