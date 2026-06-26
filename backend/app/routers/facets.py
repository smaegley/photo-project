"""Facet list endpoints — people (grouped by derived relationship), events,
places (for the map), and magazines (rolls)."""
import json

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models as m
from app.auth import current_user
from app.database import get_db
from app.family import derive_relationships
from app.schemas import EventOut, MagazineOut, MeOut, PersonOut, PlaceOut

router = APIRouter(prefix="/api", tags=["facets"])


@router.get("/me", response_model=MeOut)
def whoami(user: m.User = Depends(current_user)):
    """The current viewer + role — the frontend gates its admin/contributor UI on this."""
    return MeOut(email=user.email, role=user.role,
                 person_id=user.person_id, display_name=user.display_name)

# Stable display order for relationship groups in the People rail.
REL_ORDER = ["Self", "Parent", "Sibling", "Child", "Grandparent",
             "Aunt / Uncle", "Cousin", "Spouse", "Extended family"]


@router.get("/people", response_model=list[PersonOut])
def list_people(db: Session = Depends(get_db), user=Depends(current_user),
                with_photos_only: bool = True):
    persons = db.query(m.Person).all()
    # Root the tree from the viewer's linked person ("Self" = them); an unlinked
    # viewer gets the canonical Steve tree but with no "Self" (SPEC §3.3).
    if user.person_id:
        rels = derive_relationships(persons, root=user.person_id, mark_self=True)
    else:
        rels = derive_relationships(persons, mark_self=False)
    counts = dict(db.query(m.PhotoPerson.person_id,
                           func.count(func.distinct(m.PhotoPerson.photo_id)))
                  .group_by(m.PhotoPerson.person_id).all())
    out = []
    for p in persons:
        n = counts.get(p.id, 0)
        if with_photos_only and n == 0:
            continue
        out.append(PersonOut(id=p.id, name=p.canonical_name, relationship=rels.get(p.id),
                             photo_count=n, representative_photo_id=p.representative_photo_id))
    out.sort(key=lambda x: (REL_ORDER.index(x.relationship) if x.relationship in REL_ORDER else 99,
                            -x.photo_count, x.name))
    return out


@router.get("/events", response_model=list[EventOut])
def list_events(db: Session = Depends(get_db), _user=Depends(current_user),
                with_photos_only: bool = True):
    counts = dict(db.query(m.PhotoEvent.event_id,
                           func.count(func.distinct(m.PhotoEvent.photo_id)))
                  .group_by(m.PhotoEvent.event_id).all())
    out = [EventOut(id=e.id, name=e.name, photo_count=counts.get(e.id, 0))
           for e in db.query(m.Event).all()]
    if with_photos_only:
        out = [e for e in out if e.photo_count > 0]
    out.sort(key=lambda x: -x.photo_count)
    return out


@router.get("/places", response_model=list[PlaceOut])
def list_places(db: Session = Depends(get_db), _user=Depends(current_user),
                mappable_only: bool = False):
    counts = dict(db.query(m.Photo.place_id, func.count(m.Photo.id))
                  .filter(m.Photo.place_id.isnot(None))
                  .group_by(m.Photo.place_id).all())
    out = []
    for pl in db.query(m.Place).all():
        if mappable_only and pl.lat is None:
            continue
        out.append(PlaceOut(id=pl.id, name=pl.canonical_name, region=pl.region,
                            precision=pl.precision, lat=pl.lat, lon=pl.lon,
                            photo_count=counts.get(pl.id, 0)))
    out.sort(key=lambda x: -(x.photo_count or 0))
    return out


@router.get("/magazines", response_model=list[MagazineOut])
def list_magazines(db: Session = Depends(get_db), _user=Depends(current_user)):
    out = []
    for mag in db.query(m.Magazine).order_by(m.Magazine.id).all():
        cards = json.loads(mag.card_image_paths) if mag.card_image_paths else []
        out.append(MagazineOut(id=mag.id, title=mag.title, span_label=mag.span_label,
                               slide_count=mag.slide_count, card_image_paths=cards))
    return out
