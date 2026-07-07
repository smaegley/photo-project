"""API response/request schemas (SPEC §6.2)."""
from datetime import date, datetime

from pydantic import BaseModel


class PersonTag(BaseModel):
    person_id: str
    name: str
    source: str
    uncertain: bool


class PhotoOut(BaseModel):
    """Grid item — light payload for the gallery."""
    id: int
    source_file: str
    caption: str | None
    date_raw: str | None
    date_start: date | None
    magazine_id: int | None
    slide_in_mag: int | None
    origin: str                 # slide|scan|digital (SPEC §11)
    thumb_url: str
    display_url: str            # sized derivative for the lightbox
    image_url: str


class PhotoDetail(PhotoOut):
    """Lightbox payload — full metadata + Dad's notes (SPEC §7.3)."""
    date_end: date | None
    date_precision: str | None
    original_subject: str | None
    notes: str | None
    validation: str | None
    place: "PlaceOut | None"
    magazine_id: int | None
    slide_in_mag: int | None
    people: list[PersonTag]
    events: list[str]


class FacetCount(BaseModel):
    key: str
    label: str
    count: int


class PhotoQueryResult(BaseModel):
    total: int
    page: int
    page_size: int
    photos: list[PhotoOut]
    # live per-facet counts for the current filter set (self-facet excluded)
    people_counts: list[FacetCount]
    event_counts: list[FacetCount]
    place_counts: list[FacetCount]   # mappable places only; drives map pin filtering


class PersonOut(BaseModel):
    id: str
    name: str
    relationship: str | None          # derived relative to Steve (SPEC §3.3)
    photo_count: int
    representative_photo_id: int | None
    face_url: str | None = None        # cropped face thumbnail (SPEC §4.2)


class EventOut(BaseModel):
    id: int
    name: str
    photo_count: int


class PlaceOut(BaseModel):
    id: str
    name: str
    region: str | None
    precision: str
    lat: float | None
    lon: float | None
    photo_count: int | None = None


class MagazineOut(BaseModel):
    id: int
    title: str | None
    span_label: str | None
    slide_count: int
    card_image_paths: list[str]


# ---- admin / contributions (SPEC §3.5) ----
class EventCreate(BaseModel):
    name: str


class EventRename(BaseModel):
    name: str


class EventMerge(BaseModel):
    into_id: int


class BulkEventReq(BaseModel):
    photo_ids: list[int]
    event_id: int
    op: str  # "add" | "remove"


class BulkPersonReq(BaseModel):
    photo_ids: list[int]
    person_id: str
    op: str  # "add" | "remove"


class BulkPlaceReq(BaseModel):
    photo_ids: list[int]
    place_id: str | None = None  # null = clear the place


class PlaceCreate(BaseModel):
    name: str
    region: str | None = None
    precision: str = "unknown"
    lat: float | None = None
    lon: float | None = None


class PlaceUpdate(BaseModel):
    name: str | None = None
    region: str | None = None
    precision: str | None = None
    lat: float | None = None
    lon: float | None = None


class PlaceMerge(BaseModel):
    into_id: str


class RotateReq(BaseModel):
    degrees: int  # clockwise: 90 | 180 | 270


# ---- auth / users (SPEC §6.3, §10.5) ----
class MeOut(BaseModel):
    """The current viewer — drives frontend role gating."""
    email: str
    role: str                       # admin | contributor | viewer
    person_id: str | None           # linked tree person (per-viewer rooting)
    display_name: str | None
    theme: str = "system"           # light | dark | system (per-user UI pref)
    is_dev: bool = False            # True when CF Access is absent (enables dev switcher)


class MePatch(BaseModel):
    """Self-service viewer preferences (any role)."""
    theme: str | None = None        # light | dark | system


class PersonCreate(BaseModel):
    id: str                         # caller-supplied slug (validated server-side)
    canonical_name: str
    father_id: str | None = None
    mother_id: str | None = None
    spouse_id: str | None = None


class PersonRename(BaseModel):
    canonical_name: str


class CaptionReq(BaseModel):
    caption: str | None = None


class RepresentativeReq(BaseModel):
    photo_id: int | None = None     # null clears it


class FaceRegionReq(BaseModel):
    photo_id: int
    x: float                        # normalized face-box center + size (0..1)
    y: float
    w: float
    h: float


class UsageReq(BaseModel):
    event_type: str                 # view | download | search
    target: str | None = None


class UsageStat(BaseModel):
    key: str
    label: str
    count: int


class UsageUser(BaseModel):
    email: str
    views: int
    downloads: int


class UsageStats(BaseModel):
    total_views: int
    total_downloads: int
    top_photos: list[UsageStat]     # most-viewed across all users
    per_user: list[UsageUser]       # per-user view/download subtotals
    recent: list[str]


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str | None
    role: str
    person_id: str | None
    invited_at: datetime | None
    last_login: datetime | None


class UserCreate(BaseModel):
    """Pre-register a CF-Access email and set its role + tree link ("invite")."""
    email: str
    role: str = "viewer"
    person_id: str | None = None
    display_name: str | None = None


class UserUpdate(BaseModel):
    role: str | None = None
    person_id: str | None = None
    display_name: str | None = None
