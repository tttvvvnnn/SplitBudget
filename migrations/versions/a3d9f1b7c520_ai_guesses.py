"""ai guesses

Подсказки категорий от ИИ: кэш ответов и выключатель на пространство.

Revision ID: a3d9f1b7c520
Revises: e2a6c8f0b154
Create Date: 2026-10-22 18:10:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3d9f1b7c520'
down_revision: Union[str, None] = 'e2a6c8f0b154'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('chats', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('ai_enabled', sa.Boolean(), nullable=False, server_default=sa.true())
        )
    op.create_table(
        'ai_guesses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('keyword', sa.String(length=255), nullable=False),
        sa.Column('tree_hash', sa.String(length=16), nullable=False),
        sa.Column('action', sa.String(length=16), nullable=False),
        sa.Column('category', sa.String(length=64), nullable=True),
        sa.Column('subcategory', sa.String(length=64), nullable=True),
        sa.Column('icon', sa.String(length=16), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chat_id', 'keyword', name='uq_ai_guess_chat_keyword'),
    )


def downgrade() -> None:
    op.drop_table('ai_guesses')
    with op.batch_alter_table('chats', schema=None) as batch_op:
        batch_op.drop_column('ai_enabled')
