"""Add user.theme + usage_event table (UI dark mode + usage tracking).

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("theme", sa.String(), nullable=False,
                                      server_default="system"))
    op.create_table(
        "usage_event",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_email", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_usage_event_created_at", "usage_event", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_event_created_at", table_name="usage_event")
    op.drop_table("usage_event")
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("theme")
