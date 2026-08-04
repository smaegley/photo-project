"""Faceted photo query (SPEC §4).

Facets AND-combine (time ∩ people ∩ events ∩ map-region ∩ roll); within the
people and events facets selections are OR'd (any-of). Per-facet "live counts"
are computed with that facet's own selection removed, so the count shows what
*adding* a value would yield — standard faceted-search behaviour.
"""
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app import models as m, storage
from app.schemas import FacetCount, PhotoOut, PersonTag

def _version(p: m.Photo) -> str:
    """Cache-busting suffix tied to the master's change-token, so a rotated/re-exported
    photo gets a fresh URL and the browser stops serving the stale copy (SPEC §11.5).

    Reads the stored `file_version` column (SPEC §13.4). It used to stat() the master
    here — once per photo, during serialization, so 60 syscalls per gallery page — and
    for a B2-backed row that would have become 60 remote HEADs per page, which is the
    §10.16 pool-exhaustion shape all over again. Unstamped rows fall back to a one-time
    probe inside `storage.master_version` and self-heal on the next prewarm.
    """
    if not p.storage_path:
        return ""
    token = storage.master_version(p)
    return f"?v={token}" if token else ""


@dataclass
class PhotoFilter:
    date_start: date | None = None
    date_end: date | None = None
    people: list[str] = field(default_factory=list)
    events: list[int] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None  # (min_lon,min_lat,max_lon,max_lat)
    magazine_id: int | None = None
    origin: str | None = None  # slide|scan|digital — a hard view scope (SPEC §12.8)


def _apply(query, f: PhotoFilter, exclude: str | None):
    # origin is a view scope, not a facet: it constrains results AND every facet
    # count (no self-exclusion), so it is applied regardless of `exclude`.
    if f.origin:
        query = query.filter(m.Photo.origin == f.origin)
    if exclude != "date" and f.date_start and f.date_end:
        # overlap: the photo's range touches the slider range (SPEC §3.8/§4.1)
        query = query.filter(m.Photo.date_end >= f.date_start,
                             m.Photo.date_start <= f.date_end)
    if exclude != "people" and f.people:
        query = query.filter(m.Photo.id.in_(
            select(m.PhotoPerson.photo_id).where(m.PhotoPerson.person_id.in_(f.people))))
    if exclude != "events" and f.events:
        query = query.filter(m.Photo.id.in_(
            select(m.PhotoEvent.photo_id).where(m.PhotoEvent.event_id.in_(f.events))))
    if exclude != "places" and f.places:
        query = query.filter(m.Photo.place_id.in_(f.places))
    if exclude != "map" and f.bbox:
        mnlon, mnlat, mxlon, mxlat = f.bbox
        query = query.filter(m.Photo.place_id.in_(
            select(m.Place.id).where(
                m.Place.lat.isnot(None), m.Place.lat >= mnlat, m.Place.lat <= mxlat,
                m.Place.lon >= mnlon, m.Place.lon <= mxlon)))
    if exclude != "roll" and f.magazine_id:
        query = query.filter(m.Photo.magazine_id == f.magazine_id)
    return query


# Scan source_files are library-relative paths (photos/<batch>/<file>) that carry
# slashes and spaces; encode them for the URL (slides pass through unchanged). The
# matching routes are declared with a :path converter (SPEC §12.6).
def thumb_url(source_file: str) -> str:
    return f"/api/thumbnails/{quote(source_file)}"


def display_url(source_file: str) -> str:
    return f"/api/display/{quote(source_file)}"


def image_url(source_file: str) -> str:
    return f"/api/images/{quote(source_file)}"


def to_photo_out(p: m.Photo) -> PhotoOut:
    v = _version(p)
    return PhotoOut(
        id=p.id, source_file=p.source_file, caption=p.caption,
        date_raw=p.date_raw, date_start=p.date_start,
        magazine_id=p.magazine_id, slide_in_mag=p.slide_in_mag, origin=p.origin,
        thumb_url=thumb_url(p.source_file) + v,
        display_url=display_url(p.source_file) + v,
        image_url=image_url(p.source_file) + v,
    )


def matching_photo_ids(db: Session, f: PhotoFilter) -> list[int]:
    """All photo ids matching the filter (for 'select all in current view')."""
    q = _apply(db.query(m.Photo.id), f, exclude=None)
    return [r[0] for r in q.all()]


def run_query(db: Session, f: PhotoFilter, page: int, page_size: int):
    base = _apply(db.query(m.Photo), f, exclude=None)
    total = base.order_by(None).count()
    # Interleaved timeline (SPEC §12.5): everything sorts by materialized sort_date
    # (slides = their magazine's start, so a roll stays contiguous; scans = their
    # own date). Where a roll's start ties a scan's date, the roll's slides come
    # first (magazine_id-not-null); slides keep card order via slide_in_mag.
    # Unknown-date photos sort last, stable by id.
    order = [m.Photo.sort_date.is_(None), m.Photo.sort_date,
             m.Photo.magazine_id.is_(None), m.Photo.magazine_id,
             m.Photo.slide_in_mag,
             m.Photo.date_start.is_(None), m.Photo.date_start,
             m.Photo.id]
    rows = (base.order_by(*order)
            .offset((page - 1) * page_size).limit(page_size).all())
    photos = [to_photo_out(p) for p in rows]

    people_counts, event_counts, place_counts = [], [], []
    if page == 1:  # infinite-scroll pages reuse page 1's facet counts
        # people facet counts (people selection removed)
        pq = _apply(db.query(m.Person.id, m.Person.canonical_name,
                             func.count(func.distinct(m.PhotoPerson.photo_id)))
                    .join(m.PhotoPerson, m.Person.id == m.PhotoPerson.person_id)
                    .join(m.Photo, m.Photo.id == m.PhotoPerson.photo_id),
                    f, exclude="people").group_by(m.Person.id)
        people_counts = [FacetCount(key=pid, label=name, count=c)
                         for pid, name, c in pq.all()]

        # event facet counts (events selection removed)
        eq = _apply(db.query(m.Event.id, m.Event.name,
                             func.count(func.distinct(m.PhotoEvent.photo_id)))
                    .join(m.PhotoEvent, m.Event.id == m.PhotoEvent.event_id)
                    .join(m.Photo, m.Photo.id == m.PhotoEvent.photo_id),
                    f, exclude="events").group_by(m.Event.id)
        event_counts = [FacetCount(key=str(eid), label=name, count=c)
                        for eid, name, c in eq.all()]

        # place counts for the current result (places selection removed). Covers
        # ALL places, pinned or not, so the filter rail can live-count/grey them
        # like people/events; the map intersects these with its own mappable-only
        # list, so pins are unaffected (SPEC §12.8 fix).
        lq = _apply(db.query(m.Place.id, m.Place.canonical_name,
                             func.count(func.distinct(m.Photo.id)))
                    .join(m.Photo, m.Photo.place_id == m.Place.id),
                    f, exclude="places").group_by(m.Place.id)
        place_counts = [FacetCount(key=str(plid), label=name, count=c)
                        for plid, name, c in lq.all()]

        people_counts.sort(key=lambda x: -x.count)
        event_counts.sort(key=lambda x: -x.count)
    return total, photos, people_counts, event_counts, place_counts


def photo_people(db: Session, photo_id: int) -> list[PersonTag]:
    rows = (db.query(m.Person.id, m.Person.canonical_name,
                     m.PhotoPerson.source, m.PhotoPerson.uncertain,
                     m.PhotoPerson.region_x, m.PhotoPerson.region_y,
                     m.PhotoPerson.region_w, m.PhotoPerson.region_h)
            .join(m.PhotoPerson, m.Person.id == m.PhotoPerson.person_id)
            .filter(m.PhotoPerson.photo_id == photo_id).all())
    return [PersonTag(person_id=pid, name=name, source=src, uncertain=bool(unc),
                      region_x=rx, region_y=ry, region_w=rw, region_h=rh)
            for pid, name, src, unc, rx, ry, rw, rh in rows]


def photo_events(db: Session, photo_id: int) -> list[str]:
    return [name for (name,) in
            db.query(m.Event.name).join(m.PhotoEvent, m.Event.id == m.PhotoEvent.event_id)
            .filter(m.PhotoEvent.photo_id == photo_id).all()]
