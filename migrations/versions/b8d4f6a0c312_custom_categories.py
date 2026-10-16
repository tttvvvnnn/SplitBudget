"""custom categories

Свои категории и подкатегории пространства, скрытые базовые категории.

Revision ID: b8d4f6a0c312
Revises: a7c3e5f9b201
Create Date: 2026-10-16 17:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8d4f6a0c312'
down_revision: Union[str, None] = 'a7c3e5f9b201'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'custom_categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('parent', sa.String(length=64), nullable=True),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('icon', sa.String(length=16), nullable=False),
        sa.Column('is_builtin', sa.Boolean(), nullable=False),
        sa.Column('is_hidden', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('custom_categories')
