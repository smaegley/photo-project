"""Scan ingest: photo.batch/back_path/sort_date + person.is_family (SPEC §12.3).

Adds the columns the Scanned-Photos phase (origin=scan) needs and backfills so
existing slides behave identically under the new interleaved sort (SPEC §12.5):
- photo.batch      — FastFoto subject folder (scans only; slides stay null)
- photo.back_path  — library-relative path of the paired _b back scan
- photo.sort_date  — materialized sort key; slides backfilled to their magazine's
                     date_start, so a roll stays contiguous and in card order
- person.is_family — False = friend/other; existing people backfill to True

Composite index ix_photo_sort(sort_date, magazine_id, slide_in_mag) backs the sort.

Revision ID: a7b8c9d0e1f2
Revises: a6b7c8d9e0f1
Create Date: 2026-07-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.add_column(sa.Column("batch", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("back_path", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("sort_date", sa.Date(), nullable=True))
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.add_column(sa.Column("is_family", sa.Boolean(), nullable=False,
                                      server_default=sa.true()))
    op.create_index("ix_photo_sort", "photo",
                    ["sort_date", "magazine_id", "slide_in_mag"])
    # Backfill: every existing photo is a slide -> sort_date = its magazine's start.
    op.execute(
        "UPDATE photo SET sort_date = ("
        "  SELECT m.date_start FROM magazine m WHERE m.id = photo.magazine_id"
        ") WHERE sort_date IS NULL AND magazine_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_photo_sort", table_name="photo")
    with op.batch_alter_table("person", schema=None) as batch_op:
        batch_op.drop_column("is_family")
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.drop_column("sort_date")
        batch_op.drop_column("back_path")
        batch_op.drop_column("batch")
