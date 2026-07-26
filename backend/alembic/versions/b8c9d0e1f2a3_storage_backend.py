"""Storage backend + file_version for B2-backed digital masters (SPEC §13.3/§13.4).

Adds:
- photo.storage_backend — 'local' (default) | 'b2'. Existing rows (slides, scans, any
  local digital) are 'local'; digital masters served from Backblaze B2 are 'b2'.
- photo.file_version    — a cheap change-token stamped into the ?v= cache-buster and
  the derivative cache key, so the image server never has to stat()/HEAD the master
  per request. Left null on existing rows; self-heals (falls back to a one-time stat)
  and gets recorded on the next prewarm.

Additive and non-destructive; safe on the live prod DB.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.add_column(sa.Column("storage_backend", sa.String(), nullable=False,
                                      server_default="local"))
        batch_op.add_column(sa.Column("file_version", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.drop_column("file_version")
        batch_op.drop_column("storage_backend")
