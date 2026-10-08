"""category rules

Выученные соответствия «название траты → категория» (автоопределение категории).

Revision ID: 9c4d1e8a2b37
Revises: 7b3c9d2e4f10
Create Date: 2026-10-08 20:15:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c4d1e8a2b37'
down_revision: Union[str, None] = '7b3c9d2e4f10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'category_rules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('keyword', sa.String(length=255), nullable=False),
        sa.Column('category', sa.String(length=64), nullable=False),
        sa.Column('subcategory', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chat_id', 'keyword', name='uq_category_rule_chat_keyword'),
    )


def downgrade() -> None:
    op.drop_table('category_rules')
