"""Add Lightroom face-region box to photo_person (face thumbnails, SPEC §4.2).

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("photo_person", schema=None) as batch_op:
        batch_op.add_column(sa.Column("region_x", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("region_y", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("region_w", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("region_h", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("photo_person", schema=None) as batch_op:
        batch_op.drop_column("region_h")
        batch_op.drop_column("region_w")
        batch_op.drop_column("region_y")
        batch_op.drop_column("region_x")
