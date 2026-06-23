"""Add inverse + undone to contribution for undo (SPEC §3.5).

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-06-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("contribution", schema=None) as batch_op:
        batch_op.add_column(sa.Column("inverse", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("undone", sa.Boolean(), nullable=True,
                                      server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("contribution", schema=None) as batch_op:
        batch_op.drop_column("undone")
        batch_op.drop_column("inverse")
