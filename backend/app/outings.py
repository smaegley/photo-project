"""Group ingested photos into outings and give each a triage class (SPEC §15.5, slice 4).

    python -m app.outings [--dry-run]

Runs at the end of every ingest, after face matching; safe to run by hand at any time.
No ML, no network — it only reads rows and writes three columns — so it runs in the
plain `api` image as well as the `ingest` one.

**Outing.** Photos in capture order; a new outing starts after a gap of 2 hours or more,
or when a photo's GPS fix is more than 5 km from the previous fix in the outing. Photos
with no EXIF capture time (saved or received images) group by their dated import folder.

**Triage, first match wins** (`rescued` is an admin override and is never touched):
  selected   — a face in the photo matches someone known to the app (a pending or
               accepted suggestion), or the photo carries a people tag
  junk       — junk score at or over the threshold AND no face of any kind in the
               photo (every flagged photo with a face was a false positive, P-I2)
  related    — same outing as a selected or rescued photo
  unselected — everything else
Only *pending* photos are re-triaged, so a class can still improve when a late arrival
or a newly named friend changes the picture; once a photo is published or culled its
class is frozen as the record of what the pipeline thought.

**Stable ids.** Outings are recomputed from scratch each run but keep their ids: a
group reuses the id most of its photos already carry (the largest group wins a tie), so
an event name typed on an outing survives new photos joining it.

Writes are chunked with a pause between chunks — SQLite's write lock is not fair, and
the site must stay writable while this runs (tests/check_write_contention.py).
"""
import argparse
import math
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import timedelta

from app import junk, models as m
from app.database import SessionLocal

GAP = timedelta(hours=2)
KM = 5.0
CHUNK = 100
YIELD_S = 0.05
FOLDER = re.compile(r"^(.*/\d{4}-\d{2}-\d{2})/")


def km_between(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def group(rows) -> list[dict]:
    """rows: objects with id, taken_at, lat, lon, source_file -> [{ids, start, end,
    lat, lon, folder}] in a deterministic order."""
    timed = sorted((r for r in rows if r.taken_at is not None),
                   key=lambda r: (r.taken_at, r.id))
    out, cur, last_t, last_gps = [], [], None, None
    for r in timed:
        gps = (r.lat, r.lon) if r.lat is not None and r.lon is not None else None
        if cur and (r.taken_at - last_t >= GAP
                    or (gps and last_gps and km_between(gps, last_gps) > KM)):
            out.append(cur)
            cur, last_gps = [], None
        cur.append(r)
        last_t = r.taken_at
        last_gps = gps or last_gps
    if cur:
        out.append(cur)

    groups = []
    for g in out:
        pts = [(r.lat, r.lon) for r in g if r.lat is not None and r.lon is not None]
        groups.append({
            "ids": [r.id for r in g], "start": g[0].taken_at, "end": g[-1].taken_at,
            "lat": sum(p[0] for p in pts) / len(pts) if pts else None,
            "lon": sum(p[1] for p in pts) / len(pts) if pts else None, "folder": None})

    by_folder = defaultdict(list)
    for r in rows:
        if r.taken_at is None:
            mm = FOLDER.match(r.source_file or "")
            by_folder[mm.group(1) if mm else "(undated)"].append(r.id)
    for folder in sorted(by_folder):
        groups.append({"ids": sorted(by_folder[folder]), "start": None, "end": None,
                       "lat": None, "lon": None, "folder": folder})
    return groups


def trigger_ids(db) -> set[int]:
    """Ingested photos showing someone the app knows."""
    ingested = m.Photo.ingest_run_id.isnot(None)
    ids = {pid for (pid,) in
           db.query(m.Face.photo_id).distinct()
           .join(m.FaceSuggestion, m.FaceSuggestion.face_id == m.Face.id)
           .join(m.Photo, m.Photo.id == m.Face.photo_id)
           .filter(ingested, m.FaceSuggestion.status.in_((m.FACE_PENDING, m.FACE_ACCEPTED)))}
    ids |= {pid for (pid,) in
            db.query(m.PhotoPerson.photo_id).distinct()
            .join(m.Photo, m.Photo.id == m.PhotoPerson.photo_id).filter(ingested)}
    return ids


def run(dry_run: bool = False) -> dict:
    db = SessionLocal()
    try:
        rows = (db.query(m.Photo.id, m.Photo.taken_at, m.Photo.lat, m.Photo.lon,
                         m.Photo.source_file, m.Photo.outing_id, m.Photo.triage,
                         m.Photo.visibility, m.Photo.junk_score)
                .filter(m.Photo.ingest_run_id.isnot(None)).all())
        if not rows:
            print("outings: no ingested photos")
            return {"photos": 0}
        by_id = {r.id: r for r in rows}
        groups = group(rows)

        # --- keep ids stable: biggest groups choose first ---
        claimed: set[int] = set()
        for g in sorted(groups, key=lambda g: (-len(g["ids"]), g["ids"][0])):
            votes = Counter(by_id[i].outing_id for i in g["ids"]
                            if by_id[i].outing_id is not None)
            g["id"] = next((oid for oid, _ in votes.most_common() if oid not in claimed), None)
            if g["id"] is not None:
                claimed.add(g["id"])

        # --- triage ---
        trig = trigger_ids(db)
        faced = {pid for (pid,) in db.query(m.Face.photo_id).distinct()
                 .join(m.Photo, m.Photo.id == m.Face.photo_id)
                 .filter(m.Photo.ingest_run_id.isnot(None))}
        junky = {r.id for r in rows if r.junk_score is not None
                 and r.junk_score >= junk.THRESHOLD and r.id not in faced and r.id not in trig}
        new_triage: dict[int, str] = {}
        for g in groups:
            def frozen(r):                         # once published or culled, or rescued
                return r.triage == m.TRIAGE_RESCUED or (
                    r.triage is not None and r.visibility != m.VIS_PENDING)

            anchored = any(
                by_id[i].triage in (m.TRIAGE_SELECTED, m.TRIAGE_RESCUED)
                if frozen(by_id[i]) else i in trig for i in g["ids"])
            for i in g["ids"]:
                r = by_id[i]
                if frozen(r):
                    continue
                t = (m.TRIAGE_SELECTED if i in trig
                     else m.TRIAGE_JUNK if i in junky
                     else m.TRIAGE_RELATED if anchored else m.TRIAGE_UNSELECTED)
                if t != r.triage:
                    new_triage[i] = t

        final = Counter(new_triage.get(r.id, r.triage) for r in rows)
        stats = {"photos": len(rows), "outings": len(groups),
                 "singletons": sum(1 for g in groups if len(g["ids"]) == 1),
                 "retriaged": len(new_triage), **{k: v for k, v in final.items() if k}}
        if dry_run:
            print("outings (dry run):", stats)
            return stats

        # --- write: outing rows first (one short transaction), then photos in chunks ---
        existing = {o.id: o for o in db.query(m.Outing).all()}
        for g in groups:
            o = existing.get(g["id"]) if g["id"] is not None else None
            if o is None:
                o = m.Outing()
                db.add(o)
            o.start_at, o.end_at, o.lat, o.lon, o.folder = (
                g["start"], g["end"], g["lat"], g["lon"], g["folder"])
            g["row"] = o
        db.flush()
        for g in groups:
            g["id"] = g["row"].id
        # An outing none of the groups kept is dropped. When one splits, its id (and
        # so its event name) stays with the part holding most of its photos.
        for oid, o in existing.items():
            if oid not in {g["id"] for g in groups}:
                db.delete(o)
        db.commit()

        todo = []
        for g in groups:
            for i in g["ids"]:
                vals = {}
                if by_id[i].outing_id != g["id"]:
                    vals["outing_id"] = g["id"]
                if i in new_triage:
                    vals["triage"] = new_triage[i]
                if vals:
                    todo.append((i, vals))
        for k in range(0, len(todo), CHUNK):
            for i, vals in todo[k:k + CHUNK]:
                db.query(m.Photo).filter(m.Photo.id == i).update(vals, synchronize_session=False)
            db.commit()
            time.sleep(YIELD_S)
        stats["updated"] = len(todo)
        print("outings:", stats)
        return stats
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Outings + triage for ingested photos (SPEC §15.5)")
    ap.add_argument("--dry-run", action="store_true")
    run(dry_run=ap.parse_args().dry_run)
    sys.exit(0)
