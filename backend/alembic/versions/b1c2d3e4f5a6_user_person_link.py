"""Link user account to its tree person (SPEC §3.3 per-viewer People rooting).

Adds user.person_id FK -> person.id, nullable. When set, the People tree roots
from that person ("Self" = them); when null the viewer sees the canonical
Steve-rooted tree with no "Self".

Revision ID: b1c2d3e4f5a6
Revises: 4309d43a4b0a
Create Date: 2026-06-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "4309d43a4b0a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.add_column(sa.Column("person_id", sa.String(), nullable=True))
        batch_op.create_foreign_key(
            "fk_user_person_id", "person", ["person_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_constraint("fk_user_person_id", type_="foreignkey")
        batch_op.drop_column("person_id")
