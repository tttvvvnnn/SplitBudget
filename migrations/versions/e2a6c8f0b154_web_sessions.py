"""web sessions

Вход в веб-версию через бота: запросы на вход и сессии браузера.

Revision ID: e2a6c8f0b154
Revises: c9e5a7b1d423
Create Date: 2026-10-22 17:10:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2a6c8f0b154'
down_revision: Union[str, None] = 'c9e5a7b1d423'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'web_logins',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token', sa.String(length=64), nullable=False),
        sa.Column('secret_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('tg_user_id', sa.BigInteger(), nullable=True),
        sa.Column('user_json', sa.String(length=1024), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )
    op.create_table(
        'web_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('tg_user_id', sa.BigInteger(), nullable=False),
        sa.Column('user_json', sa.String(length=1024), nullable=False),
        sa.Column('user_agent', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index('ix_web_sessions_tg_user_id', 'web_sessions', ['tg_user_id'])


def downgrade() -> None:
    op.drop_index('ix_web_sessions_tg_user_id', table_name='web_sessions')
    op.drop_table('web_sessions')
    op.drop_table('web_logins')
