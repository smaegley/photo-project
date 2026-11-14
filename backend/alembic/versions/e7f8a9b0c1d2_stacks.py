"""Near-duplicate stacks (SPEC §15.4 step 5, slice 6).

`photo.phash` (64-bit difference hash, hex), `photo.sharpness` (variance of the
Laplacian — which of a stack to suggest keeping) and `photo.stack_id`. Additive.

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-11-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, None] = "d6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.add_column(sa.Column("phash", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("sharpness", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("stack_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.drop_column("stack_id")
        batch_op.drop_column("sharpness")
        batch_op.drop_column("phash")
