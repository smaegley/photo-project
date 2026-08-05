"""Display-time rotation override for photos whose master cannot be rewritten.

B2-backed digital masters are read-only, but plenty of them are sideways: early
digicams (and some phones) wrote Orientation=1 regardless of how they were held, so
the pixels are rotated and the EXIF is *wrong*, not merely unapplied. `rotation`
(clockwise degrees: 0/90/180/270) is applied when derivatives are generated — the
master is never touched, nothing needs re-exporting from Lightroom, and clearing the
column restores the original view. Local masters (slides/scans) keep rotating their
pixels in place; this column stays 0 for them.

`photo.width`/`height` intentionally keep describing the MASTER's pixels, not the
displayed orientation — they're informational, and normalized regions make display
math independent of them.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.add_column(sa.Column("rotation", sa.Integer(), nullable=False,
                                      server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.drop_column("rotation")
