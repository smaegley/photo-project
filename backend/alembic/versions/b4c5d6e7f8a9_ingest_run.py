"""Ingest runs + per-photo ingest provenance (SPEC §15.4/§15.6, slice 3).

`ingest_run` is the detailed record of each nightly B2 ingest (counts, skips, errors).
`photo.ingest_run_id` ties a photo to the run that brought it in; `photo.keywords_raw`
keeps any keywords embedded in the file (XMP dc:subject / IPTC) so the inbox can offer
them as event names later. Additive; existing rows are untouched.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-11-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ingest_run",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("state", sa.String(), nullable=False, server_default="running"),
        sa.Column("listed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail", sa.Text(), nullable=True),
    )
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ingest_run_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("keywords_raw", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.drop_column("keywords_raw")
        batch_op.drop_column("ingest_run_id")
    op.drop_table("ingest_run")
