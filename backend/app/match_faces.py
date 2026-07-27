"""Match detected faces against enrolled people (SPEC §14, slice 4).

Builds one **centroid embedding per person** from the faces Steve has already confirmed,
scores every *unidentified* face against them, and writes a `face_suggestion` row for
anything clearing the threshold. Nothing is applied — a suggestion is a question, and
§14.8 #2 is the answer to why: at the measured 0.45, ~4.6% of complete strangers still
score above it (P-F3), so human confirmation is the defence, not the threshold.

    python -m app.match_faces [--threshold 0.45] [--dry-run] [--min-refs 3]

Three populations, and the distinction matters for reading the output:

- **Enrolled** — a `face` row whose box overlaps a confirmed `photo_person` region.
  These *are* the reference set; they are never suggested, they define the centroids.
- **Region backfill** — an unidentified face on a photo where that person is already
  tagged (but with no geometry). Confirming adds a box, not a name, so it is low-risk
  and high-value; counted separately so it doesn't look like new information.
- **Genuinely new** — an unidentified face on a photo where that person is *not* tagged.
  This is the pile that actually grows the archive's knowledge, and the pile to read
  most carefully.

Faces scoring below the threshold get no suggestion at all — they fall through to
unknown-face clustering (§14.7a), which is where background strangers get bulk-ignored.

Idempotent: re-running refreshes `pending` suggestions and **never touches a suggestion
already accepted or rejected**, so a human decision is never silently reopened.
"""
import argparse
from collections import defaultdict

import numpy as np

from app import models as m
from app.database import SessionLocal

DEFAULT_THRESHOLD = 0.45   # P-F3: keeps 93.6% of genuine matches, admits 4.6% of strangers
MIN_REFS = 3               # a centroid from 1–2 examples is noise, not a person
IOU_LINK = 0.3             # same overlap rule P-F3/P-F4 validated at 100% recall
EMB_DIM = 512


def _iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _box(x, y, w, h):
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def run(threshold: float = DEFAULT_THRESHOLD, min_refs: int = MIN_REFS,
        dry_run: bool = False) -> None:
    db = SessionLocal()
    try:
        faces = db.query(m.Face.id, m.Face.photo_id, m.Face.x, m.Face.y,
                         m.Face.w, m.Face.h, m.Face.embedding).all()
        if not faces:
            print("no faces — run: python -m app.detect_faces")
            return
        regions = (db.query(m.PhotoPerson.photo_id, m.PhotoPerson.person_id,
                            m.PhotoPerson.region_x, m.PhotoPerson.region_y,
                            m.PhotoPerson.region_w, m.PhotoPerson.region_h)
                   .filter(m.PhotoPerson.region_w.isnot(None)).all())
        tagged = defaultdict(set)      # photo_id -> {person_id} (any tag, box or not)
        for pid, person in db.query(m.PhotoPerson.photo_id, m.PhotoPerson.person_id).all():
            tagged[pid].add(person)
        decided = {(fid, pers) for fid, pers, st in
                   db.query(m.FaceSuggestion.face_id, m.FaceSuggestion.person_id,
                            m.FaceSuggestion.status).all()
                   if st != m.FACE_PENDING}
    finally:
        db.close()

    emb = np.zeros((len(faces), EMB_DIM), dtype=np.float32)
    face_ids = np.empty(len(faces), dtype=np.int64)
    by_photo = defaultdict(list)
    for i, f in enumerate(faces):
        face_ids[i] = f[0]
        if f[6]:
            emb[i] = np.frombuffer(f[6], dtype=np.float32)
        by_photo[f[1]].append((i, _box(f[2], f[3], f[4], f[5])))

    # ---- link confirmed regions to detected faces: these become the reference set ----
    refs = defaultdict(list)
    identified: dict[int, str] = {}          # face index -> person it already is
    for photo_id, person, rx, ry, rw, rh in regions:
        g = _box(rx, ry, rw, rh)
        best_i, best_iou = None, IOU_LINK
        for i, fb in by_photo.get(photo_id, ()):
            v = _iou(g, fb)
            if v > best_iou:
                best_i, best_iou = i, v
        if best_i is not None:
            refs[person].append(best_i)
            identified[best_i] = person

    people = [p for p, idxs in refs.items() if len(idxs) >= min_refs]
    if not people:
        print(f"no person has >= {min_refs} linked reference faces — nothing to match against")
        return
    cent = np.zeros((len(people), EMB_DIM), dtype=np.float32)
    for j, p in enumerate(people):
        c = emb[refs[p]].mean(0)
        n = np.linalg.norm(c)
        cent[j] = c / n if n else c

    # ---- score every unidentified face ----
    cand = np.array([i for i in range(len(faces)) if i not in identified], dtype=np.int64)
    sims = emb[cand] @ cent.T
    best = sims.argmax(1)
    score = sims.max(1)

    n_new = n_backfill = n_below = 0
    rows = []
    for k, ci in enumerate(cand):
        if score[k] < threshold:
            n_below += 1
            continue
        person = people[best[k]]
        fid = int(face_ids[ci])
        if (fid, person) in decided:
            continue                          # human already ruled on this pair
        photo_id = faces[ci][1]
        backfill = person in tagged.get(photo_id, ())
        n_backfill += backfill
        n_new += not backfill
        rows.append((fid, person, float(score[k])))

    if not dry_run:
        db = SessionLocal()
        try:
            # Refresh pending suggestions only; accepted/rejected are never reopened.
            db.query(m.FaceSuggestion).filter(
                m.FaceSuggestion.status == m.FACE_PENDING).delete(synchronize_session=False)
            db.add_all([m.FaceSuggestion(face_id=f, person_id=p, score=s) for f, p, s in rows])
            db.commit()
        finally:
            db.close()

    tag = " (DRY RUN)" if dry_run else ""
    print(f"=== MATCH FACES{tag} ===")
    print(f"threshold:          {threshold}  (min {min_refs} refs per person)")
    print(f"faces total:        {len(faces)}")
    print(f"already identified: {len(identified)}  (linked to a confirmed region — the reference set)")
    print(f"people enrolled:    {len(people)} of {len(refs)} with any reference")
    print(f"unidentified:       {len(cand)}")
    print(f"  suggestions:      {len(rows)}")
    print(f"    genuinely new:  {n_new}  (person not yet tagged on that photo)")
    print(f"    region backfill:{n_backfill}  (person already tagged; adds geometry only)")
    print(f"  below threshold:  {n_below}  -> unknown-face clustering (§14.7a)")
    if not dry_run and rows:
        print("\nNext: slice 5 — the by-person confirm queue")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Match faces to enrolled people (SPEC §14 slice 4)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--min-refs", type=int, default=MIN_REFS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(threshold=args.threshold, min_refs=args.min_refs, dry_run=args.dry_run)
