"""Keep a photo's own GPS coordinates (SPEC §13.9 / §3.4 gazetteer workflow).

Until now `resolve_place()` consumed a photo's GPS to find a `place_id` and then threw
the coordinates away, so an unresolved point existed only in the importer's review CSV.
That made "show me the photos whose location I haven't named yet" impossible to answer
from the DB — which is exactly what the admin unresolved-locations map needs, and it has
to work on prod where no review CSV lives.

Additive and nullable: existing rows stay null and are backfilled by re-running the
importer (or the one-off backfill for the already-imported digital set).

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.add_column(sa.Column("lat", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("lon", sa.Float(), nullable=True))
    # Partial-ish index for the admin "unresolved locations" query: photos that have
    # coordinates but no named place yet. SQLite will use it for the WHERE lat IS NOT
    # NULL scan, which is the only access pattern.
    op.create_index("ix_photo_latlon", "photo", ["lat", "lon"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_photo_latlon", table_name="photo")
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.drop_column("lon")
        batch_op.drop_column("lat")
