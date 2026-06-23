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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from PIL import Image

from app import models as m
from app.auth import require_admin
from app.config import settings
from app.database import get_db
from app.geocoding import geocode
from app.models import SOURCE_AUTO, SOURCE_HUMAN
from app.routers.images import THUMB_MAX, _slide_path
from app.schemas import (
    BulkEventReq, BulkPersonReq, BulkPlaceReq,
    EventCreate, EventMerge, EventOut, EventRename,
    PlaceCreate, PlaceMerge, PlaceOut, PlaceUpdate, RotateReq,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

_CHUNK = 500  # keep SQLite IN(...) under its ~999 bound-variable limit


def _chunks(seq):
    for i in range(0, len(seq), _CHUNK):
        yield seq[i:i + _CHUNK]


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
    """Rotate the slide on disk (clockwise) and regenerate its thumbnail."""
    rot_map = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
               270: Image.Transpose.ROTATE_90}
    path = _slide_path(p.source_file)
    with Image.open(path) as im:
        im.load()
        rot = im.transpose(rot_map[deg])
    rot.save(path, "JPEG", quality=95)
    thumb = settings.thumbnails_dir / p.source_file
    thumb.parent.mkdir(parents=True, exist_ok=True)
    t = rot.copy()
    t.thumbnail((THUMB_MAX, THUMB_MAX))
    t.convert("RGB").save(thumb, "JPEG", quality=82)


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
               user: m.User = Depends(require_admin)):
    e = db.get(m.Event, body.event_id)
    if e is None:
        raise HTTPException(404, "event not found")
    ids = body.photo_ids
    if not ids:
        raise HTTPException(400, "no photos selected")

    if body.op == "add":
        existing = set()
        for ch in _chunks(ids):
            existing |= {r[0] for r in db.query(m.PhotoEvent.photo_id).filter(
                m.PhotoEvent.event_id == e.id, m.PhotoEvent.photo_id.in_(ch)).all()}
        added, confirmed = [], []
        for pid in ids:
            if pid in existing:
                pe = db.query(m.PhotoEvent).filter(
                    m.PhotoEvent.event_id == e.id, m.PhotoEvent.photo_id == pid).first()
                if pe and pe.source != SOURCE_HUMAN:
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
                user: m.User = Depends(require_admin)):
    person = db.get(m.Person, body.person_id)
    if person is None:
        raise HTTPException(404, "person not found")
    ids = body.photo_ids
    if not ids:
        raise HTTPException(400, "no photos selected")

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
               user: m.User = Depends(require_admin)):
    """Set (or clear, if place_id is null) the place on a set of photos."""
    ids = body.photo_ids
    if not ids:
        raise HTTPException(400, "no photos selected")
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
def geocode_lookup(q: str, user: m.User = Depends(require_admin)):
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
                 user: m.User = Depends(require_admin)):
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
