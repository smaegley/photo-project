"""Indexes for the facet-query columns (photo date/place/magazine,
photo_person.person_id, photo_event.event_id, contribution.photo_id).

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
"""
from alembic import op

revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_photo_date_start", "photo", ["date_start"]),
    ("ix_photo_place_id", "photo", ["place_id"]),
    ("ix_photo_magazine_id", "photo", ["magazine_id"]),
    ("ix_photo_person_person_id", "photo_person", ["person_id"]),
    ("ix_photo_event_event_id", "photo_event", ["event_id"]),
    ("ix_contribution_photo_id", "contribution", ["photo_id"]),
]


def upgrade() -> None:
    for name, table, cols in INDEXES:
        op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, cols in INDEXES:
        op.drop_index(name, table_name=table)
