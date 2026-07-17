"""Facet list endpoints — people (grouped by derived relationship), events,
places (for the map), and magazines (rolls)."""
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import derivatives, models as m
from app.auth import current_user
from app.config import settings
from app.database import get_db
from app.family import derive_relationships
from app.schemas import (
    EventOut, MagazineOut, MeOut, MePatch, PersonOut, PlaceOut, UsageReq,
)

router = APIRouter(prefix="/api", tags=["facets"])

_THEMES = {"light", "dark", "system"}


def _me(user: m.User) -> MeOut:
    return MeOut(email=user.email, role=user.role, person_id=user.person_id,
                 display_name=user.display_name, theme=user.theme or "system",
                 is_dev=not settings.cf_access_enabled)


@router.get("/me", response_model=MeOut)
def whoami(user: m.User = Depends(current_user)):
    """The current viewer + role — the frontend gates its admin/contributor UI on this."""
    return _me(user)


@router.get("/logout")
def logout():
    """Redirect to Cloudflare Access logout in prod; back to root in dev (no-op)."""
    if settings.cf_access_enabled:
        return RedirectResponse(
            url=f"https://{settings.cf_access_team_domain}/cdn-cgi/access/logout"
        )
    return RedirectResponse(url="/")


@router.get("/dev/users")
def dev_list_users(db: Session = Depends(get_db)):
    """Dev-only: all registered users for the switcher UI. 404s in prod."""
    if settings.cf_access_enabled:
        raise HTTPException(404)
    return [{"email": u.email, "display_name": u.display_name,
             "person_id": u.person_id, "role": u.role}
            for u in db.query(m.User).order_by(m.User.email).all()]


@router.get("/dev/switch")
def dev_switch(email: str = ""):
    """Dev-only: set (or clear) the dev_override cookie and reload. 404s in prod."""
    if settings.cf_access_enabled:
        raise HTTPException(404)
    r = RedirectResponse(url="/", status_code=302)
    if email:
        r.set_cookie("dev_override", email, max_age=30 * 86400, samesite="lax", path="/")
    else:
        r.delete_cookie("dev_override", path="/")
    return r


@router.patch("/me", response_model=MeOut)
def update_me(body: MePatch, db: Session = Depends(get_db), user: m.User = Depends(current_user)):
    """Self-service viewer preferences (theme). Any role."""
    if body.theme is not None:
        if body.theme not in _THEMES:
            raise HTTPException(400, "theme must be light|dark|system")
        user.theme = body.theme
        db.commit()
    return _me(user)


@router.post("/usage")
def log_usage(body: UsageReq, db: Session = Depends(get_db), user: m.User = Depends(current_user)):
    """Lightweight usage beacon (fire-and-forget) for the admin stats panel."""
    db.add(m.UsageEvent(user_email=user.email, event_type=body.event_type[:32],
                        target=(body.target or "")[:200] or None))
    db.commit()
    return {"ok": True}

# Stable display order for relationship groups in the People rail.
REL_ORDER = ["Self", "Parent", "Sibling", "Child", "Grandparent",
             "Aunt / Uncle", "Cousin", "Spouse", "Extended family",
             "Friends & others"]


@router.get("/people", response_model=list[PersonOut])
def list_people(db: Session = Depends(get_db), user=Depends(current_user),
                with_photos_only: bool = True):
    persons = db.query(m.Person).all()
    # Root the tree from the viewer's linked person ("Self" = them); an unlinked
    # viewer gets the canonical Steve tree but with no "Self" (SPEC §3.3).
    if user.person_id:
        rels = derive_relationships(persons, root=user.person_id, mark_self=True)
    else:
        # Unlinked viewer has no place in the family tree — show everyone flat
        # with no relationship labels rather than a Steve-rooted view that excludes Steve.
        rels = {p.id: None for p in persons}
    counts = dict(db.query(m.PhotoPerson.person_id,
                           func.count(func.distinct(m.PhotoPerson.photo_id)))
                  .group_by(m.PhotoPerson.person_id).all())
    out = []
    for p in persons:
        n = counts.get(p.id, 0)
        if with_photos_only and n == 0:
            continue
        face_url = None
        if p.representative_photo_id:
            pp = db.get(m.PhotoPerson, (p.representative_photo_id, p.id))
            region = (pp.region_x, pp.region_y, pp.region_w, pp.region_h) if pp else None
            face_url = f"/api/faces/{p.id}?v={derivatives.face_version(p.representative_photo_id, region)}"
        out.append(PersonOut(
            id=p.id, name=p.canonical_name, relationship=rels.get(p.id),
            photo_count=n, representative_photo_id=p.representative_photo_id, face_url=face_url,
            father_id=p.father_id, mother_id=p.mother_id, spouse_id=p.spouse_id,
            is_family=p.is_family))
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
