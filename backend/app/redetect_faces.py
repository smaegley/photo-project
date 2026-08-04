"""Second-pass face detection at higher resolution (SPEC §14).

The first pass ran SCRFD at `det_size=(640,640)` against 2560px display derivatives — a
4x downscale that throws small faces away. Measured on known misses, raising det_size
recovers roughly a third of them, but **non-monotonically**: a face found at 1280 can be
lost again at 1920, so neither size dominates and only the union wins.

Re-running the whole library at both sizes costs ~13x the original 56-minute pass to
mostly harvest small background faces. This targets the photos where a miss is already
evidenced instead:

- a boxed `photo_person` that overlaps **no** detected face — a human said "a face is
  here" and the detector disagreed
- a photo with **zero** detected faces at all, which the first rule can't see because a
  photo nobody has tagged has no box to contradict

    python -m app.redetect_faces [--sizes 1280,1920] [--dry-run] [--limit N]

Additive and idempotent: a new detection is written only if it overlaps no existing face
on that photo, so re-running finds nothing new. Rows carry `detector_version` tagged with
the det_size that produced them, and `photo.faces_scanned_version` is deliberately left
alone — this is a supplement to the baseline pass, not a replacement for it.

New faces get no suggestions here. Run `python -m app.match_faces` afterwards to score
them against the person models.
"""
import argparse
import warnings
from collections import defaultdict

import numpy as np

from app import models as m
from app.database import SessionLocal
from app.detect_faces import DETECTOR_VERSION, display_path
from app.match_faces import _box, _iou

DEFAULT_SIZES = (1280, 1920)
DEDUPE_IOU = 0.3          # same overlap rule the matcher links regions with


def _targets(db):
    """Photos where a miss is already evidenced — see the module docstring."""
    by_photo = defaultdict(list)
    for pid, x, y, w, h in db.query(m.Face.photo_id, m.Face.x, m.Face.y,
                                    m.Face.w, m.Face.h).all():
        by_photo[pid].append(_box(x, y, w, h))

    unlinked = set()
    for pid, rx, ry, rw, rh in (
            db.query(m.PhotoPerson.photo_id, m.PhotoPerson.region_x,
                     m.PhotoPerson.region_y, m.PhotoPerson.region_w,
                     m.PhotoPerson.region_h)
            .filter(m.PhotoPerson.region_w.isnot(None)).all()):
        g = _box(rx, ry, rw, rh)
        if max([_iou(g, b) for b in by_photo.get(pid, [])], default=0) <= DEDUPE_IOU:
            unlinked.add(pid)

    scanned = {pid for (pid,) in db.query(m.Photo.id)
               .filter(m.Photo.faces_scanned_version.isnot(None)).all()}
    no_faces = scanned - set(by_photo)
    return unlinked | no_faces, by_photo


def run(sizes=DEFAULT_SIZES, dry_run: bool = False, limit: int | None = None) -> None:
    warnings.filterwarnings("ignore")
    import cv2

    db = SessionLocal()
    try:
        target_ids, by_photo = _targets(db)
        ids = sorted(target_ids)[:limit] if limit else sorted(target_ids)
        photos = {p.id: (p.source_file, p.file_version, p.storage_backend, p.origin)
                  for p in db.query(m.Photo).filter(m.Photo.id.in_(ids)).all()}
    finally:
        db.close()

    print(f"targets: {len(ids)} photos  ·  sizes: {', '.join(map(str, sizes))}")
    if dry_run:
        print("(dry run — nothing will be written)")

    from insightface.app import FaceAnalysis

    # Existing geometry per photo, extended as we go so two det_sizes can't both write
    # the same face. Seeded from the baseline pass.
    known = {pid: list(by_photo.get(pid, [])) for pid in ids}
    added = defaultdict(int)
    n_photos_gained = 0
    missing = 0

    for size in sizes:
        fa = FaceAnalysis(name="buffalo_l", allowed_modules=["detection", "recognition"],
                          providers=["CPUExecutionProvider"])
        fa.prepare(ctx_id=-1, det_size=(size, size))
        rows = []
        for n, pid in enumerate(ids, 1):
            src, fv, backend, _origin = photos[pid]
            path = display_path(src, fv, backend)
            if not path.exists():
                missing += 1
                continue
            img = cv2.imread(str(path))
            if img is None:
                missing += 1
                continue
            H, W = img.shape[:2]
            gained = False
            for f in fa.get(img):
                x1, y1, x2, y2 = f.bbox
                fb = (x1 / W, y1 / H, x2 / W, y2 / H)
                if max([_iou(fb, b) for b in known[pid]], default=0) > DEDUPE_IOU:
                    continue                       # already known from another pass
                known[pid].append(fb)
                gained = True
                emb = np.asarray(f.normed_embedding, dtype=np.float32)
                rows.append(m.Face(
                    photo_id=pid,
                    x=float((fb[0] + fb[2]) / 2), y=float((fb[1] + fb[3]) / 2),
                    w=float(fb[2] - fb[0]), h=float(fb[3] - fb[1]),
                    det_score=float(f.det_score), embedding=emb.tobytes(),
                    detector_version=f"{DETECTOR_VERSION}@{size}"))
                added[size] += 1
            n_photos_gained += gained
            if n % 100 == 0:
                print(f"  {size}px: {n}/{len(ids)} photos, {added[size]} new faces",
                      flush=True)
        if rows and not dry_run:
            db = SessionLocal()
            try:
                db.add_all(rows)
                db.commit()
            finally:
                db.close()
        print(f"  {size}px done: {added[size]} new faces", flush=True)

    total = sum(added.values())
    print("\n=== RE-DETECT ===")
    for s in sizes:
        print(f"  {s}px added:       {added[s]}")
    print(f"  total new faces:  {total}")
    print(f"  photos improved:  {n_photos_gained} of {len(ids)}")
    if missing:
        print(f"  skipped (no derivative): {missing}")
    if total and not dry_run:
        print("\nNext: python -m app.match_faces   (scores the new faces against people)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Higher-resolution second-pass detection (SPEC §14)")
    ap.add_argument("--sizes", default=",".join(map(str, DEFAULT_SIZES)))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    run(sizes=tuple(int(s) for s in args.sizes.split(",") if s.strip()),
        dry_run=args.dry_run, limit=args.limit)
