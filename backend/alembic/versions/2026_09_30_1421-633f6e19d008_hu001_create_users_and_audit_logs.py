"""create users and audit_logs tables (HU-001)

Revision ID: 633f6e19d008
Revises: 
Create Date: 2026-09-30 14:21:56.018649

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = '633f6e19d008'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """HU-001: user accounts (both roles) and the audit log."""
    op.create_table('users',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('full_name', sa.String(length=150), nullable=False),
    sa.Column('document_number', sa.String(length=20), nullable=True),
    sa.Column('phone', sa.String(length=20), nullable=True),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', mysql.DATETIME(fsp=6), server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('updated_at', mysql.DATETIME(fsp=6), server_default=sa.text('CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('deleted_at', mysql.DATETIME(fsp=6), nullable=True),
    sa.CheckConstraint("role IN ('ADMINISTRADOR', 'RESIDENTE')", name=op.f('ck_users_role_valid')),
    sa.CheckConstraint("status <> 'ACTIVE' OR password_hash IS NOT NULL", name=op.f('ck_users_active_has_password')),
    sa.CheckConstraint("status IN ('PENDING_ACTIVATION', 'ACTIVE', 'DISABLED')", name=op.f('ck_users_status_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('document_number', name=op.f('uq_users_document_number')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email'))
    )
    op.create_table('audit_logs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('actor_user_id', sa.BigInteger(), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('entity_type', sa.String(length=64), nullable=True),
    sa.Column('entity_id', sa.BigInteger(), nullable=True),
    sa.Column('context', sa.JSON(), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('created_at', mysql.DATETIME(fsp=6), server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_audit_logs_actor_user_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_logs'))
    )
    op.create_index(op.f('ix_audit_logs_actor_user_id'), 'audit_logs', ['actor_user_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_index('ix_audit_logs_entity', 'audit_logs', ['entity_type', 'entity_id'], unique=False)


def downgrade() -> None:
    # Dropping the tables removes their indexes. Dropping the actor_user_id
    # index first would fail in MySQL because the foreign key still uses it.
    op.drop_table('audit_logs')
    op.drop_table('users')
