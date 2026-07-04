"""Photo endpoints — the faceted gallery query and the lightbox detail."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import models as m, queries
from app.auth import current_user
from app.database import get_db
from app.queries import PhotoFilter
from app.schemas import PhotoDetail, PhotoQueryResult, PlaceOut

router = APIRouter(prefix="/api", tags=["photos"])


def _parse_bbox(bbox: str | None):
    if not bbox:
        return None
    try:
        a, b, c, d = (float(x) for x in bbox.split(","))
        return (a, b, c, d)
    except ValueError:
        raise HTTPException(400, "bbox must be 'minLon,minLat,maxLon,maxLat'")


@router.get("/photos", response_model=PhotoQueryResult)
def list_photos(
    db: Session = Depends(get_db),
    _user=Depends(current_user),
    date_start: date | None = None,
    date_end: date | None = None,
    people: list[str] = Query(default=[]),
    events: list[int] = Query(default=[]),
    places: list[str] = Query(default=[]),
    bbox: str | None = None,
    magazine_id: int | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=60, ge=1, le=500),
):
    f = PhotoFilter(date_start=date_start, date_end=date_end, people=people,
                    events=events, places=places, bbox=_parse_bbox(bbox),
                    magazine_id=magazine_id)
    total, photos, people_counts, event_counts, place_counts = queries.run_query(db, f, page, page_size)
    return PhotoQueryResult(
        total=total, page=page, page_size=page_size, photos=photos,
        people_counts=people_counts, event_counts=event_counts, place_counts=place_counts,
    )


@router.get("/photos/ids", response_model=list[int])
def list_photo_ids(
    db: Session = Depends(get_db),
    _user=Depends(current_user),
    date_start: date | None = None,
    date_end: date | None = None,
    people: list[str] = Query(default=[]),
    events: list[int] = Query(default=[]),
    places: list[str] = Query(default=[]),
    bbox: str | None = None,
    magazine_id: int | None = None,
):
    """All matching photo ids for the current filter — backs 'select all'."""
    f = PhotoFilter(date_start=date_start, date_end=date_end, people=people,
                    events=events, places=places, bbox=_parse_bbox(bbox),
                    magazine_id=magazine_id)
    return queries.matching_photo_ids(db, f)


@router.get("/photos/{photo_id}", response_model=PhotoDetail)
def photo_detail(photo_id: int, db: Session = Depends(get_db), _user=Depends(current_user)):
    p = db.get(m.Photo, photo_id)
    if p is None:
        raise HTTPException(404, "photo not found")
    place = None
    if p.place:
        place = PlaceOut(id=p.place.id, name=p.place.canonical_name, region=p.place.region,
                         precision=p.place.precision, lat=p.place.lat, lon=p.place.lon)
    base = queries.to_photo_out(p)
    return PhotoDetail(
        **base.model_dump(),  # includes magazine_id + slide_in_mag from PhotoOut
        date_end=p.date_end, date_precision=p.date_precision,
        original_subject=p.original_subject, notes=p.notes, validation=p.validation,
        place=place,
        people=queries.photo_people(db, photo_id),
        events=queries.photo_events(db, photo_id),
    )
