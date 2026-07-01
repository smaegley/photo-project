"""Add origin + storage_path (+ file meta) to photo for non-slide ingest (SPEC §11.4).

Generalizes the photo row so it can hold digital/scanned photos, not just slides:
- origin: slide|scan|digital  (existing rows backfilled to 'slide')
- storage_path: path under the library root; lets the server locate the file by
  DB lookup instead of the hardcoded Mag<N>_Slide<NN> regex (SPEC §11.5).
  Backfilled for slides to slides/Mag<N>/<source_file>.
- original_filename, width, height, imported_at: provenance for imported photos.

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.add_column(sa.Column("origin", sa.String(), nullable=False,
                                      server_default="slide"))
        batch_op.add_column(sa.Column("storage_path", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("original_filename", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("width", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("height", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("imported_at", sa.DateTime(), nullable=True))
    # Backfill storage_path for existing slides (all current rows are slides with a
    # magazine_id). The library layout is slides/Mag<N>/<source_file>.
    op.execute(
        "UPDATE photo SET storage_path = 'slides/Mag' || magazine_id || '/' || source_file "
        "WHERE origin = 'slide' AND magazine_id IS NOT NULL AND storage_path IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.drop_column("imported_at")
        batch_op.drop_column("height")
        batch_op.drop_column("width")
        batch_op.drop_column("original_filename")
        batch_op.drop_column("storage_path")
        batch_op.drop_column("origin")
