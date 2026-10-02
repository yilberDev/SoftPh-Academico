"""hu003 resident invitations

Revision ID: 4cfaeb7a0be8
Revises: 522bf629af33
Create Date: 2026-10-01 21:17:00

HU-003: resident invitations, plus the residence table they lead to.

- resident_invitations: one invitation of a resident (user) to an apartment
  (unit). Only SHA-256(token) is stored (UNIQUE). status PENDING/USED/EXPIRED,
  delivery_status PENDING/SENT/FAILED, validity_days 1-30. The VIRTUAL
  generated markers pending_unit_marker / pending_user_marker are NULL unless
  the invitation is PENDING; their UNIQUE indexes allow at most one pending
  invitation per apartment and per user.
- resident_units: residence created by HU-019 when the account is activated
  (HU-003 never writes it; it only reads it for availability). UNIQUE
  invitation_id, and one active residence per apartment and per resident.

Written by hand (Alembic does not compare CHECK constraints and only partially
handles generated columns); the tests verify the real structure in
information_schema. Foreign keys use ON DELETE RESTRICT and have their own index.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '4cfaeb7a0be8'
down_revision: Union[str, Sequence[str], None] = '522bf629af33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("resident_invitations", "resident_units")

CREATE_RESIDENT_INVITATIONS = """
CREATE TABLE resident_invitations (
    id BIGINT NOT NULL AUTO_INCREMENT,
    token_hash CHAR(64) NOT NULL,
    user_id BIGINT NOT NULL,
    unit_id BIGINT NOT NULL,
    invited_by_user_id BIGINT NOT NULL,
    validity_days TINYINT UNSIGNED NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    delivery_status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    send_attempts SMALLINT UNSIGNED NOT NULL DEFAULT '0',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    expires_at DATETIME(6) NOT NULL,
    used_at DATETIME(6) NULL,
    last_sent_at DATETIME(6) NULL,
    pending_unit_marker BIGINT GENERATED ALWAYS AS (CASE WHEN status = 'PENDING' THEN unit_id END) VIRTUAL,
    pending_user_marker BIGINT GENERATED ALWAYS AS (CASE WHEN status = 'PENDING' THEN user_id END) VIRTUAL,
    PRIMARY KEY (id),
    CONSTRAINT uq_resident_invitations_token_hash UNIQUE (token_hash),
    CONSTRAINT uq_resident_invitations_pending_unit UNIQUE (pending_unit_marker),
    CONSTRAINT uq_resident_invitations_pending_user UNIQUE (pending_user_marker),
    INDEX ix_resident_invitations_user_id (user_id),
    INDEX ix_resident_invitations_unit_id (unit_id),
    INDEX ix_resident_invitations_invited_by_user_id (invited_by_user_id),
    CONSTRAINT fk_resident_invitations_user_id_users
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_resident_invitations_unit_id_units
        FOREIGN KEY (unit_id) REFERENCES units (id) ON DELETE RESTRICT,
    CONSTRAINT fk_resident_invitations_invited_by_user_id_users
        FOREIGN KEY (invited_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_resident_invitations_validity_days_range CHECK (validity_days BETWEEN 1 AND 30),
    CONSTRAINT ck_resident_invitations_status_valid CHECK (status IN ('PENDING', 'USED', 'EXPIRED')),
    CONSTRAINT ck_resident_invitations_delivery_status_valid
        CHECK (delivery_status IN ('PENDING', 'SENT', 'FAILED')),
    CONSTRAINT ck_resident_invitations_send_attempts_non_negative CHECK (send_attempts >= 0),
    CONSTRAINT ck_resident_invitations_expires_after_creation CHECK (expires_at > created_at),
    CONSTRAINT ck_resident_invitations_used_at_matches_status CHECK ((status = 'USED') = (used_at IS NOT NULL)),
    CONSTRAINT ck_resident_invitations_sent_has_timestamp
        CHECK (delivery_status <> 'SENT' OR last_sent_at IS NOT NULL)
) ENGINE=InnoDB
"""

CREATE_RESIDENT_UNITS = """
CREATE TABLE resident_units (
    id BIGINT NOT NULL AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    unit_id BIGINT NOT NULL,
    invitation_id BIGINT NOT NULL,
    started_at DATETIME(6) NOT NULL,
    ended_at DATETIME(6) NULL,
    active_unit_marker BIGINT GENERATED ALWAYS AS (CASE WHEN ended_at IS NULL THEN unit_id END) VIRTUAL,
    active_user_marker BIGINT GENERATED ALWAYS AS (CASE WHEN ended_at IS NULL THEN user_id END) VIRTUAL,
    PRIMARY KEY (id),
    CONSTRAINT uq_resident_units_invitation_id UNIQUE (invitation_id),
    CONSTRAINT uq_resident_units_active_unit UNIQUE (active_unit_marker),
    CONSTRAINT uq_resident_units_active_user UNIQUE (active_user_marker),
    INDEX ix_resident_units_user_id (user_id),
    INDEX ix_resident_units_unit_id (unit_id),
    CONSTRAINT fk_resident_units_user_id_users
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_resident_units_unit_id_units
        FOREIGN KEY (unit_id) REFERENCES units (id) ON DELETE RESTRICT,
    CONSTRAINT fk_resident_units_invitation_id_resident_invitations
        FOREIGN KEY (invitation_id) REFERENCES resident_invitations (id) ON DELETE RESTRICT,
    CONSTRAINT ck_resident_units_ended_after_start CHECK (ended_at IS NULL OR ended_at >= started_at)
) ENGINE=InnoDB
"""


def _existing_tables(bind: sa.Connection) -> set[str]:
    return set(
        bind.execute(
            sa.text(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN :names"
            ).bindparams(sa.bindparam("names", expanding=True)),
            {"names": list(TABLES)},
        ).scalars()
    )


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Validate first: two CREATE TABLE statements are not atomic together.
    existing = _existing_tables(bind)
    if existing:
        raise RuntimeError(
            f"Tables {sorted(existing)} already exist. Remove them manually after checking their "
            "content, then upgrade again. Nothing was changed."
        )

    # 2. The residence references the invitation.
    op.execute(CREATE_RESIDENT_INVITATIONS)
    op.execute(CREATE_RESIDENT_UNITS)


def downgrade() -> None:
    bind = op.get_bind()

    # 1. Validate everything before changing anything: invitations and
    #    residences are history and are never dropped with data.
    missing = set(TABLES) - _existing_tables(bind)
    if missing:
        raise RuntimeError(f"Downgrade aborted: tables {sorted(missing)} are missing. Nothing was changed.")
    rows = {table: bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one() for table in TABLES}
    non_empty = {table: count for table, count in rows.items() if count}
    if non_empty:
        raise RuntimeError(f"Downgrade aborted: {non_empty} rows would be lost. Nothing was changed.")

    # 2. One statement, atomic in InnoDB (MySQL 8.0 atomic DDL).
    op.execute("DROP TABLE resident_units, resident_invitations")
