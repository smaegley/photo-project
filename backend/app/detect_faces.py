"""Detect + embed every face in the library (SPEC §14, slice 3).

Reads the **local display derivatives** — never the masters — and writes one `face` row
per detection, carrying the box, the detector's confidence, and a 512-float ArcFace
embedding. Nothing is matched or suggested here; this only builds the index that slice 4
scores against.

    python -m app.detect_faces [--limit N] [--origin slide|scan|digital] [--force]

**Runs on dev only** (SPEC §14.8a). ~2 photos/s and ~650 MB peak on VM 201, so a full
6,598-photo pass is roughly an hour. Prod never runs this — it receives confirmed tags,
not faces.

Why the display derivative rather than the master:
- it is already local, already sized, and needs no B2 fetch;
- P-F4 measured **100% recall** against known catalog regions on exactly these files,
  sampled from the most crowded photos in the library, so nothing is lost by using them.

**Idempotent and resumable.** A photo whose faces were already detected by this
`detector_version` is skipped, so an interrupted run costs only the photo it was on.
`--force` re-detects, replacing that photo's rows for this detector version — useful
after a model upgrade, and the version tag is what keeps two models' output apart.
"""
import argparse
import sys
import time
import warnings

import numpy as np

from app import derivatives, models as m
from app.config import settings
from app.database import SessionLocal

# Identifies which model produced a row, so a future model upgrade can re-detect
# without silently mixing incompatible embeddings (cosine distance between different
# ArcFace variants is meaningless).
DETECTOR_VERSION = "buffalo_l/scrfd_10g+w600k_r50"
DET_SIZE = (640, 640)


def _load_app():
    warnings.filterwarnings("ignore")
    from insightface.app import FaceAnalysis
    fa = FaceAnalysis(name="buffalo_l", allowed_modules=["detection", "recognition"],
                      providers=["CPUExecutionProvider"])
    fa.prepare(ctx_id=-1, det_size=DET_SIZE)
    return fa


def display_path(source_file: str, file_version: str | None, backend: str,
                 rotation: int = 0):
    """The local display derivative for any origin — slides, scans and B2-backed digital
    all resolve through one rule (remote masters version their cache key, §13.4; a
    rotation override folds into that key, so pass photo.rotation for b2 rows)."""
    token = derivatives.version_token(
        type("P", (), {"file_version": file_version, "rotation": rotation})())
    return settings.display_dir / derivatives.cache_key(
        source_file, token, versioned=(backend == "b2"))


def run(limit: int | None = None, origin: str | None = None, force: bool = False) -> None:
    db = SessionLocal()
    try:
        q = db.query(m.Photo.id, m.Photo.source_file, m.Photo.file_version,
                     m.Photo.storage_backend, m.Photo.rotation)
        if origin:
            q = q.filter(m.Photo.origin == origin)
        rows = q.order_by(m.Photo.id).all()

        # Resume on "scanned", not "has faces" — a faceless photo is still done.
        done = {pid for (pid,) in db.query(m.Photo.id)
                .filter(m.Photo.faces_scanned_version == DETECTOR_VERSION).all()}
    finally:
        db.close()

    todo = rows if force else [r for r in rows if r[0] not in done]
    if limit:
        todo = todo[:limit]
    print(f"photos: {len(rows)} total, {len(done)} already detected, {len(todo)} to do")
    if not todo:
        print("nothing to do")
        return

    from PIL import Image
    app = _load_app()
    print(f"model ready ({DETECTOR_VERSION})", flush=True)

    n_photos = n_faces = n_missing = n_err = 0
    t0 = time.time()
    batch: list[m.Face] = []
    db = SessionLocal()
    try:
        for i, (pid, sf, fv, backend, rot) in enumerate(todo, 1):
            p = display_path(sf, fv, backend, rot)
            if not p.exists():
                n_missing += 1
                continue
            try:
                im = np.asarray(Image.open(p).convert("RGB"))[:, :, ::-1]  # BGR
                H, W = im.shape[:2]
                faces = app.get(im)
            except Exception as e:  # noqa: BLE001
                print(f"  ! {sf}: {type(e).__name__}: {e}", file=sys.stderr)
                n_err += 1
                continue

            if force:
                (db.query(m.Face)
                 .filter(m.Face.photo_id == pid,
                         m.Face.detector_version == DETECTOR_VERSION).delete())
            for f in faces:
                x1, y1, x2, y2 = f.bbox
                # store normalized centre+size, matching photo_person.region_*
                batch.append(m.Face(
                    photo_id=pid,
                    x=float((x1 + x2) / 2 / W), y=float((y1 + y2) / 2 / H),
                    w=float(abs(x2 - x1) / W), h=float(abs(y2 - y1) / H),
                    det_score=float(f.det_score),
                    embedding=np.asarray(f.normed_embedding, dtype=np.float32).tobytes(),
                    detector_version=DETECTOR_VERSION))
            db.query(m.Photo).filter(m.Photo.id == pid).update(
                {"faces_scanned_version": DETECTOR_VERSION})
            n_faces += len(faces)
            n_photos += 1

            # Commit in chunks: an interrupted run keeps everything up to the last chunk,
            # which is what makes this resumable rather than all-or-nothing.
            if len(batch) >= 500:
                db.add_all(batch); db.commit(); batch = []
            if i % 200 == 0:
                rate = i / max(time.time() - t0, 1e-3)
                eta = (len(todo) - i) / max(rate, 1e-3) / 60
                print(f"  {i}/{len(todo)}  {rate:.1f} ph/s  {n_faces} faces  ETA {eta:.0f}m",
                      flush=True)
        if batch:
            db.add_all(batch); db.commit()
    finally:
        db.close()

    el = time.time() - t0
    print(f"\n=== DETECT FACES ===")
    print(f"photos processed: {n_photos}")
    print(f"faces found:      {n_faces}  ({n_faces/max(n_photos,1):.2f} per photo)")
    print(f"no derivative:    {n_missing}")
    print(f"errors:           {n_err}")
    print(f"elapsed:          {el/60:.1f} min  ({n_photos/max(el,1e-3):.2f} photos/s)")
    if n_faces:
        print("\nNext: slice 4 — match these against enrolled people (threshold 0.45, P-F3)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Detect + embed faces (SPEC §14 slice 3)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--origin", choices=["slide", "scan", "digital"], default=None)
    ap.add_argument("--force", action="store_true",
                    help="re-detect photos already done for this detector version")
    args = ap.parse_args()
    run(limit=args.limit, origin=args.origin, force=args.force)
