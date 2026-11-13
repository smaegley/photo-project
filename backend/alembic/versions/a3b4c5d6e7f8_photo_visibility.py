"""Photo visibility: published | pending | culled (SPEC §15.6, slice 1).

Until now "has a row" meant "the family can see it". Direct-to-prod ingest (§15) stages
new photos before anyone approves them, and the cull tool hides photos without deleting
anything — so visibility becomes a column, and every family-facing read filters on it
(`queries.visible()`). Existing rows are all `published`; nothing changes on deploy.

`culled_from` remembers what a culled photo was before, so Restore puts an unapproved
photo back in the inbox rather than publishing it by accident.

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-11-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a3b4c5d6e7f8"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.add_column(sa.Column("visibility", sa.String(), nullable=False,
                                      server_default="published"))
        batch_op.add_column(sa.Column("culled_from", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("culled_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("culled_by", sa.String(), nullable=True))
        batch_op.create_index("ix_photo_visibility", ["visibility"])


def downgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.drop_index("ix_photo_visibility")
        batch_op.drop_column("culled_by")
        batch_op.drop_column("culled_at")
        batch_op.drop_column("culled_from")
        batch_op.drop_column("visibility")
