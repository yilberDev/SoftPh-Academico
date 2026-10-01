"""hu002 sessions and login throttles

Revision ID: 5cde3a1e780c
Revises: 925c4947c7a3
Create Date: 2026-09-30 15:09:34.256401

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '5cde3a1e780c'
down_revision: Union[str, Sequence[str], None] = '925c4947c7a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """HU-002: server-side sessions and the failed login counter.

    Only creates new tables; existing tables and data are not touched.
    """
    op.create_table('login_throttles',
    sa.Column('identifier_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('failed_attempts', sa.Integer(), nullable=False),
    sa.Column('window_started_at', mysql.DATETIME(fsp=6), nullable=False),
    sa.Column('locked_until', mysql.DATETIME(fsp=6), nullable=True),
    sa.Column('updated_at', mysql.DATETIME(fsp=6), server_default=sa.text('CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.CheckConstraint('failed_attempts >= 0', name=op.f('ck_login_throttles_failed_attempts_non_negative')),
    sa.PrimaryKeyConstraint('identifier_hash', name=op.f('pk_login_throttles'))
    )
    op.create_table('sessions',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('token_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('created_at', mysql.DATETIME(fsp=6), server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('last_seen_at', mysql.DATETIME(fsp=6), nullable=False),
    sa.Column('expires_at', mysql.DATETIME(fsp=6), nullable=False),
    sa.Column('revoked_at', mysql.DATETIME(fsp=6), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_sessions_token_hash'))
    )
    op.create_index(op.f('ix_sessions_expires_at'), 'sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)


def downgrade() -> None:
    # Dropping the tables removes their indexes. Dropping ix_sessions_user_id
    # first would fail in MySQL because the foreign key still uses it.
    op.drop_table('sessions')
    op.drop_table('login_throttles')
