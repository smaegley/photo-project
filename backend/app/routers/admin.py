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
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from PIL import Image

from app import derivatives, models as m, storage
from app.auth import require_admin, require_contributor
from app.config import settings
from app.database import get_db
from app.geocoding import geocode
from app.import_photos import _km, REVIEW_DIR  # same proximity rule the importer uses (SPEC §12.6)
from app.queries import thumb_url as _thumb_url, _version as _photo_version
from app.models import SOURCE_AUTO, SOURCE_HUMAN
from app.routers.images import photo_file, _safe_key
from app.schemas import (
    BulkEventReq, BulkPersonReq, BulkPlaceReq, CaptionReq, NotesReq,
    EventCreate, EventMerge, EventOut, EventRename,
    FaceRegionReq, PersonCreate, PersonLinksUpdate, PersonMerge, PersonRename, PlaceCreate, PlaceMerge, PlaceOut, PlaceUpdate,
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


def _rotate_regions(db: Session, p: m.Photo, deg: int) -> None:
    """Rotate every face box on this photo to match the pixels (SPEC §14).

    `photo_person.region_*` and `face.x/y/w/h` are normalized to the image as it is
    stored. Rotating the pixels without rotating the boxes leaves every face outline
    pointing somewhere else — silently, since nothing re-checks them. That did not matter
    before there were face boxes; it matters now that 1,150 local photos carry them.

    Clockwise, on normalized centre coords: 90° (x,y)->(1-y,x); 180° ->(1-x,1-y);
    270° ->(y,1-x). Width and height swap for the quarter turns.
    """
    def turn(x, y, w, h):
        if deg == 90:
            return 1.0 - y, x, h, w
        if deg == 180:
            return 1.0 - x, 1.0 - y, w, h
        return y, 1.0 - x, h, w          # 270

    for pp in db.query(m.PhotoPerson).filter(m.PhotoPerson.photo_id == p.id,
                                             m.PhotoPerson.region_w.isnot(None)).all():
        pp.region_x, pp.region_y, pp.region_w, pp.region_h = turn(
            pp.region_x, pp.region_y, pp.region_w, pp.region_h)
    for f in db.query(m.Face).filter(m.Face.photo_id == p.id).all():
        f.x, f.y, f.w, f.h = turn(f.x, f.y, f.w, f.h)


def _rotate_file(p: m.Photo, deg: int) -> None:
    """Rotate the slide on disk (clockwise) and refresh its cached derivatives."""
    rot_map = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
               270: Image.Transpose.ROTATE_90}
    path = photo_file(p)
    with Image.open(path) as im:
        im.load()
        rot = im.transpose(rot_map[deg])
    rot.save(path, "JPEG", quality=95)
    # The master's bytes just changed, so restamp the change-token: image URLs are
    # served `immutable`, and ?v= now comes from this column rather than a live stat
    # (SPEC §13.4) — leaving it stale would pin the old orientation in every browser
    # that had already loaded the photo. The caller's commit persists it.
    p.file_version = storage.probe_version(p)
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


# ---------- face matching queue (SPEC §14.7) ----------
@router.get("/face-queue")
def face_queue(db: Session = Depends(get_db), user: m.User = Depends(require_admin)):
    """People with pending face suggestions, most to review first.

    `backfill` counts suggestions where the person is *already* tagged on that photo, so
    confirming only adds geometry — near-zero risk, safe to accept in bulk. The remainder
    are genuinely new tags and deserve a closer look (§14 slice 4)."""
    rows = (db.query(m.FaceSuggestion.person_id, m.Person.canonical_name,
                     func.count(m.FaceSuggestion.id), func.avg(m.FaceSuggestion.score))
            .join(m.Person, m.Person.id == m.FaceSuggestion.person_id)
            .filter(m.FaceSuggestion.status == m.FACE_PENDING)
            .group_by(m.FaceSuggestion.person_id, m.Person.canonical_name).all())
    tagged = {(pid, per) for pid, per in
              db.query(m.PhotoPerson.photo_id, m.PhotoPerson.person_id).all()}
    face_photo = dict(db.query(m.Face.id, m.Face.photo_id).all())
    backfill = defaultdict(int)
    for fid, per in db.query(m.FaceSuggestion.face_id, m.FaceSuggestion.person_id).filter(
            m.FaceSuggestion.status == m.FACE_PENDING).all():
        if (face_photo.get(fid), per) in tagged:
            backfill[per] += 1
    return sorted(
        [{"person_id": p, "name": n, "pending": c, "avg_score": round(a or 0, 3),
          "backfill": backfill.get(p, 0), "new": c - backfill.get(p, 0)}
         for p, n, c, a in rows],
        key=lambda r: -r["pending"])


# NOTE: this and /accepted/summary MUST stay above `/face-queue/{person_id}`. FastAPI
# matches routes in declaration order, so with the catch-all first, a request for
# /face-queue/accepted binds person_id="accepted" and quietly returns an empty list —
# a 200 with no data, which looks like "nothing to audit" rather than a routing bug.
def _face_tag_query(db, person_id=None, max_score=None, max_area=None):
    """Every face-boxed tag, with its confidence where one exists.

    One query behind the whole audit. `By confidence` and `Tiny faces` were two views of
    this list differing only in filter and sort, which meant a tag could be missed by
    whichever lens you happened to be using.

    **Score is LEFT-joined and nullable on purpose.** Only tags created by accepting a
    suggestion have one; tags imported from the Lightroom catalog never do — and those
    are exactly where the worst mis-IDs came from, so filtering them out by requiring a
    score would hide the problem. The join is on (photo, person), which is exact, rather
    than on box geometry, which is not: a tag's box and a detector's box are independent.
    """
    area = m.PhotoPerson.region_w * m.PhotoPerson.region_h
    sub = (db.query(m.FaceSuggestion.person_id.label("pid"),
                    m.Face.photo_id.label("ph"),
                    func.min(m.FaceSuggestion.score).label("score"))
           .join(m.Face, m.Face.id == m.FaceSuggestion.face_id)
           .filter(m.FaceSuggestion.status == m.FACE_ACCEPTED)
           .group_by(m.FaceSuggestion.person_id, m.Face.photo_id).subquery())
    q = (db.query(m.PhotoPerson.photo_id, m.PhotoPerson.person_id,
                  m.PhotoPerson.region_x, m.PhotoPerson.region_y,
                  m.PhotoPerson.region_w, m.PhotoPerson.region_h,
                  m.Person.canonical_name, m.Photo.source_file, m.Photo.date_start,
                  sub.c.score, area.label("area"))
         .join(m.Person, m.Person.id == m.PhotoPerson.person_id)
         .join(m.Photo, m.Photo.id == m.PhotoPerson.photo_id)
         .outerjoin(sub, (sub.c.pid == m.PhotoPerson.person_id)
                    & (sub.c.ph == m.PhotoPerson.photo_id))
         .filter(m.PhotoPerson.region_w.isnot(None)))
    if person_id:
        q = q.filter(m.PhotoPerson.person_id == person_id)
    if max_area is not None:
        q = q.filter(area <= max_area)
    if max_score is not None:
        q = q.filter(sub.c.score <= max_score)
    return q, area


@router.get("/face-tags")
def face_tags(person_id: str | None = None, max_score: float | None = None,
              max_area: float | None = None, sort: str = "area", limit: int = 500,
              offset: int = 0,
              db: Session = Depends(get_db), user: m.User = Depends(require_admin)):
    """The unified audit list — filter by person / score / size, sort by size or score.

    Paged. The grid renders one image request per row, so returning 10,000 at once would
    stall the browser; `offset` lets the panel append a page at a time while the sidebar
    counts show the true total, so a cap can never silently hide work.
    """
    q, area = _face_tag_query(db, person_id, max_score, max_area)
    q = q.order_by(area.asc() if sort == "area"
                   else m.PhotoPerson.region_w.asc() if sort == "width"
                   else _nulls_last_score())
    rows = q.offset(max(0, offset)).limit(limit).all()
    return [{"photo_id": ph, "person_id": pid, "person": pname,
             "area": round(a, 6), "score": round(sc, 3) if sc is not None else None,
             "origin": "suggested" if sc is not None else "imported",
             "source_file": sf, "year": dt.year if dt else None,
             "box": [round(rx, 5), round(ry, 5), round(rw, 5), round(rh, 5)]}
            for ph, pid, rx, ry, rw, rh, pname, sf, dt, sc, a in rows]


def _nulls_last_score():
    from sqlalchemy import literal_column
    return literal_column("score IS NULL, score ASC")


@router.get("/face-tags/by-person")
def face_tags_by_person(max_score: float | None = None, max_area: float | None = None,
                        db: Session = Depends(get_db), user: m.User = Depends(require_admin)):
    """Per-person counts under the SAME filters, so the sidebar always agrees with the grid."""
    q, _area = _face_tag_query(db, None, max_score, max_area)
    counts = {}
    for row in q.all():
        pid, pname = row[1], row[6]
        e = counts.setdefault(pid, {"person_id": pid, "name": pname, "count": 0,
                                    "smallest": 1.0, "imported": 0})
        e["count"] += 1
        e["smallest"] = min(e["smallest"], row[10])
        if row[9] is None:
            e["imported"] += 1
    return sorted(counts.values(), key=lambda r: -r["count"])


@router.get("/face-queue/tiny-tags")
def tiny_tags(max_area: float = 0.003, limit: int = 400, person_id: str | None = None,
              db: Session = Depends(get_db), user: m.User = Depends(require_admin)):
    """Every face-boxed tag whose face is tiny, smallest first — regardless of origin.

    The accepted-suggestion audit only sees tags this app created. It misses the other
    two sources entirely: tags imported from the Lightroom catalog (`import_faces`) and
    tags applied by naming a cluster. A real example — photo 6388, a crowd scene with 19
    detected faces, carried "Mary Emma Beck" and "Brendan Lefkowicz" on ~2%-wide boxes
    straight from LR, with no suggestion behind either.

    **Face size is the origin-independent signal.** Steve's rejections measured 6× smaller
    than his accepts, and a box under ~2% of frame width is a background face, a
    reflection or a photo-of-a-photo far more often than it is a person worth tagging.
    """
    rows = (db.query(m.PhotoPerson.photo_id, m.PhotoPerson.person_id,
                     m.PhotoPerson.region_x, m.PhotoPerson.region_y,
                     m.PhotoPerson.region_w, m.PhotoPerson.region_h,
                     m.Person.canonical_name, m.Photo.source_file, m.Photo.date_start)
            .join(m.Person, m.Person.id == m.PhotoPerson.person_id)
            .join(m.Photo, m.Photo.id == m.PhotoPerson.photo_id)
            .filter(m.PhotoPerson.region_w.isnot(None),
                    (m.PhotoPerson.region_w * m.PhotoPerson.region_h) <= max_area)
            .order_by((m.PhotoPerson.region_w * m.PhotoPerson.region_h).asc()))
    if person_id:
        rows = rows.filter(m.PhotoPerson.person_id == person_id)
    rows = rows.limit(limit).all()
    out = []
    for ph, pid, rx, ry, rw, rh, pname, sf, dt in rows:
        # find the detected face that matches this box, so the UI can crop it
        f = (db.query(m.Face.id).filter(m.Face.photo_id == ph,
                                        m.Face.x > rx - 1e-4, m.Face.x < rx + 1e-4).first())
        out.append({"photo_id": ph, "person_id": pid, "person": pname,
                    "face_id": f[0] if f else None,
                    "area": round(rw * rh, 6), "source_file": sf,
                    "year": dt.year if dt else None,
                    "box": [round(rx, 5), round(ry, 5), round(rw, 5), round(rh, 5)]})
    return out


@router.get("/face-queue/tiny-tags/by-person")
def tiny_tags_by_person(max_area: float = 0.003, db: Session = Depends(get_db),
                        user: m.User = Depends(require_admin)):
    """How many tiny-boxed tags each person has. Reviewing one person at a time is far
    easier than a mixed grid: you hold one face in your head instead of forty."""
    rows = (db.query(m.PhotoPerson.person_id, m.Person.canonical_name,
                     func.count(m.PhotoPerson.photo_id),
                     func.min(m.PhotoPerson.region_w * m.PhotoPerson.region_h))
            .join(m.Person, m.Person.id == m.PhotoPerson.person_id)
            .filter(m.PhotoPerson.region_w.isnot(None),
                    (m.PhotoPerson.region_w * m.PhotoPerson.region_h) <= max_area)
            .group_by(m.PhotoPerson.person_id, m.Person.canonical_name).all())
    return sorted([{"person_id": p, "name": n, "count": c, "smallest": round(sm or 0, 6)}
                   for p, n, c, sm in rows], key=lambda r: -r["count"])


@router.post("/face-queue/tiny-tags/remove")
def remove_tiny_tags(body: dict, db: Session = Depends(get_db),
                     user: m.User = Depends(require_admin)):
    """Drop specific (photo, person) tags. Undoable, and restores the box on undo."""
    pairs = [(int(a), str(b)) for a, b in body.get("pairs", [])]
    if not pairs:
        raise HTTPException(400, "no pairs")
    removed = []
    for ph, pid in pairs:
        pp = db.get(m.PhotoPerson, (ph, pid))
        if pp is None:
            continue
        removed.append([ph, pid, [pp.region_x, pp.region_y, pp.region_w, pp.region_h],
                        pp.source, bool(pp.uncertain)])
        db.delete(pp)
    _log(db, user, "face:untag", None, f"{len(removed)} tags",
         inverse={"op": "face_untag", "removed": removed})
    db.commit()
    return {"removed": len(removed)}


@router.get("/face-queue/accepted")
def accepted_faces(person_id: str | None = None, max_score: float = 1.0,
                   limit: int = 400, db: Session = Depends(get_db),
                   user: m.User = Depends(require_admin)):
    """Already-accepted suggestions, **worst score first** — for auditing bulk accepts.

    Exists because "Select all → Accept" on a scrolling grid commits faces the reviewer
    never saw: single accepts of 105, 105 and 102 happened against a grid showing ~3
    rows. Score is the best available proxy for which of those are wrong, and it was
    recorded at match time, so the damage is reviewable after the fact rather than lost.
    """
    q = (db.query(m.FaceSuggestion.id, m.FaceSuggestion.face_id, m.FaceSuggestion.score,
                  m.FaceSuggestion.person_id, m.Person.canonical_name,
                  m.Face.photo_id, m.Face.x, m.Face.y, m.Face.w, m.Face.h,
                  m.Photo.source_file, m.Photo.date_start)
         .join(m.Face, m.Face.id == m.FaceSuggestion.face_id)
         .join(m.Photo, m.Photo.id == m.Face.photo_id)
         .join(m.Person, m.Person.id == m.FaceSuggestion.person_id)
         .filter(m.FaceSuggestion.status == m.FACE_ACCEPTED,
                 m.FaceSuggestion.score <= max_score))
    if person_id:
        q = q.filter(m.FaceSuggestion.person_id == person_id)
    rows = q.order_by(m.FaceSuggestion.score.asc()).limit(limit).all()
    return [{"suggestion_id": sid, "face_id": fid, "score": round(sc, 3),
             "person_id": pid, "person": pname, "photo_id": ph,
             "source_file": sf, "year": dt.year if dt else None,
             "box": [round(x, 5), round(y, 5), round(w, 5), round(h, 5)]}
            for sid, fid, sc, pid, pname, ph, x, y, w, h, sf, dt in rows]


@router.get("/face-queue/accepted/summary")
def accepted_summary(db: Session = Depends(get_db), user: m.User = Depends(require_admin)):
    buckets = []
    for lo, hi in ((0.0, 0.50), (0.50, 0.55), (0.55, 0.60), (0.60, 1.01)):
        n = (db.query(func.count(m.FaceSuggestion.id))
             .filter(m.FaceSuggestion.status == m.FACE_ACCEPTED,
                     m.FaceSuggestion.score >= lo, m.FaceSuggestion.score < hi).scalar() or 0)
        buckets.append({"from": lo, "to": hi, "count": n})
    return {"buckets": buckets}


@router.get("/face-queue/{person_id}")
def face_queue_person(person_id: str, limit: int = 300, db: Session = Depends(get_db),
                      user: m.User = Depends(require_admin)):
    """The pending suggestions for one person, highest confidence first."""
    rows = (db.query(m.FaceSuggestion.id, m.FaceSuggestion.face_id, m.FaceSuggestion.score,
                     m.Face.photo_id, m.Photo.source_file, m.Photo.date_start,
                     m.Face.x, m.Face.y, m.Face.w, m.Face.h)
            .join(m.Face, m.Face.id == m.FaceSuggestion.face_id)
            .join(m.Photo, m.Photo.id == m.Face.photo_id)
            .filter(m.FaceSuggestion.person_id == person_id,
                    m.FaceSuggestion.status == m.FACE_PENDING)
            .order_by(m.FaceSuggestion.score.desc()).limit(limit).all())
    tagged = {pid for (pid,) in db.query(m.PhotoPerson.photo_id)
              .filter(m.PhotoPerson.person_id == person_id).all()}
    return [{"suggestion_id": sid, "face_id": fid, "score": round(sc, 3),
             "photo_id": pid, "source_file": sf,
             "year": dt.year if dt else None,
             "backfill": pid in tagged,
             # normalized centre+size, so the preview can outline WHICH face this is —
             # adjacent faces otherwise both land inside the crop and are ambiguous
             "box": [round(x, 5), round(y, 5), round(w, 5), round(h, 5)]}
            for sid, fid, sc, pid, sf, dt, x, y, w, h in rows]


@router.post("/face-suggestions/decide")
def decide_suggestions(body: dict, db: Session = Depends(get_db),
                       user: m.User = Depends(require_admin)):
    """Accept or reject suggestions in bulk.

    Accepting writes an ordinary `photo_person` row with the face box — after which it is
    indistinguishable from a hand-made tag, which is the whole point (§14.1). Rejections
    are *kept*, not deleted, so `match_faces` never re-offers the same wrong guess.
    One undoable contribution per call rather than per face, so a 200-face accept is one
    click to undo."""
    ids = [int(i) for i in body.get("suggestion_ids", [])]
    action = (body.get("action") or "").lower()
    if action == "unaccept":
        # Undo a bad bulk accept after the fact. The undo stack is serial and long gone
        # by the time a mis-ID surfaces in the People filter, so this is targeted: revert
        # these specific suggestions to pending and drop the tag each one created.
        ids2 = [int(i) for i in body.get("suggestion_ids", [])]
        if not ids2:
            raise HTTPException(400, "no suggestion_ids")
        sugs2 = db.query(m.FaceSuggestion).filter(
            m.FaceSuggestion.id.in_(ids2),
            m.FaceSuggestion.status == m.FACE_ACCEPTED).all()
        removed = []
        for sg in sugs2:
            f = db.get(m.Face, sg.face_id)
            sg.status, sg.decided_by, sg.decided_at = m.FACE_PENDING, None, None
            if not f:
                continue
            pp = db.get(m.PhotoPerson, (f.photo_id, sg.person_id))
            # Only drop a tag whose box matches this face — never one the human made by
            # hand or that came from the Lightroom import.
            if pp is not None and pp.region_w is not None and abs((pp.region_x or 0) - f.x) < 1e-6:
                db.delete(pp)
                removed.append([f.photo_id, sg.person_id,
                                [f.x, f.y, f.w, f.h]])
        _log(db, user, "face:unaccept", None, f"{len(sugs2)} faces",
             inverse={"op": "face_unaccept", "suggestion_ids": [s.id for s in sugs2],
                      "removed": removed})
        db.commit()
        return {"reverted": len(sugs2), "tags_removed": len(removed)}
    if action not in ("accept", "reject"):
        raise HTTPException(400, "action must be accept, reject or unaccept")
    if not ids:
        raise HTTPException(400, "no suggestion_ids")

    sugs = db.query(m.FaceSuggestion).filter(
        m.FaceSuggestion.id.in_(ids), m.FaceSuggestion.status == m.FACE_PENDING).all()
    now = datetime.now(timezone.utc)
    added = []
    # `photo_person` is unique on (photo_id, person_id), and a batch can legitimately
    # contain TWO faces of the same person in one photo — a mis-detection, a reflection,
    # a photo-of-a-photo. `db.get` cannot see a row added earlier in this same
    # transaction, so checking it alone let a duplicate through and the whole accept
    # failed with an IntegrityError. Track what this request has queued as well.
    # Best-scoring face first, so if two compete for one slot the stronger box wins.
    queued: set[tuple[int, str]] = set()
    for sg in sorted(sugs, key=lambda x: -x.score):
        sg.status = m.FACE_ACCEPTED if action == "accept" else m.FACE_REJECTED
        sg.decided_by, sg.decided_at = user.email, now
        if action != "accept":
            continue
        f = db.get(m.Face, sg.face_id)
        if not f:
            continue
        key = (f.photo_id, sg.person_id)
        if key in queued:
            continue          # a better-scoring face in this batch already took the slot
        pp = db.get(m.PhotoPerson, key)
        if pp is None:
            db.add(m.PhotoPerson(photo_id=f.photo_id, person_id=sg.person_id,
                                 source=SOURCE_HUMAN, uncertain=False,
                                 region_x=f.x, region_y=f.y, region_w=f.w, region_h=f.h))
            added.append([f.photo_id, sg.person_id, True])
            queued.add(key)
        elif pp.region_w is None:
            pp.region_x, pp.region_y, pp.region_w, pp.region_h = f.x, f.y, f.w, f.h
            added.append([f.photo_id, sg.person_id, False])
    _log(db, user, f"face:{action}", None, f"{len(sugs)} faces",
         inverse={"op": "face_decide", "suggestion_ids": [s.id for s in sugs],
                  "added": added})
    db.commit()
    return {"decided": len(sugs), "tags_added": sum(1 for a in added if a[2]),
            "regions_filled": sum(1 for a in added if not a[2])}


@router.get("/face-clusters/{cluster_id}/faces")
def face_cluster_faces(cluster_id: int, limit: int = 400, db: Session = Depends(get_db),
                       user: m.User = Depends(require_admin)):
    """Every face in a cluster, so a mixed group can be split rather than accepted whole.

    Clustering groups by *appearance*, which is not the same as identity — two different
    dogs land together because an ArcFace model maps anything non-human into a similar
    out-of-domain corner. Whole-cluster decisions alone would force naming both as one
    animal or ignoring both."""
    rows = (db.query(m.Face.id, m.Face.det_score, m.Face.w, m.Face.h,
                     m.Photo.source_file, m.Photo.date_start, m.Face.x, m.Face.y)
            .join(m.Photo, m.Photo.id == m.Face.photo_id)
            .filter(m.Face.cluster_id == cluster_id)
            .order_by((m.Face.w * m.Face.h).desc()).limit(limit).all())
    return [{"face_id": fid, "det_score": round(ds or 0, 3),
             "area": round(w * h, 5), "source_file": sf,
             "year": dt.year if dt else None,
             "box": [round(x, 5), round(y, 5), round(w, 5), round(h, 5)]}
            for fid, ds, w, h, sf, dt, x, y in rows]


@router.get("/faces/unnamed")
def face_unnamed(min_area: float = 0.004, min_score: float = 0.70,
                 include_ignored: bool = False, limit: int = 300, offset: int = 0,
                 db: Session = Depends(get_db), _user: m.User = Depends(require_admin)):
    """Good faces nobody has named — the pile the matcher structurally cannot surface.

    A suggestion needs a person model to match against, so anyone with fewer than
    `MIN_REFS` references is invisible to it, and so is anyone not in the archive at all.
    Those faces fall below threshold and vanish into clustering. This asks the
    origin-independent question instead — *is this a good face with no name on it?* —
    ranked by area x det_score so the most worthwhile come first.

    Excludes faces already carrying a confirmed region and those queued as a pending
    suggestion (they belong to that review, not this one). Ignored faces are excluded by
    default but can be brought back, since a better model may have changed the answer.
    """
    regions: dict[int, list] = {}
    for pid, rx, ry, rw, rh in (
            db.query(m.PhotoPerson.photo_id, m.PhotoPerson.region_x, m.PhotoPerson.region_y,
                     m.PhotoPerson.region_w, m.PhotoPerson.region_h)
            .filter(m.PhotoPerson.region_w.isnot(None)).all()):
        regions.setdefault(pid, []).append(
            (rx - rw / 2, ry - rh / 2, rx + rw / 2, ry + rh / 2))

    # Everyone already on each photo, boxed or not. The reviewer needs this to answer
    # "is she already in here, and where?" before naming a face — otherwise a second,
    # redundant tag looks like the right move when it isn't.
    tagged_by_photo: dict[int, list] = {}
    for pid, person, name, rx, ry, rw, rh in (
            db.query(m.PhotoPerson.photo_id, m.PhotoPerson.person_id,
                     m.Person.canonical_name, m.PhotoPerson.region_x,
                     m.PhotoPerson.region_y, m.PhotoPerson.region_w, m.PhotoPerson.region_h)
            .join(m.Person, m.Person.id == m.PhotoPerson.person_id).all()):
        tagged_by_photo.setdefault(pid, []).append({
            "person_id": person, "name": name,
            "box": ([round(rx, 5), round(ry, 5), round(rw, 5), round(rh, 5)]
                    if rw is not None else None)})

    pending = {fid for (fid,) in db.query(m.FaceSuggestion.face_id)
               .filter(m.FaceSuggestion.status == m.FACE_PENDING).all()}
    ignored = {fid for (fid,) in db.query(m.Face.id)
               .join(m.FaceCluster, m.Face.cluster_id == m.FaceCluster.id)
               .filter(m.FaceCluster.status == m.CLUSTER_IGNORED).all()}

    def overlaps(box, gs):
        for g in gs:
            ix1, iy1 = max(box[0], g[0]), max(box[1], g[1])
            ix2, iy2 = min(box[2], g[2]), min(box[3], g[3])
            inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
            ua = ((box[2] - box[0]) * (box[3] - box[1])
                  + (g[2] - g[0]) * (g[3] - g[1]) - inter)
            if ua > 0 and inter / ua > 0.3:
                return True
        return False

    rows = (db.query(m.Face.id, m.Face.photo_id, m.Face.x, m.Face.y, m.Face.w, m.Face.h,
                     m.Face.det_score, m.Face.detector_version, m.Face.embedding,
                     m.Photo.source_file, m.Photo.date_start, m.Photo.origin)
            .join(m.Photo, m.Photo.id == m.Face.photo_id)
            .filter(m.Face.det_score >= min_score,
                    (m.Face.w * m.Face.h) >= min_area).all())

    out, embs = [], []
    for fid, pid, x, y, w, h, ds, dv, blob, sf, dt, origin in rows:
        if fid in pending:
            continue
        if fid in ignored and not include_ignored:
            continue
        if overlaps((x - w / 2, y - h / 2, x + w / 2, y + h / 2), regions.get(pid, ())):
            continue
        # `boxed_here` is the reason most of these are unnamed at all: the matcher drops a
        # suggestion when its best person already has a box on that photo, so a sibling's
        # face lands here nameless. It also flags the interesting case — if this really is
        # that person, their existing box is on the wrong face.
        out.append({"face_id": fid, "photo_id": pid, "source_file": sf,
                    "det_score": round(ds or 0, 3), "area": round(w * h, 5),
                    "year": dt.year if dt else None, "origin": origin,
                    "hires": bool(dv and "@" in dv),
                    "ignored": fid in ignored,
                    "tagged": tagged_by_photo.get(pid, []),
                    "box": [round(x, 5), round(y, 5), round(w, 5), round(h, 5)]})
        embs.append(blob)

    _annotate_best_match(db, out, embs)
    out.sort(key=lambda r: -(r["area"] * r["det_score"]))
    return {"total": len(out), "items": out[offset:offset + limit]}


def _annotate_best_match(db: Session, out: list[dict], embs: list) -> None:
    """Add 'looks like <person> · <score>' to each unnamed face.

    Same centroids the matcher uses, so the number on the tile is the number that kept
    the face out of the queue. It is most often a *sibling* — that resemblance is exactly
    what the model cannot resolve — so it is a hint about which family the face belongs
    to, never an answer.
    """
    import numpy as np
    from app.match_faces import (EMB_DIM, IOU_LINK, MAX_SUB, MIN_REFS, SUB_PER,
                                 _box, _iou, _kmeans, _unit)

    faces = db.query(m.Face.id, m.Face.photo_id, m.Face.x, m.Face.y,
                     m.Face.w, m.Face.h, m.Face.embedding).all()
    if not faces:
        return
    emb = np.zeros((len(faces), EMB_DIM), dtype=np.float32)
    by_photo: dict[int, list] = {}
    for i, f in enumerate(faces):
        if f[6]:
            emb[i] = np.frombuffer(f[6], dtype=np.float32)
        by_photo.setdefault(f[1], []).append((i, _box(f[2], f[3], f[4], f[5])))

    refs: dict[str, list] = {}
    for pid, person, rx, ry, rw, rh in (
            db.query(m.PhotoPerson.photo_id, m.PhotoPerson.person_id,
                     m.PhotoPerson.region_x, m.PhotoPerson.region_y,
                     m.PhotoPerson.region_w, m.PhotoPerson.region_h)
            .filter(m.PhotoPerson.region_w.isnot(None)).all()):
        g = _box(rx, ry, rw, rh)
        best_i, best = None, IOU_LINK
        for i, fb in by_photo.get(pid, ()):
            v = _iou(g, fb)
            if v > best:
                best_i, best = i, v
        if best_i is not None:
            refs.setdefault(person, []).append(best_i)

    people = [p for p, i in refs.items() if len(i) >= MIN_REFS]
    if not people:
        return
    cent, owner = [], []
    for j, p in enumerate(people):
        idxs = refs[p]
        k = min(MAX_SUB, max(1, len(idxs) // SUB_PER))
        for c in (_kmeans(emb[idxs], k) if k > 1 else [_unit(emb[idxs].mean(0))]):
            cent.append(c)
            owner.append(j)
    cent = np.stack(cent)
    owner = np.array(owner)

    names = dict(db.query(m.Person.id, m.Person.canonical_name)
                 .filter(m.Person.id.in_(people)).all())
    boxed = {(pid, per) for pid, per in
             db.query(m.PhotoPerson.photo_id, m.PhotoPerson.person_id)
             .filter(m.PhotoPerson.region_w.isnot(None)).all()}

    q = np.zeros((len(out), EMB_DIM), dtype=np.float32)
    for i, blob in enumerate(embs):
        if blob:
            q[i] = np.frombuffer(blob, dtype=np.float32)
    sims = q @ cent.T
    top = sims.argmax(1)
    for i, row in enumerate(out):
        person = people[owner[top[i]]]
        row["looks_like"] = names.get(person, person)
        row["looks_like_id"] = person
        row["looks_like_score"] = round(float(sims[i, top[i]]), 3)
        row["boxed_here"] = (row["photo_id"], person) in boxed


@router.post("/faces/assign")
def assign_faces(body: dict, db: Session = Depends(get_db),
                 user: m.User = Depends(require_admin)):
    """Name or ignore a SUBSET of faces, splitting a mixed cluster.

    - **name**: writes ordinary `photo_person` rows with the box and drops the faces out
      of their cluster. Future `cluster_faces` runs skip them anyway, because a face
      overlapping a confirmed region is no longer "unidentified".
    - **ignore**: moves them into a *new* ignored cluster carrying their own centroid —
      not merely detached — so the §14.7a promise still holds and lookalikes are absorbed
      silently on later runs rather than re-asked.

    Either way the source cluster's `n_faces` is corrected, so what remains is what is
    genuinely left to decide."""
    import numpy as np

    action = (body.get("action") or "").lower()
    if action not in ("name", "ignore"):
        raise HTTPException(400, "action must be name or ignore")
    face_ids = [int(i) for i in body.get("face_ids", [])]
    if not face_ids:
        raise HTTPException(400, "no face_ids")
    person_id = body.get("person_id")
    if action == "name" and not db.get(m.Person, person_id or ""):
        raise HTTPException(400, "name requires a valid person_id")

    faces = db.query(m.Face).filter(m.Face.id.in_(face_ids)).all()
    if not faces:
        raise HTTPException(404, "no such faces")
    src_clusters = {f.cluster_id for f in faces if f.cluster_id}

    added, moved, blocked, filled = [], [], [], []
    new_cluster_id = None
    if action == "ignore":
        # `no_centroid` is for dismissing a SECOND face of someone already tagged on that
        # photo — collages and photos-of-photos. Ignoring normally would store a centroid
        # shaped like a real family member, and cluster_faces seeds from ignored centroids
        # at 0.55 to absorb new faces silently: measured 42 unnamed faces within 0.55 of
        # Kate, 54 of Cori. That would quietly suppress genuine faces of them forever.
        # A centroid-less ignored cluster records the decision and absorbs nothing.
        vecs = ([] if body.get("no_centroid") else
                [np.frombuffer(f.embedding, dtype=np.float32) for f in faces if f.embedding])
        cvec = None
        if vecs:
            c = np.mean(vecs, axis=0)
            n = np.linalg.norm(c)
            cvec = (c / n if n else c).astype(np.float32).tobytes()
        cl = m.FaceCluster(status=m.CLUSTER_IGNORED, centroid=cvec, n_faces=len(faces),
                           decided_by=user.email, decided_at=datetime.now(timezone.utc))
        db.add(cl)
        db.flush()
        new_cluster_id = cl.id

    queued_pairs: set[tuple[int, str]] = set()
    for f in faces:
        moved.append([f.id, f.cluster_id])
        if action == "name":
            if (f.photo_id, person_id) in queued_pairs:
                f.cluster_id = None
                continue
            if db.get(m.PhotoPerson, (f.photo_id, person_id)) is None:
                queued_pairs.add((f.photo_id, person_id))
                db.add(m.PhotoPerson(photo_id=f.photo_id, person_id=person_id,
                                     source=SOURCE_HUMAN, uncertain=False,
                                     region_x=f.x, region_y=f.y, region_w=f.w, region_h=f.h))
                added.append([f.photo_id, person_id])
            else:
                pp = db.get(m.PhotoPerson, (f.photo_id, person_id))
                if pp.region_w is None:
                    # Tagged but with no geometry — extremely common for people carried
                    # in from Lightroom keywords. Naming this face is not a duplicate,
                    # it is the missing box, so fill it in. Blocking here was wrong: a
                    # dog tagged by keyword could never be given a face at all.
                    pp.region_x, pp.region_y = f.x, f.y
                    pp.region_w, pp.region_h = f.w, f.h
                    filled.append([f.photo_id, person_id])
                else:
                    # A real box already exists, and one region per (photo, person) means
                    # this face cannot also be recorded. It used to skip in silence, which
                    # reads as "the tag didn't work" — and it matters, because if this
                    # really is that person then their EXISTING box is on the wrong face.
                    blocked.append([f.photo_id, person_id])
            f.cluster_id = None
        else:
            f.cluster_id = new_cluster_id

    for cid in src_clusters:
        left = db.query(func.count(m.Face.id)).filter(m.Face.cluster_id == cid).scalar() or 0
        c = db.get(m.FaceCluster, cid)
        if c:
            c.n_faces = left
    _log(db, user, f"faces:{action}", None,
         f"{len(faces)} faces" + (f" -> {person_id}" if person_id else ""),
         inverse={"op": "faces_assign", "moved": moved, "added": added,
                  "filled": filled, "new_cluster_id": new_cluster_id})
    db.commit()
    return {"faces": len(faces), "tags_added": len(added),
            "regions_filled": len(filled), "blocked": blocked,
            "new_ignored_cluster": new_cluster_id}


@router.get("/face-clusters")
def face_clusters(status: str = "pending", min_faces: int = 2, limit: int = 200,
                  db: Session = Depends(get_db), user: m.User = Depends(require_admin)):
    """Unknown-face clusters, most prominent first (SPEC §14.7a).

    `min_faces=2` by default because singletons dominate — 2,520 of 2,976 clusters on the
    first run — and they are overwhelmingly one-off background faces. They are meant to be
    dismissed with the bulk action, not paged through."""
    q = (db.query(m.FaceCluster).filter(m.FaceCluster.status == status,
                                        m.FaceCluster.n_faces >= min_faces)
         .order_by(m.FaceCluster.prominence.desc().nullslast()).limit(limit).all())
    out = []
    for c in q:
        # Samples carry the photo + box so the panel can preview one in context. Ids
        # alone forced the collapsed view to render crops it could say nothing about.
        rows = (db.query(m.Face.id, m.Face.x, m.Face.y, m.Face.w, m.Face.h,
                         m.Photo.source_file)
                .join(m.Photo, m.Photo.id == m.Face.photo_id)
                .filter(m.Face.cluster_id == c.id)
                .order_by((m.Face.w * m.Face.h).desc()).limit(8).all())
        out.append({"id": c.id, "n_faces": c.n_faces,
                    "prominence": round(c.prominence or 0, 5),
                    "samples": [{"face_id": fid, "source_file": sf,
                                 "box": [round(x, 5), round(y, 5), round(w, 5), round(h, 5)]}
                                for fid, x, y, w, h, sf in rows]})
    return out


@router.get("/face-clusters/summary")
def face_cluster_summary(db: Session = Depends(get_db), user: m.User = Depends(require_admin)):
    rows = db.query(m.FaceCluster.status, func.count(m.FaceCluster.id),
                    func.sum(m.FaceCluster.n_faces)).group_by(m.FaceCluster.status).all()
    singles = (db.query(func.count(m.FaceCluster.id))
               .filter(m.FaceCluster.status == m.CLUSTER_PENDING,
                       m.FaceCluster.n_faces == 1).scalar() or 0)
    return {"by_status": [{"status": s, "clusters": c, "faces": f or 0} for s, c, f in rows],
            "pending_singletons": singles}


@router.post("/face-clusters/decide")
def decide_clusters(body: dict, db: Session = Depends(get_db),
                    user: m.User = Depends(require_admin)):
    """Name a cluster (tags every face in it) or ignore it — one decision, N faces.

    Ignoring keeps the cluster and its centroid so `cluster_faces` absorbs future
    lookalikes silently instead of re-asking (§14.7a). `singletons: true` applies the
    action to every pending one-face cluster at once, which is the intended way to clear
    the background tail."""
    action = (body.get("action") or "").lower()
    if action not in ("name", "ignore"):
        raise HTTPException(400, "action must be name or ignore")
    person_id = body.get("person_id")
    if action == "name" and not db.get(m.Person, person_id or ""):
        raise HTTPException(400, "name requires a valid person_id")

    if body.get("singletons"):
        cl = db.query(m.FaceCluster).filter(m.FaceCluster.status == m.CLUSTER_PENDING,
                                            m.FaceCluster.n_faces == 1).all()
    else:
        cl = db.query(m.FaceCluster).filter(
            m.FaceCluster.id.in_([int(i) for i in body.get("cluster_ids", [])])).all()
    if not cl:
        raise HTTPException(400, "no clusters selected")

    now = datetime.now(timezone.utc)
    added = []
    queued_pairs: set[tuple[int, str]] = set()
    for c in cl:
        c.status = m.CLUSTER_NAMED if action == "name" else m.CLUSTER_IGNORED
        c.person_id = person_id if action == "name" else None
        c.decided_by, c.decided_at = user.email, now
        if action != "name":
            continue
        for f in db.query(m.Face).filter(m.Face.cluster_id == c.id).all():
            if (f.photo_id, person_id) in queued_pairs:
                continue      # same (photo, person) already queued in this request
            if db.get(m.PhotoPerson, (f.photo_id, person_id)) is None:
                queued_pairs.add((f.photo_id, person_id))
                db.add(m.PhotoPerson(photo_id=f.photo_id, person_id=person_id,
                                     source=SOURCE_HUMAN, uncertain=False,
                                     region_x=f.x, region_y=f.y, region_w=f.w, region_h=f.h))
                added.append([f.photo_id, person_id])
    _log(db, user, f"faces:cluster-{action}", None,
         f"{len(cl)} clusters" + (f" -> {person_id}" if person_id else ""),
         inverse={"op": "face_cluster_decide", "cluster_ids": [c.id for c in cl],
                  "added": added})
    db.commit()
    return {"clusters": len(cl), "tags_added": len(added)}


@router.get("/unresolved-locations")
def unresolved_locations(radius_km: float = 15.0, db: Session = Depends(get_db),
                         user: m.User = Depends(require_admin)):
    """Photos that carry GPS but resolved to no gazetteer place, grouped into clusters.

    The gazetteer is curated — the importers never auto-create places (SPEC §3.4/§13.9),
    so a photo taken somewhere Steve has not named yet keeps its coordinates and no
    `place_id`. Raw, that is hundreds of points; clustered it is a short, nameable list
    (671 digital photos collapse to 26 locations at 15 km).

    Greedy single-pass clustering, seeded by the densest points because rows come back
    ordered by count — good enough for "which places do I still need to name", and it
    avoids pulling a clustering dependency in for a few hundred points.
    """
    rows = (db.query(m.Photo.id, m.Photo.lat, m.Photo.lon, m.Photo.source_file,
                     m.Photo.date_start)
            .filter(m.Photo.lat.isnot(None), m.Photo.place_id.is_(None))
            .all())
    clusters: list[dict] = []
    for pid, lat, lon, src, dt in rows:
        for c in clusters:
            if _km(lat, lon, c["lat"], c["lon"]) < radius_km:
                c["count"] += 1
                c["photo_ids"].append(pid)
                if dt and (c["first"] is None or dt < c["first"]):
                    c["first"] = dt
                if dt and (c["last"] is None or dt > c["last"]):
                    c["last"] = dt
                break
        else:
            clusters.append({"lat": lat, "lon": lon, "count": 1, "photo_ids": [pid],
                             "sample_source_file": src, "first": dt, "last": dt})
    clusters.sort(key=lambda c: -c["count"])
    return [{"lat": round(c["lat"], 5), "lon": round(c["lon"], 5), "count": c["count"],
             "sample_source_file": c["sample_source_file"],
             "sample_photo_id": c["photo_ids"][0],
             "first_year": c["first"].year if c["first"] else None,
             "last_year": c["last"].year if c["last"] else None}
            for c in clusters]


@router.post("/places/{place_id}/claim-nearby")
def claim_nearby(place_id: str, radius_km: float = 15.0, db: Session = Depends(get_db),
                 user: m.User = Depends(require_admin)):
    """Attach every unplaced GPS photo within `radius_km` of this place to it.

    Saves re-running the whole importer just to pick up a place named a moment ago.

    **Default is the CLUSTER radius (15 km), not the importer's 25 km match radius.**
    Deliberate: the admin names a cluster it can see on the map, so the claim must match
    what was shown. At 25 km, naming the 421-photo Broomfield cluster also swallowed the
    separate 12-photo Boulder cluster 15.4 km away — surprising, and it silently denies
    Boulder its own name. Callers wanting the importer's wider behaviour pass
    `radius_km=25`. Undoable as one contribution."""
    pl = db.get(m.Place, place_id)
    if pl is None:
        raise HTTPException(404, "place not found")
    if pl.lat is None or pl.lon is None:
        raise HTTPException(400, "place has no coordinates")
    rows = (db.query(m.Photo)
            .filter(m.Photo.lat.isnot(None), m.Photo.place_id.is_(None)).all())
    claimed = [p for p in rows if _km(p.lat, p.lon, pl.lat, pl.lon) < radius_km]
    for p in claimed:
        p.place_id = pl.id
    if claimed:
        _log(db, user, "place:claim-nearby", None, f"{len(claimed)} photos -> {pl.canonical_name}",
             inverse={"op": "place_unclaim", "photo_ids": [p.id for p in claimed]})
    db.commit()
    return {"claimed": len(claimed), "place": pl.canonical_name}


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
@router.get("/rotation/queue")
def rotation_queue(db: Session = Depends(get_db), user: m.User = Depends(require_admin)):
    """The Rotation-sweep worklist: every rotatable photo (slides + scans; digital
    masters are read-only B2 objects), ordered detector-proposals first, then
    probed-but-faceless (landscapes the detector can't judge), then everything else
    in Wendel's canonical mag/slide order. Proposals come from
    data/review/rotation_proposals.json (app.detect_rotation, dev-side)."""
    proposals: dict[int, dict] = {}
    digital_flagged: list[dict] = []
    ppath = REVIEW_DIR / "rotation_proposals.json"
    generated_at = None
    if ppath.exists():
        data = json.loads(ppath.read_text())
        generated_at = data.get("generated_at")
        for r in data.get("results", []):
            proposals[r["photo_id"]] = r
            if r["origin"] == "digital" and r.get("proposal"):
                digital_flagged.append({"id": r["photo_id"],
                                        "source_file": r["source_file"],
                                        "proposal": r["proposal"]})
    # A proposal (and the probe verdict itself) describes the file AS IT WAS when the
    # detector ran. Once a photo is rotated, that data is stale — re-offering it
    # pre-marks an already-fixed photo and a second Apply would wreck it (that is not
    # hypothetical: it triple-rotated 6 photos on 2026-08-05). The contribution log
    # is the authority on what was rotated after the probe.
    rotated_since = set()
    if generated_at is not None:
        cutoff = datetime.fromisoformat(generated_at).astimezone(
            timezone.utc).replace(tzinfo=None)
        rotated_since = {pid for (pid,) in db.query(m.Contribution.photo_id)
                         .filter(m.Contribution.field == "photo:rotate",
                                 m.Contribution.created_at > cutoff,
                                 m.Contribution.photo_id.isnot(None)).all()}
    face_counts = dict(db.query(m.Face.photo_id, func.count(m.Face.id))
                       .group_by(m.Face.photo_id).all())
    rows = (db.query(m.Photo).filter(m.Photo.origin.in_(["slide", "scan"]))
            .order_by(m.Photo.magazine_id, m.Photo.slide_in_mag, m.Photo.id).all())
    items = []
    for p in rows:
        pr = None if p.id in rotated_since else proposals.get(p.id)
        items.append({"id": p.id, "source_file": p.source_file, "origin": p.origin,
                      "caption": p.caption, "faces": face_counts.get(p.id, 0),
                      "thumb": _thumb_url(p.source_file) + _photo_version(p),
                      "proposal": (pr or {}).get("proposal"), "probed": pr is not None})
    items.sort(key=lambda r: 0 if r["proposal"] else (1 if r["probed"] else 2))
    return {"generated_at": generated_at, "items": items,
            "digital_flagged": digital_flagged}


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
    if storage.backend_of(p) != "local":
        # B2 masters are read-only and there is no local file to rewrite. Rotating only
        # the cached derivative would diverge from the master and be undone by the next
        # prewarm, so refuse rather than appear to work (SPEC §13.3).
        raise HTTPException(400, "digital photos are stored in B2 and cannot be rotated "
                                 "here — rotate in Lightroom and re-sync")
    _rotate_file(p, deg)
    _rotate_regions(db, p, deg)
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


@router.delete("/people/{person_id}")
def delete_person(person_id: str, db: Session = Depends(get_db),
                  user: m.User = Depends(require_admin)):
    """Remove a person — for typos and mistaken creations, not for curation.

    **Refuses while anything still points at them**, with a count, rather than cascading:
    deleting a tagged person would silently strip them from photos, and there is no way
    to tell "this was a typo" from "I tagged 40 photos then changed my mind" at this
    layer. Untag or merge first if that is what you meant.

    Undoable — the inverse carries the whole row plus its aliases, so the person comes
    back intact rather than as a bare id.
    """
    p = db.get(m.Person, person_id)
    if p is None:
        raise HTTPException(404, "person not found")

    tags = db.query(func.count(m.PhotoPerson.photo_id)).filter(
        m.PhotoPerson.person_id == person_id).scalar() or 0
    if tags:
        raise HTTPException(409, f"'{person_id}' is tagged on {tags} photo(s) — "
                                 "untag or merge them first")
    kin = db.query(func.count(m.Person.id)).filter(
        (m.Person.father_id == person_id) | (m.Person.mother_id == person_id)
        | (m.Person.spouse_id == person_id)).scalar() or 0
    if kin:
        raise HTTPException(409, f"'{person_id}' is listed as a parent/spouse of "
                                 f"{kin} other person(s) — clear those links first")
    users = db.query(func.count(m.User.email)).filter(
        m.User.person_id == person_id).scalar() or 0
    if users:
        raise HTTPException(409, f"'{person_id}' is linked to {users} user account(s)")

    aliases = [a.alias for a in db.query(m.PersonAlias)
               .filter(m.PersonAlias.person_id == person_id).all()]
    row = {"id": p.id, "canonical_name": p.canonical_name, "is_family": bool(p.is_family),
           "notes": p.notes, "father_id": p.father_id, "mother_id": p.mother_id,
           "spouse_id": p.spouse_id, "representative_photo_id": p.representative_photo_id}
    db.query(m.PersonAlias).filter(m.PersonAlias.person_id == person_id).delete(
        synchronize_session=False)
    db.delete(p)
    _log(db, user, "person:delete", person_id, None,
         inverse={"op": "person_undelete", "row": row, "aliases": aliases})
    db.commit()
    return {"deleted": person_id}


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


@router.post("/people/{person_id}/merge")
def merge_person(person_id: str, body: PersonMerge, db: Session = Depends(get_db),
                 user: m.User = Depends(require_admin)):
    """Merge one person into another — for duplicate rows (Layman/Leyman), not curation.

    Everything that points at the source is repointed at the destination: photo tags
    (when a photo carries *both*, the destination's tag wins and at most inherits the
    source's face region), aliases, family-tree links, linked viewer accounts, and the
    dev-side face suggestion/cluster rows. The source's display name survives as an
    alias of the destination so future imports of the old spelling still resolve.

    Undoable in one step — the inverse carries every moved row, so undo rebuilds the
    source person exactly rather than approximately.
    """
    src = db.get(m.Person, person_id)
    dst = db.get(m.Person, body.into_id)
    if src is None or dst is None:
        raise HTTPException(404, "person not found")
    if src.id == dst.id:
        raise HTTPException(400, "cannot merge a person into themselves")

    src_row = {"id": src.id, "canonical_name": src.canonical_name,
               "is_family": bool(src.is_family), "notes": src.notes,
               "father_id": src.father_id, "mother_id": src.mother_id,
               "spouse_id": src.spouse_id,
               "representative_photo_id": src.representative_photo_id}

    # ---- photo tags. A photo tagged with both people collides on the composite PK:
    # keep the destination's row, but let it inherit the source's face region if it
    # has none of its own (the source was often the one with the reviewed box).
    dst_tags = {pp.photo_id: pp for pp in db.query(m.PhotoPerson)
                .filter(m.PhotoPerson.person_id == dst.id).all()}
    moved, dropped, region_fills = [], [], []
    for pp in db.query(m.PhotoPerson).filter(m.PhotoPerson.person_id == src.id).all():
        row = [pp.photo_id, pp.source, bool(pp.uncertain),
               [pp.region_x, pp.region_y, pp.region_w, pp.region_h]]
        clash = dst_tags.get(pp.photo_id)
        if clash is None:
            moved.append(row)
            db.add(m.PhotoPerson(photo_id=pp.photo_id, person_id=dst.id,
                                 source=pp.source, uncertain=pp.uncertain,
                                 region_x=pp.region_x, region_y=pp.region_y,
                                 region_w=pp.region_w, region_h=pp.region_h))
        else:
            dropped.append(row)
            if clash.region_w is None and pp.region_w is not None:
                region_fills.append([pp.photo_id, [clash.region_x, clash.region_y,
                                                   clash.region_w, clash.region_h]])
                clash.region_x, clash.region_y = pp.region_x, pp.region_y
                clash.region_w, clash.region_h = pp.region_w, pp.region_h
        db.delete(pp)

    # ---- aliases (bulk, immediate SQL — keeps the delete-orphan cascade on
    # src.aliases from seeing anything when the source row goes).
    dst_aliases = {a.alias for a in db.query(m.PersonAlias)
                   .filter(m.PersonAlias.person_id == dst.id).all()}
    alias_moved, dupe_ids = [], []
    for a in db.query(m.PersonAlias).filter(m.PersonAlias.person_id == src.id).all():
        if a.alias in dst_aliases:
            dupe_ids.append(a.id)
        else:
            alias_moved.append(a.alias)
    alias_dupes = [a.alias for a in db.query(m.PersonAlias)
                   .filter(m.PersonAlias.id.in_(dupe_ids)).all()] if dupe_ids else []
    if dupe_ids:
        db.query(m.PersonAlias).filter(m.PersonAlias.id.in_(dupe_ids)).delete(
            synchronize_session=False)
    db.query(m.PersonAlias).filter(m.PersonAlias.person_id == src.id).update(
        {"person_id": dst.id}, synchronize_session=False)
    name_alias = None
    if (src.canonical_name != dst.canonical_name
            and src.canonical_name not in dst_aliases
            and src.canonical_name not in alias_moved):
        name_alias = src.canonical_name
        db.add(m.PersonAlias(person_id=dst.id, alias=name_alias))

    # ---- family-tree links pointing AT the source. Immediate SQL: these FKs carry
    # no relationship(), so ORM flush ordering vs. the person delete is not guaranteed.
    # A destination that pointed at its own duplicate would become a self-link — clear
    # it instead (undo restores the original either way).
    kin = []
    for field in ("father_id", "mother_id", "spouse_id"):
        col = getattr(m.Person, field)
        for (pid,) in db.query(m.Person.id).filter(col == src.id,
                                                   m.Person.id != src.id).all():
            db.query(m.Person).filter(m.Person.id == pid).update(
                {field: None if pid == dst.id else dst.id}, synchronize_session=False)
            kin.append([pid, field])

    # ---- the source's own links fill the destination's gaps (never as a self-link)
    db.refresh(dst)
    link_fills = []
    for field in ("father_id", "mother_id", "spouse_id"):
        sv = src_row[field]
        if sv and sv != dst.id and getattr(dst, field) is None:
            setattr(dst, field, sv)
            link_fills.append(field)

    # ---- linked viewer accounts (FK without ondelete — must move before the delete)
    user_ids = [uid for (uid,) in db.query(m.User.id)
                .filter(m.User.person_id == src.id).all()]
    if user_ids:
        db.query(m.User).filter(m.User.person_id == src.id).update(
            {"person_id": dst.id}, synchronize_session=False)

    # ---- face suggestions (dev-side). uq(face_id, person_id): where both people were
    # suggested for one face, the destination's row wins and the source's is recorded.
    dst_faces = {fid for (fid,) in db.query(m.FaceSuggestion.face_id)
                 .filter(m.FaceSuggestion.person_id == dst.id).all()}
    sugg_moved, sugg_dropped, drop_ids = [], [], []
    for s in db.query(m.FaceSuggestion).filter(m.FaceSuggestion.person_id == src.id).all():
        if s.face_id in dst_faces:
            drop_ids.append(s.id)
            sugg_dropped.append([s.face_id, s.score, s.status, s.decided_by,
                                 s.decided_at.isoformat() if s.decided_at else None])
        else:
            sugg_moved.append(s.id)
    for ch in _chunks(drop_ids):
        db.query(m.FaceSuggestion).filter(m.FaceSuggestion.id.in_(ch)).delete(
            synchronize_session=False)
    for ch in _chunks(sugg_moved):
        db.query(m.FaceSuggestion).filter(m.FaceSuggestion.id.in_(ch)).update(
            {"person_id": dst.id}, synchronize_session=False)

    # ---- named face clusters
    cluster_ids = [cid for (cid,) in db.query(m.FaceCluster.id)
                   .filter(m.FaceCluster.person_id == src.id).all()]
    if cluster_ids:
        db.query(m.FaceCluster).filter(m.FaceCluster.person_id == src.id).update(
            {"person_id": dst.id}, synchronize_session=False)

    # ---- thumbnail: adopt the source's only if the destination has none
    rep_adopted = False
    if dst.representative_photo_id is None and src_row["representative_photo_id"] is not None:
        dst.representative_photo_id = src_row["representative_photo_id"]
        rep_adopted = True

    db.delete(src)
    _log(db, user, "person:merge", src.id, dst.id,
         inverse={"op": "person_unmerge", "row": src_row, "dst_id": dst.id,
                  "moved": moved, "dropped": dropped, "region_fills": region_fills,
                  "alias_moved": alias_moved, "alias_dupes": alias_dupes,
                  "name_alias": name_alias, "kin": kin, "link_fills": link_fills,
                  "users": user_ids, "sugg_moved": sugg_moved,
                  "sugg_dropped": sugg_dropped, "clusters": cluster_ids,
                  "rep_adopted": rep_adopted})
    db.commit()
    total = db.query(func.count(m.PhotoPerson.photo_id)).filter(
        m.PhotoPerson.person_id == dst.id).scalar() or 0
    return {"merged": src_row["id"], "into": dst.id, "tags_moved": len(moved),
            "tags_already_there": len(dropped), "aliases_moved": len(alias_moved),
            "photo_count": total}


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


@router.get("/people/without-face")
def people_without_face(limit: int = 200, candidates: int = 6,
                        db: Session = Depends(get_db), user: m.User = Depends(require_admin)):
    """People with no filter thumbnail, each with candidate faces to choose from.

    Only worth building now that face boxes exist: 106 of 126 people had no thumbnail,
    and picking one used to mean hunting through the gallery for a photo where they are
    recognisable. Now every tagged person has boxes, so the app can *propose* — ranked by
    **box area**, since a bigger face makes a better 240px crop, which is the only thing
    a thumbnail has to be good at.
    """
    area = m.PhotoPerson.region_w * m.PhotoPerson.region_h
    people = (db.query(m.Person.id, m.Person.canonical_name)
              .filter(m.Person.representative_photo_id.is_(None))
              .order_by(m.Person.canonical_name).limit(limit).all())
    out = []
    for pid, name in people:
        rows = (db.query(m.PhotoPerson.photo_id, area.label("a"),
                         m.Photo.source_file, m.Photo.date_start)
                .join(m.Photo, m.Photo.id == m.PhotoPerson.photo_id)
                .filter(m.PhotoPerson.person_id == pid,
                        m.PhotoPerson.region_w.isnot(None))
                .order_by(area.desc()).limit(candidates).all())
        out.append({"person_id": pid, "name": name,
                    "candidates": [{"photo_id": ph, "area": round(a, 5),
                                    "source_file": sf,
                                    "year": dt.year if dt else None}
                                   for ph, a, sf, dt in rows]})
    return out


@router.post("/people/set-representatives")
def set_representatives(body: dict, db: Session = Depends(get_db),
                        user: m.User = Depends(require_admin)):
    """Set several people's thumbnails at once — one undoable contribution for the batch."""
    picks = [(str(a), int(b)) for a, b in body.get("picks", [])]
    if not picks:
        raise HTTPException(400, "no picks")
    prior = []
    done = 0
    for person_id, photo_id in picks:
        person = db.get(m.Person, person_id)
        if person is None or db.get(m.Photo, photo_id) is None:
            continue
        prior.append([person_id, person.representative_photo_id])
        person.representative_photo_id = photo_id
        done += 1
    _log(db, user, "person:representative-bulk", None, f"{done} people",
         inverse={"op": "person_representative_bulk", "prior": prior})
    db.commit()
    return {"set": done}


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
    """Manually set a person's face box on a photo, creating the tag if they weren't
    already on it.

    Two callers: the ★-thumbnail flow (SPEC §4.2) passes `set_representative`, and the
    §14 face-tagging flow does not — drawn boxes feed the face index and must not keep
    reassigning someone's thumbnail. Undo restores both the prior region and the prior
    representative either way."""
    person = db.get(m.Person, person_id)
    if person is None:
        raise HTTPException(404, "person not found")
    if db.get(m.Photo, body.photo_id) is None:
        raise HTTPException(404, "photo not found")
    pp = db.get(m.PhotoPerson, (body.photo_id, person_id))
    created = pp is None
    if created:
        pp = m.PhotoPerson(photo_id=body.photo_id, person_id=person_id, source=SOURCE_HUMAN)
        db.add(pp)
    # `created` so undo can remove the tag outright rather than leaving a region-less
    # one behind — this path now creates tags routinely, not just backfills regions.
    prior = {"rep": person.representative_photo_id, "created": created,
             "region": [pp.region_x, pp.region_y, pp.region_w, pp.region_h]}
    pp.region_x, pp.region_y, pp.region_w, pp.region_h = body.x, body.y, body.w, body.h
    if body.set_representative:
        person.representative_photo_id = body.photo_id
    _log(db, user, "person:face", None, person_id,
         inverse={"op": "person_face", "person_id": person_id,
                  "photo_id": body.photo_id, "prior": prior})
    db.commit()
    return {"person_id": person_id,
            "representative_photo_id": person.representative_photo_id}


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
            _rotate_regions(db, p, inv["degrees"])
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
            if inv["prior"].get("created"):
                db.delete(pp)        # the tag itself was ours; take it with us
            else:
                pp.region_x, pp.region_y, pp.region_w, pp.region_h = inv["prior"]["region"]
    elif op == "face_decide":
        # Put the suggestions back to pending and remove only what this decision added.
        db.query(m.FaceSuggestion).filter(
            m.FaceSuggestion.id.in_(inv["suggestion_ids"])).update(
            {"status": m.FACE_PENDING, "decided_by": None, "decided_at": None},
            synchronize_session=False)
        for photo_id, person_id, was_new in inv.get("added", []):
            pp = db.get(m.PhotoPerson, (photo_id, person_id))
            if pp is None:
                continue
            if was_new:
                db.delete(pp)          # the tag itself came from this decision
            else:
                pp.region_x = pp.region_y = pp.region_w = pp.region_h = None
    elif op == "person_representative_bulk":
        for person_id, prev in inv.get("prior", []):
            person = db.get(m.Person, person_id)
            if person is not None:
                person.representative_photo_id = prev
    elif op == "face_untag":
        for ph, pid, box, src, unc in inv.get("removed", []):
            if db.get(m.PhotoPerson, (ph, pid)) is None:
                db.add(m.PhotoPerson(photo_id=ph, person_id=pid, source=src,
                                     uncertain=unc, region_x=box[0], region_y=box[1],
                                     region_w=box[2], region_h=box[3]))
    elif op == "face_unaccept":
        db.query(m.FaceSuggestion).filter(
            m.FaceSuggestion.id.in_(inv["suggestion_ids"])).update(
            {"status": m.FACE_ACCEPTED}, synchronize_session=False)
        for photo_id, person_id, box in inv.get("removed", []):
            if db.get(m.PhotoPerson, (photo_id, person_id)) is None:
                db.add(m.PhotoPerson(photo_id=photo_id, person_id=person_id,
                                     source=SOURCE_HUMAN, uncertain=False,
                                     region_x=box[0], region_y=box[1],
                                     region_w=box[2], region_h=box[3]))
    elif op == "person_undelete":
        r = inv["row"]
        if db.get(m.Person, r["id"]) is None:
            db.add(m.Person(**r))
            db.flush()
            for a in inv.get("aliases", []):
                db.add(m.PersonAlias(person_id=r["id"], alias=a))
    elif op == "person_unmerge":
        r = inv["row"]
        src_id, dst_id = r["id"], inv["dst_id"]
        if db.get(m.Person, src_id) is None:
            db.add(m.Person(**r))
            db.flush()          # the immediate-SQL repoints below need the row to exist
        # tags: moved rows go back wholesale; collision-dropped rows are recreated
        # beside the destination's (which stays); inherited regions are cleared.
        for photo_id, srcv, unc, box in inv.get("moved", []):
            pp = db.get(m.PhotoPerson, (photo_id, dst_id))
            if pp is not None:
                db.delete(pp)
            if db.get(m.PhotoPerson, (photo_id, src_id)) is None:
                db.add(m.PhotoPerson(photo_id=photo_id, person_id=src_id, source=srcv,
                                     uncertain=bool(unc), region_x=box[0], region_y=box[1],
                                     region_w=box[2], region_h=box[3]))
        for photo_id, srcv, unc, box in inv.get("dropped", []):
            if db.get(m.PhotoPerson, (photo_id, src_id)) is None:
                db.add(m.PhotoPerson(photo_id=photo_id, person_id=src_id, source=srcv,
                                     uncertain=bool(unc), region_x=box[0], region_y=box[1],
                                     region_w=box[2], region_h=box[3]))
        for photo_id, box in inv.get("region_fills", []):
            pp = db.get(m.PhotoPerson, (photo_id, dst_id))
            if pp is not None:
                pp.region_x, pp.region_y, pp.region_w, pp.region_h = box
        for al in inv.get("alias_moved", []):
            db.query(m.PersonAlias).filter(m.PersonAlias.person_id == dst_id,
                                           m.PersonAlias.alias == al).delete(
                synchronize_session=False)
        for al in inv.get("alias_moved", []) + inv.get("alias_dupes", []):
            db.add(m.PersonAlias(person_id=src_id, alias=al))
        if inv.get("name_alias"):
            db.query(m.PersonAlias).filter(m.PersonAlias.person_id == dst_id,
                                           m.PersonAlias.alias == inv["name_alias"]).delete(
                synchronize_session=False)
        for pid, field in inv.get("kin", []):
            db.query(m.Person).filter(m.Person.id == pid).update(
                {field: src_id}, synchronize_session=False)
        dstp = db.get(m.Person, dst_id)
        if dstp is not None:
            db.refresh(dstp)     # the kin repoints above may have touched its row
            for field in inv.get("link_fills", []):
                setattr(dstp, field, None)
            if inv.get("rep_adopted"):
                dstp.representative_photo_id = None
        for ch in _chunks(inv.get("users", [])):
            db.query(m.User).filter(m.User.id.in_(ch)).update(
                {"person_id": src_id}, synchronize_session=False)
        for ch in _chunks(inv.get("sugg_moved", [])):
            db.query(m.FaceSuggestion).filter(m.FaceSuggestion.id.in_(ch)).update(
                {"person_id": src_id}, synchronize_session=False)
        for face_id, score, status, decided_by, decided_at in inv.get("sugg_dropped", []):
            db.add(m.FaceSuggestion(face_id=face_id, person_id=src_id, score=score,
                                    status=status, decided_by=decided_by,
                                    decided_at=datetime.fromisoformat(decided_at)
                                    if decided_at else None))
        for ch in _chunks(inv.get("clusters", [])):
            db.query(m.FaceCluster).filter(m.FaceCluster.id.in_(ch)).update(
                {"person_id": src_id}, synchronize_session=False)
    elif op == "faces_assign":
        for photo_id, person_id in inv.get("added", []):
            pp = db.get(m.PhotoPerson, (photo_id, person_id))
            if pp is not None:
                db.delete(pp)
        # Regions filled on a PRE-EXISTING tag: clear the geometry, keep the tag itself.
        for photo_id, person_id in inv.get("filled", []):
            pp = db.get(m.PhotoPerson, (photo_id, person_id))
            if pp is not None:
                pp.region_x = pp.region_y = pp.region_w = pp.region_h = None
        for face_id, prev_cluster in inv.get("moved", []):
            f = db.get(m.Face, face_id)
            if f is not None:
                f.cluster_id = prev_cluster
        if inv.get("new_cluster_id"):
            c = db.get(m.FaceCluster, inv["new_cluster_id"])
            if c is not None:
                db.delete(c)
        for cid in {pc for _f, pc in inv.get("moved", []) if pc}:
            c = db.get(m.FaceCluster, cid)
            if c is not None:
                c.n_faces = db.query(func.count(m.Face.id)).filter(
                    m.Face.cluster_id == cid).scalar() or 0
    elif op == "face_cluster_decide":
        db.query(m.FaceCluster).filter(
            m.FaceCluster.id.in_(inv["cluster_ids"])).update(
            {"status": m.CLUSTER_PENDING, "person_id": None,
             "decided_by": None, "decided_at": None}, synchronize_session=False)
        for photo_id, person_id in inv.get("added", []):
            pp = db.get(m.PhotoPerson, (photo_id, person_id))
            if pp is not None:
                db.delete(pp)
    elif op == "place_unclaim":
        for pid in inv["photo_ids"]:
            p = db.get(m.Photo, pid)
            if p:
                p.place_id = None
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
