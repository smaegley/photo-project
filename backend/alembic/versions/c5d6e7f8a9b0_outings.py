"""Outings + triage (SPEC §15.5/§15.6, slice 4).

`outing` groups ingested photos taken close together in time and place; `photo.triage`
records what the pipeline thought of each photo (selected | related | unselected, plus
`rescued` when an admin overrules it). `photo.taken_at` is the capture time to the
second (outings need more than the date); `photo.file_size` lets reconcile recognise a
master that was moved or renamed in the bucket; `photo.master_missing` marks one
whose master has gone, so it is auto-culled once and not again after a restore. Additive; only ingested rows get values.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-11-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "outing",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("start_at", sa.DateTime(), nullable=True),
        sa.Column("end_at", sa.DateTime(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("folder", sa.String(), nullable=True),
        sa.Column("event_name", sa.String(), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.func.current_timestamp()),
    )
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.add_column(sa.Column("taken_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("triage", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("outing_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("file_size", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("master_missing", sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
        batch_op.create_index("ix_photo_outing", ["outing_id"])
    # Slice 3 kept the EXIF capture time only inside date_raw ("2026-07-30T10:52:46").
    op.execute(
        "UPDATE photo SET taken_at = replace(date_raw, 'T', ' ') || '.000000' "
        "WHERE ingest_run_id IS NOT NULL AND length(date_raw) = 19 "
        "AND date_raw LIKE '____-__-__T__:__:__'")


def downgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.drop_index("ix_photo_outing")
        batch_op.drop_column("master_missing")
        batch_op.drop_column("file_size")
        batch_op.drop_column("outing_id")
        batch_op.drop_column("triage")
        batch_op.drop_column("taken_at")
    op.drop_table("outing")
