"""Face matching tables: face, face_suggestion, face_cluster (SPEC §14.6).

Additive only — nothing existing is touched, and no behaviour changes until the
detection batch (slice 3) starts writing rows. Suggestions deliberately live apart from
`photo_person` so an unreviewed guess can never reach the gallery, and so abandoning the
ML layer stays a DROP TABLE rather than a data-cleaning exercise.

Portable-identity note (SPEC §14.8a): these tables carry `photo_id` for join speed, but
the dev->prod *export* format keys on `photo.source_file`, never these row ids — ids are
assigned per-database and would silently attach faces to the wrong photos.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "face_cluster",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("person_id", sa.String(), sa.ForeignKey("person.id", ondelete="SET NULL"), nullable=True),
        sa.Column("centroid", sa.LargeBinary(), nullable=True),
        sa.Column("n_faces", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("prominence", sa.Float(), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_face_cluster_status", "face_cluster", ["status"])

    op.create_table(
        "face",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("photo_id", sa.Integer(), sa.ForeignKey("photo.id", ondelete="CASCADE"), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("w", sa.Float(), nullable=False),
        sa.Column("h", sa.Float(), nullable=False),
        sa.Column("det_score", sa.Float(), nullable=True),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("detector_version", sa.String(), nullable=True),
        sa.Column("cluster_id", sa.Integer(), sa.ForeignKey("face_cluster.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_face_photo", "face", ["photo_id"])
    op.create_index("ix_face_cluster_id", "face", ["cluster_id"])

    op.create_table(
        "face_suggestion",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("face_id", sa.Integer(), sa.ForeignKey("face.id", ondelete="CASCADE"), nullable=False),
        sa.Column("person_id", sa.String(), sa.ForeignKey("person.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("face_id", "person_id", name="uq_face_suggestion"),
    )
    op.create_index("ix_face_suggestion_status", "face_suggestion", ["status", "score"])


def downgrade() -> None:
    op.drop_table("face_suggestion")
    op.drop_table("face")
    op.drop_table("face_cluster")
