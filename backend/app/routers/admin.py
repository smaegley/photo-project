"""Admin write endpoints — maintain the event/place vocabularies, bulk-tag
photos, and per-photo edits (SPEC §3.5: edits are immediate + logged, admin-only).

Every change writes a Contribution row that carries both an audit summary
(old/new) and an `inverse` JSON payload — the exact data needed to reverse it.
`POST /undo` pops the most recent not-yet-undone edit and replays its inverse,
so undo is a repeatable stack. Adding/confirming a tag flips provenance to
human-confirmed.
"""
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from PIL import Image

from app import derivatives, models as m
from app.auth import require_admin, require_contributor
from app.config import settings
from app.database import get_db
from app.geocoding import geocode
from app.models import SOURCE_AUTO, SOURCE_HUMAN
from app.routers.images import photo_file, _safe_key
from app.schemas import (
    BulkEventReq, BulkPersonReq, BulkPlaceReq, CaptionReq, NotesReq,
    EventCreate, EventMerge, EventOut, EventRename,
    FaceRegionReq, PersonCreate, PersonLinksUpdate, PersonRename, PlaceCreate, PlaceMerge, PlaceOut, PlaceUpdate,
    RepresentativeReq, RotateReq, UsageStat, UsageStats, UsageUser, UserCreate, UserOut, UserUpdate,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

_CHUNK = 500  # keep SQLite IN(...) under its ~999 bound-variable limit


def _chunks(seq):
    for i in range(0, len(seq), _CHUNK):
        yield seq[i:i + _CHUNK]


def _check_photo_ids(db: Session, ids: list[int]) -> None:
    """Reject unknown photo ids up front (a bad id would otherwise surface as a
    500 FK IntegrityError from a bulk insert/update)."""
    found: set[int] = set()
    for ch in _chunks(ids):
        found |= {r[0] for r in db.query(m.Photo.id).filter(m.Photo.id.in_(ch)).all()}
    missing = set(ids) - found
    if missing:
        raise HTTPException(400, f"unknown photo ids: {sorted(missing)[:5]}")


def _log(db: Session, user: m.User, field: str, old: str | None, new: str | None,
         photo_id: int | None = None, inverse: dict | None = None) -> None:
    db.add(m.Contribution(user_email=user.email, photo_id=photo_id, field=field,
                          old_value=old, new_value=new,
                          inverse=json.dumps(inverse) if inverse is not None else None))


def _event_count(db: Session, event_id: int) -> int:
    return (db.query(func.count(func.distinct(m.PhotoEvent.photo_id)))
            .filter(m.PhotoEvent.event_id == event_id).scalar() or 0)


def _place_count(db: Session, place_id: str) -> int:
    return (db.query(func.count(m.Photo.id))
            .filter(m.Photo.place_id == place_id).scalar() or 0)


def _place_out(db: Session, pl: m.Place) -> PlaceOut:
    return PlaceOut(id=pl.id, name=pl.canonical_name, region=pl.region,
                    precision=pl.precision, lat=pl.lat, lon=pl.lon,
                    photo_count=_place_count(db, pl.id))


def _rotate_file(p: m.Photo, deg: int) -> None:
    """Rotate the slide on disk (clockwise) and refresh its cached derivatives."""
    rot_map = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
               270: Image.Transpose.ROTATE_90}
    path = photo_file(p)
    with Image.open(path) as im:
        im.load()
        rot = im.transpose(rot_map[deg])
    rot.save(path, "JPEG", quality=95)
    # Eagerly refresh both derivatives so the new orientation shows immediately
    # (the server's mtime self-heal would also catch them on next request).
    key = _safe_key(p.source_file)
    derivatives.generate(path, settings.thumbnails_dir / key, derivatives.THUMB_MAX)
    derivatives.generate(path, settings.display_dir / key, derivatives.DISPLAY_MAX)


# ---------- event vocabulary ----------
@router.post("/events", response_model=EventOut)
def create_event(body: EventCreate, db: Session = Depends(get_db),
                 user: m.User = Depends(require_admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    if db.query(m.Event).filter(func.lower(m.Event.name) == name.lower()).first():
        raise HTTPException(409, f"event '{name}' already exists")
    e = m.Event(name=name)
    db.add(e)
    db.flush()
    _log(db, user, "event:create", None, name, inverse={"op": "event_delete", "id": e.id})
    db.commit()
    db.refresh(e)
    return EventOut(id=e.id, name=e.name, photo_count=0)


@router.patch("/events/{event_id}", response_model=EventOut)
def rename_event(event_id: int, body: EventRename, db: Session = Depends(get_db),
                 user: m.User = Depends(require_admin)):
    e = db.get(m.Event, event_id)
    if e is None:
        raise HTTPException(404, "event not found")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    clash = (db.query(m.Event)
             .filter(func.lower(m.Event.name) == name.lower(), m.Event.id != event_id)
             .first())
    if clash:
        raise HTTPException(409, f"event '{name}' already exists (use merge)")
    old, e.name = e.name, name
    _log(db, user, "event:rename", old, name,
         inverse={"op": "event_rename", "id": e.id, "name": old})
    db.commit()
    return EventOut(id=e.id, name=e.name, photo_count=_event_count(db, e.id))


@router.post("/events/{event_id}/merge", response_model=EventOut)
def merge_event(event_id: int, body: EventMerge, db: Session = Depends(get_db),
                user: m.User = Depends(require_admin)):
    src = db.get(m.Event, event_id)
    dst = db.get(m.Event, body.into_id)
    if src is None or dst is None:
        raise HTTPException(404, "event not found")
    if src.id == dst.id:
        raise HTTPException(400, "cannot merge an event into itself")
    src_name, dst_name = src.name, dst.name

    src_rows = [[pid, srcv] for pid, srcv in db.query(m.PhotoEvent.photo_id, m.PhotoEvent.source)
                .filter(m.PhotoEvent.event_id == src.id).all()]
    have_dst = {r[0] for r in db.query(m.PhotoEvent.photo_id)
                .filter(m.PhotoEvent.event_id == dst.id).all()}
    added_to_dst = []
    for pid, _src in src_rows:
        if pid not in have_dst:
            db.add(m.PhotoEvent(photo_id=pid, event_id=dst.id, source=SOURCE_HUMAN))
            added_to_dst.append(pid)
    db.query(m.PhotoEvent).filter(m.PhotoEvent.event_id == src.id).delete(
        synchronize_session=False)
    db.delete(src)
    _log(db, user, "event:merge", src_name, dst_name,
         inverse={"op": "event_unmerge", "src_name": src_name, "src_id": event_id,
                  "dst_id": dst.id, "src_rows": src_rows, "added_to_dst": added_to_dst})
    db.commit()
    return EventOut(id=dst.id, name=dst.name, photo_count=_event_count(db, dst.id))


@router.delete("/events/{event_id}")
def delete_event(event_id: int, db: Session = Depends(get_db),
                 user: m.User = Depends(require_admin)):
    e = db.get(m.Event, event_id)
    if e is None:
        raise HTTPException(404, "event not found")
    name, eid = e.name, e.id
    rows = [[pid, srcv] for pid, srcv in db.query(m.PhotoEvent.photo_id, m.PhotoEvent.source)
            .filter(m.PhotoEvent.event_id == e.id).all()]
    db.query(m.PhotoEvent).filter(m.PhotoEvent.event_id == e.id).delete(
        synchronize_session=False)
    db.delete(e)
    _log(db, user, "event:delete", name, str(len(rows)),
         inverse={"op": "event_restore", "id": eid, "name": name, "rows": rows})
    db.commit()
    return {"deleted": name, "tags_removed": len(rows)}


# ---------- bulk photo tagging ----------
@router.post("/photos/event")
def bulk_event(body: BulkEventReq, db: Session = Depends(get_db),
               user: m.User = Depends(require_contributor)):
    e = db.get(m.Event, body.event_id)
    if e is None:
        raise HTTPException(404, "event not found")
    ids = body.photo_ids
    if not ids:
        raise HTTPException(400, "no photos selected")
    _check_photo_ids(db, ids)

    if body.op == "add":
        existing: dict[int, m.PhotoEvent] = {}
        for ch in _chunks(ids):
            for pe in db.query(m.PhotoEvent).filter(
                    m.PhotoEvent.event_id == e.id,
                    m.PhotoEvent.photo_id.in_(ch)).all():
                existing[pe.photo_id] = pe
        added, confirmed = [], []
        for pid in ids:
            pe = existing.get(pid)
            if pe is not None:
                if pe.source != SOURCE_HUMAN:
                    pe.source = SOURCE_HUMAN
                    confirmed.append(pid)
            else:
                db.add(m.PhotoEvent(photo_id=pid, event_id=e.id, source=SOURCE_HUMAN))
                added.append(pid)
        _log(db, user, "event:add", str(len(ids)), e.name,
             inverse={"op": "event_add_undo", "event_id": e.id,
                      "added": added, "confirmed": confirmed})
        db.commit()
        return {"event": e.name, "added": len(added), "confirmed": len(confirmed)}

    if body.op == "remove":
        rows = []
        for ch in _chunks(ids):
            rows += [[pid, srcv] for pid, srcv in
                     db.query(m.PhotoEvent.photo_id, m.PhotoEvent.source).filter(
                         m.PhotoEvent.event_id == e.id, m.PhotoEvent.photo_id.in_(ch)).all()]
            db.query(m.PhotoEvent).filter(
                m.PhotoEvent.event_id == e.id, m.PhotoEvent.photo_id.in_(ch)).delete(
                synchronize_session=False)
        _log(db, user, "event:remove", e.name, str(len(rows)),
             inverse={"op": "event_remove_undo", "event_id": e.id, "rows": rows})
        db.commit()
        return {"event": e.name, "removed": len(rows)}

    raise HTTPException(400, "op must be 'add' or 'remove'")


@router.post("/photos/person")
def bulk_person(body: BulkPersonReq, db: Session = Depends(get_db),
                user: m.User = Depends(require_contributor)):
    person = db.get(m.Person, body.person_id)
    if person is None:
        raise HTTPException(404, "person not found")
    ids = body.photo_ids
    if not ids:
        raise HTTPException(400, "no photos selected")
    _check_photo_ids(db, ids)

    if body.op == "add":
        existing = set()
        for ch in _chunks(ids):
            existing |= {r[0] for r in db.query(m.PhotoPerson.photo_id).filter(
                m.PhotoPerson.person_id == person.id, m.PhotoPerson.photo_id.in_(ch)).all()}
        added = []
        for pid in ids:
            if pid not in existing:
                db.add(m.PhotoPerson(photo_id=pid, person_id=person.id,
                                     source=SOURCE_HUMAN, uncertain=False))
                added.append(pid)
        _log(db, user, "person:add", str(len(ids)), person.canonical_name,
             inverse={"op": "person_add_undo", "person_id": person.id, "added": added})
        db.commit()
        return {"person": person.canonical_name, "added": len(added)}

    if body.op == "remove":
        rows = []
        for ch in _chunks(ids):
            rows += [[pid, srcv, int(unc)] for pid, srcv, unc in
                     db.query(m.PhotoPerson.photo_id, m.PhotoPerson.source,
                              m.PhotoPerson.uncertain).filter(
                         m.PhotoPerson.person_id == person.id,
                         m.PhotoPerson.photo_id.in_(ch)).all()]
            db.query(m.PhotoPerson).filter(
                m.PhotoPerson.person_id == person.id,
                m.PhotoPerson.photo_id.in_(ch)).delete(synchronize_session=False)
        _log(db, user, "person:remove", person.canonical_name, str(len(rows)),
             inverse={"op": "person_remove_undo", "person_id": person.id, "rows": rows})
        db.commit()
        return {"person": person.canonical_name, "removed": len(rows)}

    raise HTTPException(400, "op must be 'add' or 'remove'")


@router.post("/photos/place")
def bulk_place(body: BulkPlaceReq, db: Session = Depends(get_db),
               user: m.User = Depends(require_contributor)):
    """Set (or clear, if place_id is null) the place on a set of photos."""
    ids = body.photo_ids
    if not ids:
        raise HTTPException(400, "no photos selected")
    _check_photo_ids(db, ids)
    name = None
    if body.place_id is not None:
        place = db.get(m.Place, body.place_id)
        if place is None:
            raise HTTPException(404, "place not found")
        name = place.canonical_name
    prior = []
    updated = 0
    for ch in _chunks(ids):
        prior += [[pid, old] for pid, old in
                  db.query(m.Photo.id, m.Photo.place_id).filter(m.Photo.id.in_(ch)).all()]
        updated += db.query(m.Photo).filter(m.Photo.id.in_(ch)).update(
            {m.Photo.place_id: body.place_id}, synchronize_session=False)
    _log(db, user, "place:set" if body.place_id else "place:clear", None, name,
         inverse={"op": "place_set_undo", "prior": prior})
    db.commit()
    return {"place": name, "updated": updated}


@router.get("/geocode")
def geocode_lookup(q: str, user: m.User = Depends(require_contributor)):
    """Look up coordinates for a place name (OSM Nominatim). A suggestion the
    admin confirms before saving — returns null if nothing is found."""
    return geocode(q) or {"found": False}


# ---------- place vocabulary ----------
def _next_place_id(db: Session) -> str:
    nums = [int(g.group(1)) for (pid,) in db.query(m.Place.id).all()
            if (g := re.fullmatch(r"plc(\d+)", pid))]
    return f"plc{(max(nums) + 1) if nums else 1:03d}"


def _place_record(pl: m.Place) -> dict:
    return {"id": pl.id, "name": pl.canonical_name, "region": pl.region,
            "precision": pl.precision, "lat": pl.lat, "lon": pl.lon}


@router.post("/places", response_model=PlaceOut)
def create_place(body: PlaceCreate, db: Session = Depends(get_db),
                 user: m.User = Depends(require_admin)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    if db.query(m.Place).filter(func.lower(m.Place.canonical_name) == name.lower()).first():
        raise HTTPException(409, f"place '{name}' already exists")
    pl = m.Place(id=_next_place_id(db), canonical_name=name, region=body.region,
                 precision=body.precision or "unknown", lat=body.lat, lon=body.lon)
    db.add(pl)
    db.flush()
    _log(db, user, "place:create", None, name, inverse={"op": "place_delete", "id": pl.id})
    db.commit()
    return _place_out(db, pl)


@router.patch("/places/{place_id}", response_model=PlaceOut)
def update_place(place_id: str, body: PlaceUpdate, db: Session = Depends(get_db),
                 user: m.User = Depends(require_admin)):
    """Rename and/or edit a place's region / precision / coordinates."""
    pl = db.get(m.Place, place_id)
    if pl is None:
        raise HTTPException(404, "place not found")
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name cannot be empty")
        clash = (db.query(m.Place)
                 .filter(func.lower(m.Place.canonical_name) == name.lower(),
                         m.Place.id != place_id).first())
        if clash:
            raise HTTPException(409, f"place '{name}' already exists (use merge)")
        data["name"] = name
    if not data:
        return _place_out(db, pl)
    # capture old values of exactly the fields being changed, then apply
    attr = {"name": "canonical_name"}
    old = {k: getattr(pl, attr.get(k, k)) for k in data}
    for k, v in data.items():
        setattr(pl, attr.get(k, k), v)
    field = "place:rename" if "name" in data else "place:update"
    summary = old.get("name") if "name" in data else ",".join(sorted(data))
    _log(db, user, field, summary, data.get("name", ",".join(sorted(data))),
         inverse={"op": "place_update", "id": pl.id, "old": old})
    db.commit()
    return _place_out(db, pl)


@router.post("/places/{place_id}/merge", response_model=PlaceOut)
def merge_place(place_id: str, body: PlaceMerge, db: Session = Depends(get_db),
                user: m.User = Depends(require_admin)):
    """Re-point every photo at `place_id` onto `into_id`, then delete the source."""
    src = db.get(m.Place, place_id)
    dst = db.get(m.Place, body.into_id)
    if src is None or dst is None:
        raise HTTPException(404, "place not found")
    if src.id == dst.id:
        raise HTTPException(400, "cannot merge a place into itself")
    rec = _place_record(src)
    moved = [r[0] for r in db.query(m.Photo.id).filter(m.Photo.place_id == src.id).all()]
    for ch in _chunks(moved):
        db.query(m.Photo).filter(m.Photo.id.in_(ch)).update(
            {m.Photo.place_id: dst.id}, synchronize_session=False)
    db.delete(src)
    _log(db, user, "place:merge", rec["name"], dst.canonical_name,
         inverse={"op": "place_restore", "record": rec, "photos": moved})
    db.commit()
    return _place_out(db, dst)


@router.delete("/places/{place_id}")
def delete_place(place_id: str, db: Session = Depends(get_db),
                 user: m.User = Depends(require_admin)):
    pl = db.get(m.Place, place_id)
    if pl is None:
        raise HTTPException(404, "place not found")
    rec = _place_record(pl)
    photos = [r[0] for r in db.query(m.Photo.id).filter(m.Photo.place_id == pl.id).all()]
    for ch in _chunks(photos):
        db.query(m.Photo).filter(m.Photo.id.in_(ch)).update(
            {m.Photo.place_id: None}, synchronize_session=False)
    db.delete(pl)
    _log(db, user, "place:delete", rec["name"], str(len(photos)),
         inverse={"op": "place_restore", "record": rec, "photos": photos})
    db.commit()
    return {"deleted": rec["name"], "photos_unset": len(photos)}


# ---------- per-photo image ops ----------
@router.post("/photos/{photo_id}/rotate")
def rotate_photo(photo_id: int, body: RotateReq, db: Session = Depends(get_db),
                 user: m.User = Depends(require_contributor)):
    """Rotate the slide on disk (clockwise) and regenerate its thumbnail. The
    archival masters live on Steve's Mac, so the library copy is the working one."""
    p = db.get(m.Photo, photo_id)
    if p is None:
        raise HTTPException(404, "photo not found")
    deg = body.degrees % 360
    if deg not in (90, 180, 270):
        raise HTTPException(400, "degrees must be 90, 180 or 270 (clockwise)")
    _rotate_file(p, deg)
    _log(db, user, "photo:rotate", None, f"{deg}cw", photo_id=p.id,
         inverse={"op": "photo_rotate", "photo_id": p.id, "degrees": (360 - deg) % 360})
    db.commit()
    return {"rotated": deg, "source_file": p.source_file}


@router.post("/photos/{photo_id}/caption")
def edit_caption(photo_id: int, body: CaptionReq, db: Session = Depends(get_db),
                 user: m.User = Depends(require_contributor)):
    """Edit a photo's caption (SPEC §10.7). Contributor-level, undoable."""
    p = db.get(m.Photo, photo_id)
    if p is None:
        raise HTTPException(404, "photo not found")
    old = p.caption
    p.caption = (body.caption or "").strip() or None
    _log(db, user, "photo:caption", old, p.caption, photo_id=p.id,
         inverse={"op": "photo_caption", "photo_id": p.id, "caption": old})
    db.commit()
    return {"caption": p.caption}


@router.post("/photos/{photo_id}/notes")
def edit_notes(photo_id: int, body: NotesReq, db: Session = Depends(get_db),
               user: m.User = Depends(require_contributor)):
    """Edit a photo's raw card notes. Contributor-level, undoable."""
    p = db.get(m.Photo, photo_id)
    if p is None:
        raise HTTPException(404, "photo not found")
    old = p.notes
    p.notes = (body.notes or "").strip() or None
    _log(db, user, "photo:notes", old, p.notes, photo_id=p.id,
         inverse={"op": "photo_notes", "photo_id": p.id, "notes": old})
    db.commit()
    return {"notes": p.notes}


@router.post("/people")
def create_person(body: PersonCreate, db: Session = Depends(get_db),
                  user: m.User = Depends(require_admin)):
    """Add a new person to the family tree."""
    if not re.match(r"^[a-z0-9_]+$", body.id):
        raise HTTPException(400, "id must be lowercase letters, digits, and underscores only")
    if db.get(m.Person, body.id):
        raise HTTPException(409, f"person id '{body.id}' already exists")
    for fk, val in [("father_id", body.father_id), ("mother_id", body.mother_id),
                    ("spouse_id", body.spouse_id)]:
        if val and db.get(m.Person, val) is None:
            raise HTTPException(404, f"{fk} '{val}' not found")
    person = m.Person(id=body.id, canonical_name=body.canonical_name.strip(),
                      father_id=body.father_id, mother_id=body.mother_id,
                      spouse_id=body.spouse_id, is_family=body.is_family)
    db.add(person)
    _log(db, user, "person:create", None, body.id)
    db.commit()
    return {"id": person.id, "canonical_name": person.canonical_name,
            "is_family": person.is_family}


@router.patch("/people/{person_id}")
def rename_person(person_id: str, body: PersonRename, db: Session = Depends(get_db),
                  user: m.User = Depends(require_admin)):
    """Rename a person's canonical name (undoable)."""
    person = db.get(m.Person, person_id)
    if person is None:
        raise HTTPException(404, "person not found")
    old = person.canonical_name
    person.canonical_name = body.canonical_name.strip()
    _log(db, user, "person:rename", old, person.canonical_name,
         inverse={"op": "person_rename", "person_id": person_id, "canonical_name": old})
    # Non-family toggle rides along with the basic edit (SPEC §12.7); not undoable
    # (a low-stakes, one-click-reversible flag).
    if body.is_family is not None:
        person.is_family = body.is_family
    db.commit()
    return {"id": person.id, "canonical_name": person.canonical_name,
            "is_family": person.is_family}


@router.patch("/people/{person_id}/links")
def update_person_links(person_id: str, body: PersonLinksUpdate, db: Session = Depends(get_db),
                        user: m.User = Depends(require_admin)):
    """Update a person's father/mother/spouse links (undoable). Pass null to clear a link."""
    person = db.get(m.Person, person_id)
    if person is None:
        raise HTTPException(404, "person not found")
    for fk, val in [("father_id", body.father_id), ("mother_id", body.mother_id),
                    ("spouse_id", body.spouse_id)]:
        if val and db.get(m.Person, val) is None:
            raise HTTPException(404, f"{fk} '{val}' not found")
    old = {"father_id": person.father_id, "mother_id": person.mother_id,
           "spouse_id": person.spouse_id}
    person.father_id = body.father_id
    person.mother_id = body.mother_id
    person.spouse_id = body.spouse_id
    _log(db, user, "person:links", json.dumps(old),
         json.dumps({"father_id": body.father_id, "mother_id": body.mother_id,
                     "spouse_id": body.spouse_id}),
         inverse={"op": "person_links", "person_id": person_id, **old})
    db.commit()
    return {"id": person.id, "father_id": person.father_id,
            "mother_id": person.mother_id, "spouse_id": person.spouse_id}


@router.post("/people/{person_id}/representative")
def set_representative(person_id: str, body: RepresentativeReq, db: Session = Depends(get_db),
                       user: m.User = Depends(require_admin)):
    """Set (or clear) a person's representative photo — their filter thumbnail (SPEC §9)."""
    person = db.get(m.Person, person_id)
    if person is None:
        raise HTTPException(404, "person not found")
    if body.photo_id is not None and db.get(m.Photo, body.photo_id) is None:
        raise HTTPException(404, "photo not found")
    old = person.representative_photo_id
    person.representative_photo_id = body.photo_id
    _log(db, user, "person:representative", str(old or ""), str(body.photo_id or ""),
         inverse={"op": "person_representative", "person_id": person.id, "photo_id": old})
    db.commit()
    return {"person_id": person.id, "representative_photo_id": body.photo_id}


@router.post("/people/{person_id}/face-region")
def set_face_region(person_id: str, body: FaceRegionReq, db: Session = Depends(get_db),
                    user: m.User = Depends(require_admin)):
    """Manually set a person's face crop box on a photo + make it their representative
    (SPEC §4.2) — for people ★'d on a photo with no Lightroom face region."""
    person = db.get(m.Person, person_id)
    if person is None:
        raise HTTPException(404, "person not found")
    if db.get(m.Photo, body.photo_id) is None:
        raise HTTPException(404, "photo not found")
    pp = db.get(m.PhotoPerson, (body.photo_id, person_id))
    if pp is None:
        pp = m.PhotoPerson(photo_id=body.photo_id, person_id=person_id, source=SOURCE_HUMAN)
        db.add(pp)
    prior = {"rep": person.representative_photo_id,
             "region": [pp.region_x, pp.region_y, pp.region_w, pp.region_h]}
    pp.region_x, pp.region_y, pp.region_w, pp.region_h = body.x, body.y, body.w, body.h
    person.representative_photo_id = body.photo_id
    _log(db, user, "person:face", None, person_id,
         inverse={"op": "person_face", "person_id": person_id,
                  "photo_id": body.photo_id, "prior": prior})
    db.commit()
    return {"person_id": person_id, "representative_photo_id": body.photo_id}


@router.get("/usage/stats", response_model=UsageStats)
def usage_stats(db: Session = Depends(get_db), _user: m.User = Depends(require_admin)):
    """Admin usage panel: view/download totals, most-viewed photos, active users."""
    U = m.UsageEvent
    total_views = db.query(U).filter(U.event_type == "view").count()
    total_downloads = db.query(U).filter(U.event_type == "download").count()
    top = (db.query(U.target, func.count(U.id)).filter(U.event_type == "view", U.target.isnot(None))
           .group_by(U.target).order_by(func.count(U.id).desc()).limit(12).all())
    top_photos = [UsageStat(key=t or "", label=t or "—", count=c) for t, c in top]
    # per-user views + downloads (subtotal by user, alongside the all-user totals)
    rows = (db.query(U.user_email, U.event_type, func.count(U.id))
            .group_by(U.user_email, U.event_type).all())
    tally: dict[str, dict[str, int]] = {}
    for email, etype, c in rows:
        d = tally.setdefault(email or "(unknown)", {})
        d[etype] = c
    per_user = sorted(
        (UsageUser(email=e, views=d.get("view", 0), downloads=d.get("download", 0))
         for e, d in tally.items()),
        key=lambda u: -(u.views + u.downloads))
    recent_rows = db.query(U).order_by(U.id.desc()).limit(20).all()
    recent = [f"{(r.created_at.strftime('%m-%d %H:%M') if r.created_at else '')} · "
              f"{r.user_email or '?'} · {r.event_type} · {r.target or ''}".strip()
              for r in recent_rows]
    return UsageStats(total_views=total_views, total_downloads=total_downloads,
                      top_photos=top_photos, per_user=per_user, recent=recent)


# ---------- undo ----------
def _latest_undoable(db: Session) -> m.Contribution | None:
    return (db.query(m.Contribution)
            .filter(m.Contribution.inverse.isnot(None),
                    (m.Contribution.undone.is_(None)) | (m.Contribution.undone == False))  # noqa: E712
            .order_by(m.Contribution.id.desc()).first())


def _apply_inverse(db: Session, inv: dict) -> None:
    op = inv["op"]
    if op == "event_rename":
        e = db.get(m.Event, inv["id"])
        if e:
            e.name = inv["name"]
    elif op == "event_delete":
        e = db.get(m.Event, inv["id"])
        if e:
            db.query(m.PhotoEvent).filter(m.PhotoEvent.event_id == e.id).delete(
                synchronize_session=False)
            db.delete(e)
    elif op in ("event_restore", "event_unmerge"):
        # reuse the original event id when it's free so older inverses that still
        # reference it stay valid (matches how places restore by their stable id)
        orig_id = inv.get("id") if op == "event_restore" else inv.get("src_id")
        name = inv.get("name") or inv.get("src_name")
        if orig_id is not None and db.get(m.Event, orig_id) is None:
            e = m.Event(id=orig_id, name=name)
        else:
            e = m.Event(name=name)
        db.add(e)
        db.flush()
        for pid, srcv in inv.get("rows", inv.get("src_rows", [])):
            db.add(m.PhotoEvent(photo_id=pid, event_id=e.id, source=srcv))
        for pid in inv.get("added_to_dst", []):
            db.query(m.PhotoEvent).filter(
                m.PhotoEvent.event_id == inv["dst_id"],
                m.PhotoEvent.photo_id == pid).delete(synchronize_session=False)
    elif op == "event_add_undo":
        for ch in _chunks(inv["added"]):
            db.query(m.PhotoEvent).filter(
                m.PhotoEvent.event_id == inv["event_id"],
                m.PhotoEvent.photo_id.in_(ch)).delete(synchronize_session=False)
        for pid in inv["confirmed"]:
            pe = db.query(m.PhotoEvent).filter(
                m.PhotoEvent.event_id == inv["event_id"],
                m.PhotoEvent.photo_id == pid).first()
            if pe:
                pe.source = SOURCE_AUTO
    elif op == "event_remove_undo":
        for pid, srcv in inv["rows"]:
            db.add(m.PhotoEvent(photo_id=pid, event_id=inv["event_id"], source=srcv))
    elif op == "person_add_undo":
        for ch in _chunks(inv["added"]):
            db.query(m.PhotoPerson).filter(
                m.PhotoPerson.person_id == inv["person_id"],
                m.PhotoPerson.photo_id.in_(ch)).delete(synchronize_session=False)
    elif op == "person_remove_undo":
        for pid, srcv, unc in inv["rows"]:
            db.add(m.PhotoPerson(photo_id=pid, person_id=inv["person_id"],
                                 source=srcv, uncertain=bool(unc)))
    elif op == "place_set_undo":
        for pid, old in inv["prior"]:
            db.query(m.Photo).filter(m.Photo.id == pid).update(
                {m.Photo.place_id: old}, synchronize_session=False)
    elif op == "place_delete":
        pl = db.get(m.Place, inv["id"])
        if pl:
            db.query(m.Photo).filter(m.Photo.place_id == pl.id).update(
                {m.Photo.place_id: None}, synchronize_session=False)
            db.delete(pl)
    elif op == "place_update":
        pl = db.get(m.Place, inv["id"])
        if pl:
            attr = {"name": "canonical_name"}
            for k, v in inv["old"].items():
                setattr(pl, attr.get(k, k), v)
    elif op == "place_restore":
        rec = inv["record"]
        if db.get(m.Place, rec["id"]) is None:
            db.add(m.Place(id=rec["id"], canonical_name=rec["name"], region=rec["region"],
                           precision=rec["precision"], lat=rec["lat"], lon=rec["lon"]))
            db.flush()
        for ch in _chunks(inv["photos"]):
            db.query(m.Photo).filter(m.Photo.id.in_(ch)).update(
                {m.Photo.place_id: rec["id"]}, synchronize_session=False)
    elif op == "photo_rotate":
        p = db.get(m.Photo, inv["photo_id"])
        if p and inv["degrees"] in (90, 180, 270):
            _rotate_file(p, inv["degrees"])
    elif op == "photo_caption":
        p = db.get(m.Photo, inv["photo_id"])
        if p:
            p.caption = inv["caption"]
    elif op == "photo_notes":
        p = db.get(m.Photo, inv["photo_id"])
        if p:
            p.notes = inv["notes"]
    elif op == "person_rename":
        person = db.get(m.Person, inv["person_id"])
        if person:
            person.canonical_name = inv["canonical_name"]
    elif op == "person_links":
        person = db.get(m.Person, inv["person_id"])
        if person:
            person.father_id = inv["father_id"]
            person.mother_id = inv["mother_id"]
            person.spouse_id = inv["spouse_id"]
    elif op == "person_representative":
        person = db.get(m.Person, inv["person_id"])
        if person:
            person.representative_photo_id = inv["photo_id"]
    elif op == "person_face":
        person = db.get(m.Person, inv["person_id"])
        if person:
            person.representative_photo_id = inv["prior"]["rep"]
        pp = db.get(m.PhotoPerson, (inv["photo_id"], inv["person_id"]))
        if pp:
            pp.region_x, pp.region_y, pp.region_w, pp.region_h = inv["prior"]["region"]
    else:
        raise HTTPException(400, f"don't know how to undo: {op}")


@router.get("/undo/peek")
def undo_peek(db: Session = Depends(get_db), user: m.User = Depends(require_admin)):
    c = _latest_undoable(db)
    if c is None:
        return {"available": False}
    return {"available": True, "field": c.field, "old": c.old_value, "new": c.new_value}


@router.post("/undo")
def undo(db: Session = Depends(get_db), user: m.User = Depends(require_admin)):
    c = _latest_undoable(db)
    if c is None:
        raise HTTPException(404, "nothing to undo")
    _apply_inverse(db, json.loads(c.inverse))
    c.undone = True
    db.commit()
    return {"undone": c.field, "old": c.old_value, "new": c.new_value}


# ---- users / invites (SPEC §6.3, §10.5) -----------------------------------
# "Invite" = add the email to the Cloudflare Access allowlist (done in Cloudflare)
# + register the row here with a role and an optional person link for per-viewer
# People rooting. User ops are admin-only and are NOT undoable (undo is content-only).
_VALID_ROLES = {"admin", "contributor", "viewer"}


def _user_out(u: m.User) -> UserOut:
    return UserOut(id=u.id, email=u.email, display_name=u.display_name, role=u.role,
                   person_id=u.person_id, invited_at=u.invited_at, last_login=u.last_login)


def _check_role(role: str) -> None:
    if role not in _VALID_ROLES:
        raise HTTPException(400, f"invalid role: {role}")


def _check_person(db: Session, person_id: str | None) -> None:
    if person_id and db.get(m.Person, person_id) is None:
        raise HTTPException(400, f"no such person: {person_id}")


def _admin_count(db: Session) -> int:
    return db.query(func.count(m.User.id)).filter(m.User.role == "admin").scalar() or 0


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), user: m.User = Depends(require_admin)):
    users = db.query(m.User).order_by(m.User.email).all()
    return [_user_out(u) for u in users]


@router.post("/users", response_model=UserOut)
def create_user(body: UserCreate, db: Session = Depends(get_db),
                user: m.User = Depends(require_admin)):
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(400, "email required")
    if db.query(m.User).filter(func.lower(m.User.email) == email).first():
        raise HTTPException(409, f"user already exists: {email}")
    _check_role(body.role)
    _check_person(db, body.person_id)
    u = m.User(email=email, role=body.role, person_id=body.person_id,
               display_name=body.display_name, invited_at=datetime.now(timezone.utc))
    db.add(u)
    _log(db, user, "user:create", None, f"{email} ({body.role})")
    db.commit()
    db.refresh(u)
    return _user_out(u)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db),
                user: m.User = Depends(require_admin)):
    u = db.get(m.User, user_id)
    if u is None:
        raise HTTPException(404, "no such user")
    data = body.model_dump(exclude_unset=True)
    if "role" in data and data["role"] is not None:
        _check_role(data["role"])
        # Don't let the last admin demote themselves out of admin access.
        if u.role == "admin" and data["role"] != "admin" and _admin_count(db) <= 1:
            raise HTTPException(400, "cannot demote the last admin")
    if "person_id" in data:
        _check_person(db, data["person_id"])
    before = f"{u.role}/{u.person_id}/{u.display_name}"
    for k, v in data.items():
        setattr(u, k, v)
    _log(db, user, "user:update", before, f"{u.role}/{u.person_id}/{u.display_name}")
    db.commit()
    db.refresh(u)
    return _user_out(u)


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db),
                user: m.User = Depends(require_admin)):
    u = db.get(m.User, user_id)
    if u is None:
        raise HTTPException(404, "no such user")
    if u.id == user.id:
        raise HTTPException(400, "cannot delete your own account")
    if u.role == "admin" and _admin_count(db) <= 1:
        raise HTTPException(400, "cannot delete the last admin")
    _log(db, user, "user:delete", f"{u.email} ({u.role})", None)
    db.delete(u)
    db.commit()
    return {"deleted": u.email}
