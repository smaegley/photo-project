"""Auto-tag strong face matches on new photos (SPEC §15.10, slice 7).

    python -m app.autotag [--dry-run] [--threshold 0.65]

Runs in every ingest right after `match_faces`. A *pending* suggestion becomes a tag
without a human click when all of these hold:

  * score >= 0.65 — measured on 1,998 decided suggestions with each face held out of
    its own reference set: about 1 wrong in 100 at this level (P-I1; Steve accepted it);
  * the photo came through the ingest pipeline and is still **pending** in the inbox —
    never a photo the family can already see, so every auto tag passes under an admin's
    eyes (the inbox shows the name) before it is published;
  * the person is not a pet. The face model cannot tell dogs apart (§14.8), so a
    person marked as a pet (`person.notes == 'pet'`) is never auto-tagged.

What is written, and why it stays reversible:
  * `photo_person` with **`source='auto-face'`** and the face's box. Lightroom-imported
    tags were once written with the same source as hand-made ones and could never be
    told apart again; these always can (Review faces → Check accepted → auto-tagged).
  * the suggestion becomes `accepted` with `decided_by='auto-face'`, so `match_faces`
    never re-offers it and its score stays on record for the audit.
  * **Auto tags are not references.** `match_faces` skips `auto-face` rows when it builds
    each person's model, so a wrong auto tag cannot teach the matcher to repeat itself.
    Confirming one in the audit turns it into an ordinary tag, and a reference.

One short write transaction at the end; everything before it is reads.
"""
import argparse
import sys
from datetime import datetime, timezone

from app import models as m
from app.database import SessionLocal

THRESHOLD = 0.65
SOURCE_AUTO_FACE = "auto-face"
DECIDED_BY = "auto-face"


def run(threshold: float = THRESHOLD, dry_run: bool = False) -> dict:
    db = SessionLocal()
    try:
        rows = (db.query(m.FaceSuggestion, m.Face, m.Person.notes)
                .join(m.Face, m.Face.id == m.FaceSuggestion.face_id)
                .join(m.Photo, m.Photo.id == m.Face.photo_id)
                .join(m.Person, m.Person.id == m.FaceSuggestion.person_id)
                .filter(m.FaceSuggestion.status == m.FACE_PENDING,
                        m.FaceSuggestion.score >= threshold,
                        m.Photo.ingest_run_id.isnot(None),
                        m.Photo.visibility == m.VIS_PENDING)
                .order_by(m.FaceSuggestion.score.desc()).all())
        now = datetime.now(timezone.utc)
        tagged = boxed = pets = dup = 0
        who: dict[str, int] = {}
        seen: set[tuple[int, str]] = set()
        for sg, f, notes in rows:
            if (notes or "").strip().lower() == "pet":
                pets += 1
                continue
            key = (f.photo_id, sg.person_id)
            if key in seen:
                dup += 1                 # a second face of the same person in one photo:
                continue                 # one tag per (photo, person); leave it to a human
            seen.add(key)
            pp = db.get(m.PhotoPerson, key)
            if pp is None:
                db.add(m.PhotoPerson(photo_id=f.photo_id, person_id=sg.person_id,
                                     source=SOURCE_AUTO_FACE, uncertain=False,
                                     region_x=f.x, region_y=f.y, region_w=f.w, region_h=f.h))
                tagged += 1
            elif pp.region_w is None:
                # Already tagged by a human, just without a box: add the geometry and
                # leave the tag's source alone — the name was never the machine's call.
                pp.region_x, pp.region_y, pp.region_w, pp.region_h = f.x, f.y, f.w, f.h
                boxed += 1
            else:
                dup += 1
                continue
            sg.status, sg.decided_by, sg.decided_at = m.FACE_ACCEPTED, DECIDED_BY, now
            who[sg.person_id] = who.get(sg.person_id, 0) + 1
        st = {"threshold": threshold, "candidates": len(rows), "tagged": tagged,
              "boxes_added": boxed, "pets_skipped": pets, "left_for_review": dup}
        # Per-person counts: the quickest way to spot a pet that is not marked as one.
        print("autotag by person:", dict(sorted(who.items(), key=lambda kv: -kv[1])))
        if dry_run:
            db.rollback()
            print("autotag (dry run):", st)
        else:
            db.commit()
            print("autotag:", st)
        return st
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Auto-tag strong face matches (SPEC §15.10)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    a = ap.parse_args()
    run(threshold=a.threshold, dry_run=a.dry_run)
    sys.exit(0)
