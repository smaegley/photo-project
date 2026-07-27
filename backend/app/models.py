"""SQLAlchemy models — the Maegley Photo Album data model (SPEC §3.2).

Backbone: a flat, date-ordered photo stream with rich tags. People are a derived
family tree; places a gazetteer; events suggest->confirm; magazines an optional
provenance overlay. Two-tier provenance on every tag (SPEC §3.5).
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text,
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
    # False = friend / other (non-family), grouped separately in the People filter
    # and skipped by the family-tree deriver (SPEC §12.7). Slides pre-date this and
    # backfill to True.
    is_family: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
    # Composite index backing the interleaved gallery sort (SPEC §12.5).
    __table_args__ = (
        Index("ix_photo_sort", "sort_date", "magazine_id", "slide_in_mag"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_file: Mapped[str] = mapped_column(String, unique=True, nullable=False)  # slide: Mag<N>_Slide<NN>.JPG; scan: photos/<batch>/<file>; else a stable key
    # SPEC §11: non-slide ingest. origin drives serving path + UI; storage_path
    # locates the file under library_root (DB lookup, not a filename regex).
    origin: Mapped[str] = mapped_column(String, nullable=False, default="slide")   # slide|scan|digital
    # SPEC §13.3/§13.4: where the master lives + a cheap change-token. storage_backend
    # selects the backend (local file vs B2 object); storage_path is the key within it
    # (library-relative path for local; B2 object key for b2). file_version stamps the
    # ?v= cache-buster + derivative cache key so serving never stat()s the master.
    storage_backend: Mapped[str] = mapped_column(String, nullable=False, default="local", server_default="local")  # local|b2
    storage_path: Mapped[str | None] = mapped_column(String, nullable=True)        # relative to library_root (local) or B2 object key
    file_version: Mapped[str | None] = mapped_column(String, nullable=True)        # mtime (local) / ETag-ish token (b2)
    original_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    # SPEC §12.3: scan provenance. batch = FastFoto subject folder (null for slides);
    # back_path = library-relative path of the paired back-of-photo (_b) scan.
    batch: Mapped[str | None] = mapped_column(String, nullable=True)
    back_path: Mapped[str | None] = mapped_column(String, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The photo's OWN coordinates, kept even when they resolve to no gazetteer place —
    # that is what lets the admin map show "locations I haven't named yet" (SPEC §13.9).
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)               # = card_caption
    original_subject: Mapped[str | None] = mapped_column(String, nullable=True)    # = mag_subject
    date_start: Mapped[datetime | None] = mapped_column(Date, nullable=True, index=True)
    date_end: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    date_precision: Mapped[str | None] = mapped_column(String, nullable=True)      # day|month|season|year|approx
    date_raw: Mapped[str | None] = mapped_column(String, nullable=True)            # verbatim display label
    # Materialized sort key for the mixed timeline (SPEC §12.5): slides = their
    # magazine's date_start (a roll stays contiguous, in card order); scans/digital
    # = their own date_start. Null sorts last.
    sort_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    place_id: Mapped[str | None] = mapped_column(ForeignKey("place.id"), nullable=True, index=True)
    magazine_id: Mapped[int | None] = mapped_column(ForeignKey("magazine.id"), nullable=True, index=True)
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
    person_id: Mapped[str] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), primary_key=True, index=True)
    source: Mapped[str] = mapped_column(String, default=SOURCE_MANIFEST)
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    # Lightroom face-region box (normalized center + size, 0..1) for this person in
    # this photo — used to crop a face thumbnail for the People filter (SPEC §4.2).
    region_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    region_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    region_w: Mapped[float | None] = mapped_column(Float, nullable=True)
    region_h: Mapped[float | None] = mapped_column(Float, nullable=True)

    photo: Mapped["Photo"] = relationship(back_populates="people")
    person: Mapped["Person"] = relationship()


class PhotoEvent(Base):
    """photo<->event tag with provenance (SPEC §3.6)."""
    __tablename__ = "photo_event"

    photo_id: Mapped[int] = mapped_column(ForeignKey("photo.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id", ondelete="CASCADE"), primary_key=True, index=True)
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
    theme: Mapped[str] = mapped_column(String, default="system")  # light|dark|system (per-user UI pref)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UsageEvent(Base):
    """Lightweight usage log — who viewed/downloaded what, for the admin stats panel.
    Deliberately minimal; Cloudflare handles login/traffic analytics separately."""
    __tablename__ = "usage_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_email: Mapped[str | None] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)  # view|download|search
    target: Mapped[str | None] = mapped_column(String, nullable=True)  # photo source_file, filter summary, …
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Contribution(Base):
    """Edit log of human metadata changes (SPEC §3.5 — immediate + logged)."""
    __tablename__ = "contribution"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_email: Mapped[str | None] = mapped_column(String, nullable=True)
    photo_id: Mapped[int | None] = mapped_column(ForeignKey("photo.id"), nullable=True, index=True)
    field: Mapped[str] = mapped_column(String, nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON payload that reverses this edit (SPEC §3.5 undo); null = not undoable.
    inverse: Mapped[str | None] = mapped_column(Text, nullable=True)
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ---- face matching (SPEC §14) ----------------------------------------------------
# Suggestions live in their own tables rather than as speculative `photo_person` rows,
# so an unreviewed guess can never reach the gallery and abandoning the ML layer stays
# a DROP TABLE. Confirming a suggestion writes an ordinary `photo_person` row, which is
# then indistinguishable from a hand-made tag — that is the point.

FACE_PENDING, FACE_ACCEPTED, FACE_REJECTED = "pending", "accepted", "rejected"
CLUSTER_PENDING, CLUSTER_NAMED, CLUSTER_IGNORED = "pending", "named", "ignored"


class FaceCluster(Base):
    """A group of unnamed faces believed to be the same (unknown) person (SPEC §14.7a).

    Exists so Steve can dismiss background strangers in one action instead of hundreds.
    `centroid` is what makes an `ignored` decision **stick**: on a later run a new unnamed
    face near an ignored centroid joins this cluster silently rather than being re-asked.
    `prominence` (median face area x detection score) sorts nameable people to the top and
    buries the crowd tail.
    """
    __tablename__ = "face_cluster"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default=CLUSTER_PENDING, index=True)
    person_id: Mapped[str | None] = mapped_column(ForeignKey("person.id", ondelete="SET NULL"), nullable=True)
    centroid: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # float32[512]
    n_faces: Mapped[int] = mapped_column(Integer, default=0)
    prominence: Mapped[float | None] = mapped_column(Float, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Face(Base):
    """One detected face in one photo (SPEC §14.6).

    Geometry uses the same normalized **centre + size** convention as
    `photo_person.region_*`, so a confirmed suggestion copies straight across.

    `embedding` is nullable on purpose: enrichment runs on dev and exports to prod
    (§14.8a), and prod needs the box (to crop a face for review) but never the vector.
    Leaving it out of the export keeps ~2 KB/face off the wire and off the serving box.
    """
    __tablename__ = "face"
    __table_args__ = (Index("ix_face_photo", "photo_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    photo_id: Mapped[int] = mapped_column(ForeignKey("photo.id", ondelete="CASCADE"), nullable=False)
    # normalized centre + size, 0..1
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    w: Mapped[float] = mapped_column(Float, nullable=False)
    h: Mapped[float] = mapped_column(Float, nullable=False)
    det_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # float32[512]
    detector_version: Mapped[str | None] = mapped_column(String, nullable=True)
    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("face_cluster.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FaceSuggestion(Base):
    """"This face is probably <person>, at <score>" — awaiting a human click (SPEC §14.7).

    Never auto-applied: at the measured 0.45 threshold ~4.6% of strangers still score
    above it (P-F3), so confirmation is the defence, not the threshold. A rejection is
    kept rather than deleted so the same wrong guess is not offered again next run.
    """
    __tablename__ = "face_suggestion"
    __table_args__ = (
        UniqueConstraint("face_id", "person_id", name="uq_face_suggestion"),
        Index("ix_face_suggestion_status", "status", "score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    face_id: Mapped[int] = mapped_column(ForeignKey("face.id", ondelete="CASCADE"), nullable=False)
    person_id: Mapped[str] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default=FACE_PENDING)
    decided_by: Mapped[str | None] = mapped_column(String, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
