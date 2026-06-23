"""API response/request schemas (SPEC §6.2)."""
from datetime import date

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
    thumb_url: str
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


class PersonOut(BaseModel):
    id: str
    name: str
    relationship: str | None          # derived relative to Steve (SPEC §3.3)
    photo_count: int
    representative_photo_id: int | None


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
