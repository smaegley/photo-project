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
SUB_PER = 25               # one sub-centroid per ~25 references (0 disables splitting)
MAX_SUB = 5                # cap: past this it fits noise, not appearance eras
# Faces in clusters you bulk-ignored are re-asked only well above threshold. Ignoring a
# cluster is a real decision, so the bar to reopen it is "the better model is confident",
# not "the better model changed its mind slightly".
IGNORED_THRESHOLD = 0.55


def _iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _box(x, y, w, h):
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def _unit(v):
    n = np.linalg.norm(v)
    return v / n if n else v


def _kmeans(X, k, iters=25, seed=0):
    """Spherical k-means over unit embeddings — cosine, so the 'mean' is renormalised.

    Deterministic on `seed` so two runs produce the same sub-centroids and therefore the
    same suggestions; a queue that reshuffles itself between runs is unreviewable.
    """
    rng = np.random.default_rng(seed)
    C = X[rng.choice(len(X), size=min(k, len(X)), replace=False)].copy()
    for _ in range(iters):
        assign = (X @ C.T).argmax(1)
        for j in range(len(C)):
            members = X[assign == j]
            if len(members):
                C[j] = _unit(members.mean(0))
    return C


def run(threshold: float = DEFAULT_THRESHOLD, min_refs: int = MIN_REFS,
        dry_run: bool = False, sub_per: int = SUB_PER, max_sub: int = MAX_SUB,
        ignored_threshold: float = IGNORED_THRESHOLD) -> None:
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
        tagged = defaultdict(set)      # photo_id -> {person_id} tagged but with NO box
        boxed = set()                  # (photo_id, person_id) already carrying a box
        for pid, person, rw in db.query(m.PhotoPerson.photo_id, m.PhotoPerson.person_id,
                                        m.PhotoPerson.region_w).all():
            if rw is None:
                tagged[pid].add(person)
            else:
                boxed.add((pid, person))
        decided = {(fid, pers) for fid, pers, st in
                   db.query(m.FaceSuggestion.face_id, m.FaceSuggestion.person_id,
                            m.FaceSuggestion.status).all()
                   if st != m.FACE_PENDING}
        # Faces sitting in a cluster that was bulk-ignored — held to a higher bar below.
        ignored_faces = {fid for (fid,) in
                         db.query(m.Face.id)
                         .join(m.FaceCluster, m.Face.cluster_id == m.FaceCluster.id)
                         .filter(m.FaceCluster.status == "ignored").all()}
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

    # ---- build the person models ----
    # One mean per person is wrong for an archive spanning 1962–2026: it averages a
    # person's childhood and adulthood into a vector resembling neither. Measured on
    # Steve's 1,086 confirmed faces: mean 0.633 to his own centroid but p10 = 0.306, and
    # the low tail is almost all Mag* slides — child Steve, outvoted by adult digital.
    # Splitting each person's references into a few sub-centroids fixes that; the extra
    # cost is one more matmul against ~150 rows instead of ~90.
    cent, owner = [], []
    for j, p in enumerate(people):
        idxs = refs[p]
        k = 1 if sub_per < 1 else min(max_sub, max(1, len(idxs) // sub_per))
        for c in (_kmeans(emb[idxs], k) if k > 1 else [_unit(emb[idxs].mean(0))]):
            cent.append(c)
            owner.append(j)
    cent = np.stack(cent)
    owner = np.array(owner)

    # ---- score every unidentified face ----
    cand = np.array([i for i in range(len(faces)) if i not in identified], dtype=np.int64)
    sims = emb[cand] @ cent.T
    best = owner[sims.argmax(1)]        # sub-centroid -> the person that owns it
    score = sims.max(1)

    n_new = n_backfill = n_below = n_noop = n_ignored = 0
    rows = []
    for k, ci in enumerate(cand):
        if score[k] < threshold:
            n_below += 1
            continue
        person = people[best[k]]
        fid = int(face_ids[ci])
        if (fid, person) in decided:
            continue                          # human already ruled on this pair
        if fid in ignored_faces and score[k] < ignored_threshold:
            n_ignored += 1
            continue                          # dismissed cluster, not confident enough
        photo_id = faces[ci][1]
        # Skip no-ops: `photo_person` holds ONE region per (photo, person), so if that
        # person already has a box here, accepting would change nothing. Measured at 323
        # of 1,400 (23%) before this filter — a quarter of the queue costing clicks and
        # yielding nothing. The data model can't represent a second face of the same
        # person in one frame, so there is nothing lost by not asking.
        if (photo_id, person) in boxed:
            n_noop += 1
            continue
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
    print(f"person models:      {len(cent)} sub-centroids "
          f"({'one per person' if sub_per < 1 else f'~1 per {sub_per} refs, max {max_sub}'})")
    print(f"unidentified:       {len(cand)}")
    print(f"  suggestions:      {len(rows)}")
    print(f"    genuinely new:  {n_new}  (person not yet tagged on that photo)")
    print(f"    region backfill:{n_backfill}  (person already tagged; adds geometry only)")
    print(f"  below threshold:  {n_below}  -> unknown-face clustering (§14.7a)")
    print(f"  skipped as no-op: {n_noop}  (person already has a box on that photo)")
    print(f"  held back:        {n_ignored}  (in a cluster you ignored, under {ignored_threshold})")
    if not dry_run and rows:
        print("\nNext: slice 5 — the by-person confirm queue")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Match faces to enrolled people (SPEC §14 slice 4)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--min-refs", type=int, default=MIN_REFS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sub-per", type=int, default=SUB_PER,
                    help="references per sub-centroid; 0 = one centroid per person (old behaviour)")
    ap.add_argument("--max-sub", type=int, default=MAX_SUB)
    ap.add_argument("--ignored-threshold", type=float, default=IGNORED_THRESHOLD,
                    help="score needed to re-ask a face in a cluster you ignored")
    args = ap.parse_args()
    run(threshold=args.threshold, min_refs=args.min_refs, dry_run=args.dry_run,
        sub_per=args.sub_per, max_sub=args.max_sub,
        ignored_threshold=args.ignored_threshold)
