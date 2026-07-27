"""Group unidentified faces into probable-same-person clusters (SPEC §14.7a).

After matching, ~4,150 faces belong to nobody the archive knows. Triaging those one at a
time would be unusable, and most of them are background strangers Steve explicitly never
wants to name. Clustering turns that pile into a few hundred decisions — "that's cousin
Dave, 47 photos" or "crowd at the zoo, ignore" — made once per cluster.

    python -m app.cluster_faces [--threshold 0.55] [--dry-run]

**An `ignored` cluster keeps its centroid, and that is the point.** On a later run a new
unnamed face near an ignored centroid joins it silently instead of being re-asked. Without
that, every weekly sync re-surfaces the same strangers and the feature is worthless.

`prominence` = median(face area) x median(det_score). Background faces are smaller and
less confidently detected, so sorting by it puts nameable people on top and buries the
crowd tail. Note the measured size distribution (§14 slice 3) shows only 5% of faces fall
under 2% of frame width — a size *floor* is a weak filter, so prominence ordering, not
thresholding, is what does the work.

Clustering is **greedy single-pass** against running centroids rather than full
agglomerative: at this scale the quality difference is not worth a scikit-learn
dependency in an image-serving container, and a human confirms every result anyway.
"""
import argparse
from collections import defaultdict

import numpy as np

from app import models as m
from app.database import SessionLocal

DEFAULT_THRESHOLD = 0.55   # tighter than the 0.45 match threshold: grouping strangers
EMB_DIM = 512              # wrongly is more annoying than leaving them apart


def run(threshold: float = DEFAULT_THRESHOLD, dry_run: bool = False) -> None:
    db = SessionLocal()
    try:
        # Faces that are neither already identified nor carrying a suggestion.
        suggested = {r[0] for r in db.query(m.FaceSuggestion.face_id).distinct().all()}
        regioned = defaultdict(list)
        for pid, rx, ry, rw, rh in db.query(
                m.PhotoPerson.photo_id, m.PhotoPerson.region_x, m.PhotoPerson.region_y,
                m.PhotoPerson.region_w, m.PhotoPerson.region_h
        ).filter(m.PhotoPerson.region_w.isnot(None)).all():
            regioned[pid].append((rx, ry, rw, rh))

        faces = db.query(m.Face.id, m.Face.photo_id, m.Face.x, m.Face.y, m.Face.w,
                         m.Face.h, m.Face.det_score, m.Face.embedding).all()

        # Preserve decisions: a face already in a named/ignored cluster stays put, and
        # those centroids are what new faces get tested against first.
        keep = {c.id: c for c in db.query(m.FaceCluster)
                .filter(m.FaceCluster.status != m.CLUSTER_PENDING).all()}
        assigned = {fid for (fid,) in db.query(m.Face.id)
                    .filter(m.Face.cluster_id.in_(list(keep)) if keep else False).all()}
    finally:
        db.close()

    def overlaps_region(photo_id, x, y, w, h):
        for rx, ry, rw, rh in regioned.get(photo_id, ()):
            if abs(rx - x) < (rw + w) / 2 and abs(ry - y) < (rh + h) / 2:
                return True
        return False

    todo = [f for f in faces
            if f[0] not in suggested and f[0] not in assigned
            and not overlaps_region(f[1], f[2], f[3], f[4], f[5])]
    if not todo:
        print("no unidentified faces to cluster")
        return
    print(f"clustering {len(todo)} unidentified faces (threshold {threshold})")

    emb = np.zeros((len(todo), EMB_DIM), dtype=np.float32)
    for i, f in enumerate(todo):
        if f[7]:
            emb[i] = np.frombuffer(f[7], dtype=np.float32)

    # Seed with the centroids of decided clusters so their decisions absorb new faces.
    seeds, seed_ids = [], []
    for cid, c in keep.items():
        if c.centroid:
            seeds.append(np.frombuffer(c.centroid, dtype=np.float32))
            seed_ids.append(cid)
    cents = list(seeds)
    members: list[list[int]] = [[] for _ in seeds]
    existing_n = len(seeds)

    order = np.argsort(-np.array([(f[4] * f[5]) * (f[6] or 0.0) for f in todo]))
    for i in order:                       # most prominent first -> better seed centroids
        v = emb[i]
        if cents:
            sims = np.stack(cents) @ v
            j = int(sims.argmax())
            if sims[j] >= threshold:
                members[j].append(i)
                k = len(members[j])
                cents[j] = (cents[j] * (k - 1) + v) / k
                n = np.linalg.norm(cents[j])
                if n:
                    cents[j] = cents[j] / n
                continue
        cents.append(v.copy())
        members.append([i])

    n_absorbed = sum(len(members[j]) for j in range(existing_n))
    new_groups = [(cents[j], members[j]) for j in range(existing_n, len(members)) if members[j]]

    if not dry_run:
        db = SessionLocal()
        try:
            # Drop only pending clusters; named/ignored survive with their decisions.
            pend = [c.id for (c,) in [(x,) for x in db.query(m.FaceCluster)
                                      .filter(m.FaceCluster.status == m.CLUSTER_PENDING).all()]]
            if pend:
                db.query(m.Face).filter(m.Face.cluster_id.in_(pend)).update(
                    {"cluster_id": None}, synchronize_session=False)
                db.query(m.FaceCluster).filter(m.FaceCluster.id.in_(pend)).delete(
                    synchronize_session=False)
            db.flush()

            for cvec, idxs in new_groups:
                areas = [todo[i][4] * todo[i][5] for i in idxs]
                scores = [todo[i][6] or 0.0 for i in idxs]
                cl = m.FaceCluster(status=m.CLUSTER_PENDING,
                                   centroid=cvec.astype(np.float32).tobytes(),
                                   n_faces=len(idxs),
                                   prominence=float(np.median(areas) * np.median(scores)))
                db.add(cl)
                db.flush()
                db.query(m.Face).filter(m.Face.id.in_([todo[i][0] for i in idxs])).update(
                    {"cluster_id": cl.id}, synchronize_session=False)
            # Faces absorbed into an already-decided cluster.
            for j in range(existing_n):
                if members[j]:
                    db.query(m.Face).filter(
                        m.Face.id.in_([todo[i][0] for i in members[j]])).update(
                        {"cluster_id": seed_ids[j]}, synchronize_session=False)
            db.commit()
        finally:
            db.close()

    sizes = sorted((len(v) for _c, v in new_groups), reverse=True)
    singles = sum(1 for s in sizes if s == 1)
    tag = " (DRY RUN)" if dry_run else ""
    print(f"\n=== CLUSTER FACES{tag} ===")
    print(f"faces clustered:    {len(todo)}")
    print(f"new clusters:       {len(new_groups)}")
    print(f"  multi-face:       {len(sizes) - singles}")
    print(f"  singletons:       {singles}  (one-off faces — usually crowd)")
    print(f"largest clusters:   {sizes[:8]}")
    if existing_n:
        print(f"absorbed into already-decided clusters: {n_absorbed}")
    if not dry_run:
        print("\nNext: name or ignore them in the admin queue (SPEC §14.7a)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Cluster unidentified faces (SPEC §14.7a)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(threshold=args.threshold, dry_run=args.dry_run)
