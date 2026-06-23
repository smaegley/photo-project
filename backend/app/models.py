"""SQLAlchemy models — the Maegley Photo Album data model (SPEC §3.2).

Backbone: a flat, date-ordered photo stream with rich tags. People are a derived
family tree; places a gazetteer; events suggest->confirm; magazines an optional
provenance overlay. Two-tier provenance on every tag (SPEC §3.5).
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Tag provenance (SPEC §3.5). manifest == authoritative/confirmed.
SOURCE_MANIFEST = "manifest"
SOURCE_AUTO = "auto-suggested"
SOURCE_HUMAN = "human-confirmed"


class Magazine(Base):
    """An Airequipt roll Wendel pre-organized. Optional overlay (SPEC §3.7)."""
    __tablename__ = "magazine"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # = magazine number
    title: Mapped[str | None] = mapped_column(String, nullable=True)        # editable; from mag_subject
    span_label: Mapped[str | None] = mapped_column(String, nullable=True)   # from mag_date_span
    date_start: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    date_end: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    slide_count: Mapped[int] = mapped_column(Integer, default=0)
    card_image_paths: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of card filenames

    photos: Mapped[list["Photo"]] = relationship(back_populates="magazine")


class Place(Base):
    """Gazetteer entry — one per real location (SPEC §3.4)."""
    __tablename__ = "place"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # slug, e.g. plc001
    canonical_name: Mapped[str] = mapped_column(String, nullable=False)
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    precision: Mapped[str] = mapped_column(String, default="unknown")  # exact|landmark|city|region|unknown
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)

    photos: Mapped[list["Photo"]] = relationship(back_populates="place")


class Person(Base):
    """Family individual. Relationship to Steve is DERIVED from parent/spouse keys (SPEC §3.3)."""
    __tablename__ = "person"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # slug, e.g. herb_beck
    canonical_name: Mapped[str] = mapped_column(String, nullable=False)
    father_id: Mapped[str | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    mother_id: Mapped[str | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    spouse_id: Mapped[str | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    representative_photo_id: Mapped[int | None] = mapped_column(
        ForeignKey("photo.id"), nullable=True
    )  # admin-chosen thumbnail (SPEC §9)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    aliases: Mapped[list["PersonAlias"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )


class PersonAlias(Base):
    """Maps note spellings / group forms -> canonical person (SPEC §3.3)."""
    __tablename__ = "person_alias"
    __table_args__ = (UniqueConstraint("person_id", "alias", name="uq_person_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String, nullable=False)

    person: Mapped["Person"] = relationship(back_populates="aliases")


class Event(Base):
    """Extensible event vocabulary (SPEC §3.6)."""
    __tablename__ = "event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class Photo(Base):
    """One row per image — the spine of the stream (SPEC §3.2)."""
    __tablename__ = "photo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_file: Mapped[str] = mapped_column(String, unique=True, nullable=False)  # Mag<N>_Slide<NN>.JPG
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)               # = card_caption
    original_subject: Mapped[str | None] = mapped_column(String, nullable=True)    # = mag_subject
    date_start: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    date_end: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    date_precision: Mapped[str | None] = mapped_column(String, nullable=True)      # day|month|season|year|approx
    date_raw: Mapped[str | None] = mapped_column(String, nullable=True)            # verbatim display label
    place_id: Mapped[str | None] = mapped_column(ForeignKey("place.id"), nullable=True)
    magazine_id: Mapped[int | None] = mapped_column(ForeignKey("magazine.id"), nullable=True)
    slide_in_mag: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation: Mapped[str | None] = mapped_column(String, nullable=True)          # match|uncertain
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    place: Mapped["Place"] = relationship(back_populates="photos")
    magazine: Mapped["Magazine"] = relationship(back_populates="photos")
    people: Mapped[list["PhotoPerson"]] = relationship(
        back_populates="photo", cascade="all, delete-orphan"
    )
    events: Mapped[list["PhotoEvent"]] = relationship(
        back_populates="photo", cascade="all, delete-orphan"
    )


class PhotoPerson(Base):
    """photo<->person tag with provenance + uncertain flag (SPEC §3.5)."""
    __tablename__ = "photo_person"

    photo_id: Mapped[int] = mapped_column(ForeignKey("photo.id", ondelete="CASCADE"), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(String, default=SOURCE_MANIFEST)
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False)

    photo: Mapped["Photo"] = relationship(back_populates="people")
    person: Mapped["Person"] = relationship()


class PhotoEvent(Base):
    """photo<->event tag with provenance (SPEC §3.6)."""
    __tablename__ = "photo_event"

    photo_id: Mapped[int] = mapped_column(ForeignKey("photo.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(String, default=SOURCE_AUTO)

    photo: Mapped["Photo"] = relationship(back_populates="events")
    event: Mapped["Event"] = relationship()


class User(Base):
    """Account, keyed by Cloudflare-Access-verified email (SPEC §6.3)."""
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, default="viewer")  # admin|contributor|viewer
    # Links the account to its person in the family tree (SPEC §3.3). Set by admin
    # at invite. When set, the People tree roots from this person ("Self" = them);
    # when null, the viewer sees the canonical Steve-rooted tree with no "Self".
    person_id: Mapped[str | None] = mapped_column(ForeignKey("person.id"), nullable=True)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Contribution(Base):
    """Edit log of human metadata changes (SPEC §3.5 — immediate + logged)."""
    __tablename__ = "contribution"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_email: Mapped[str | None] = mapped_column(String, nullable=True)
    photo_id: Mapped[int | None] = mapped_column(ForeignKey("photo.id"), nullable=True)
    field: Mapped[str] = mapped_column(String, nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON payload that reverses this edit (SPEC §3.5 undo); null = not undoable.
    inverse: Mapped[str | None] = mapped_column(Text, nullable=True)
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
