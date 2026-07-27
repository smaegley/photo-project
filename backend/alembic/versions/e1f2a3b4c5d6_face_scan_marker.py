"""Record which detector version has scanned each photo (SPEC §14 slice 3).

Resumability needs "was this photo scanned", not "does this photo have faces". Roughly
40% of the library contains no face at all, and those produce no `face` rows — so keying
resume state off the `face` table silently re-scans every faceless photo on every run
(~10 wasted minutes per pass, growing with the library).

Nullable and additive; null simply means "not yet scanned by any detector".

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.add_column(sa.Column("faces_scanned_version", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("photo", schema=None) as batch_op:
        batch_op.drop_column("faces_scanned_version")
