"""subscription hints

Подписки, найденные по истории трат: что бот уже предлагал и от чего отказались.

Revision ID: d5a9b1c3e847
Revises: c4f8a0e2b736
Create Date: 2026-10-09 15:30:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5a9b1c3e847'
down_revision: Union[str, None] = 'c4f8a0e2b736'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'subscription_hints',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('keyword', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chat_id', 'keyword', name='uq_subscription_hint_chat_keyword'),
    )


def downgrade() -> None:
    op.drop_table('subscription_hints')
