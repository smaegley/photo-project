"""Portable export/import of detected faces (SPEC §14.8a).

Detection costs ~82 minutes of CPU for the whole library, and
`scripts/load-prod-snapshot.sh` **replaces `data/photos.db` wholesale** — so without
this, every refresh of dev from prod throws that hour away. This makes the face index a
file you can put back.

    python -m app.faces_io --export                 # -> data/review/faces_export.npz
    python -m app.faces_io --import                 # restore after a dev refresh
    python -m app.faces_io --import --dry-run

**Keyed on `photo.source_file`, never `photo.id`** — that is the §14.8a rule, and it is
load-bearing rather than stylistic: row ids are assigned per database, so an export keyed
on them would silently reattach faces to *different photos* on the other side. Rows whose
`source_file` is absent on import are reported and skipped, which is what makes this safe
to restore onto a newer prod snapshot that has photos dev has never seen.

Format is a single compressed `.npz`: embeddings as one `float32[N,512]` array (28 MB
raw, and float data does not compress usefully, so a text encoding would only inflate it)
plus parallel arrays for the metadata. Not human-readable, unlike the review CSVs — a
deliberate exception, because this is machine state, not a review artifact.

Note this is the *dev-side* half of §14.8a. The prod-facing export is different and
smaller: confirmed `photo_person` tags only, no embeddings (prod never matches), and it
arrives with slice 5 once there are confirmations to send.
"""
import argparse
from pathlib import Path

import numpy as np

from app import models as m
from app.database import SessionLocal
from app.import_photos import REVIEW_DIR

DEFAULT_PATH = "faces_export.npz"
EMB_DIM = 512


def export_faces(path: Path) -> int:
    db = SessionLocal()
    try:
        rows = (db.query(m.Face.id, m.Face.x, m.Face.y, m.Face.w, m.Face.h,
                         m.Face.det_score, m.Face.detector_version, m.Face.embedding,
                         m.Photo.source_file)
                .join(m.Photo, m.Photo.id == m.Face.photo_id)
                .order_by(m.Face.id).all())
        # Which photos have been scanned, so a restore also brings back resume state —
        # otherwise the next detection run re-scans every faceless photo (~40% of them).
        scanned = (db.query(m.Photo.source_file, m.Photo.faces_scanned_version)
                   .filter(m.Photo.faces_scanned_version.isnot(None)).all())
    finally:
        db.close()

    if not rows:
        print("no faces to export")
        return 0

    emb = np.zeros((len(rows), EMB_DIM), dtype=np.float32)
    for i, r in enumerate(rows):
        if r[7]:
            emb[i] = np.frombuffer(r[7], dtype=np.float32)

    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        source_file=np.array([r[8] for r in rows]),
        x=np.array([r[1] for r in rows], dtype=np.float32),
        y=np.array([r[2] for r in rows], dtype=np.float32),
        w=np.array([r[3] for r in rows], dtype=np.float32),
        h=np.array([r[4] for r in rows], dtype=np.float32),
        det_score=np.array([r[5] if r[5] is not None else np.nan for r in rows], dtype=np.float32),
        detector_version=np.array([r[6] or "" for r in rows]),
        embedding=emb,
        scanned_source_file=np.array([s for s, _v in scanned]),
        scanned_version=np.array([v for _s, v in scanned]),
    )
    mb = path.stat().st_size / 1048576
    print(f"exported {len(rows)} faces + {len(scanned)} scan markers -> {path} ({mb:.1f} MB)")
    return len(rows)


def import_faces(path: Path, dry_run: bool = False) -> None:
    if not path.exists():
        print(f"export not found: {path}")
        return
    z = np.load(path, allow_pickle=False)
    # Materialise every array ONCE. NpzFile members are lazy: indexing z["embedding"][i]
    # inside a loop re-reads and re-decompresses the whole 28 MB array each time, which
    # turned a 20-second restore into one that ran past five minutes.
    src = z["source_file"]
    ax, ay, aw, ah = z["x"], z["y"], z["w"], z["h"]
    ascore, aver, aemb = z["det_score"], z["detector_version"], z["embedding"]
    scanned_src = z["scanned_source_file"] if "scanned_source_file" in z else np.array([])
    scanned_ver = z["scanned_version"] if "scanned_source_file" in z else np.array([])
    print(f"loaded {len(src)} faces from {path}")

    db = SessionLocal()
    try:
        photo_by_src = {sf: pid for pid, sf in db.query(m.Photo.id, m.Photo.source_file).all()}
        existing = {(pid, round(float(x), 5), round(float(y), 5))
                    for pid, x, y in db.query(m.Face.photo_id, m.Face.x, m.Face.y).all()}

        added = skipped = unknown = 0
        unknown_examples: list[str] = []
        batch = []
        for i in range(len(src)):
            pid = photo_by_src.get(str(src[i]))
            if pid is None:
                unknown += 1
                if len(unknown_examples) < 5:
                    unknown_examples.append(str(src[i]))
                continue
            key = (pid, round(float(ax[i]), 5), round(float(ay[i]), 5))
            if key in existing:          # already present — restore is idempotent
                skipped += 1
                continue
            score = float(ascore[i])
            batch.append(m.Face(
                photo_id=pid,
                x=float(ax[i]), y=float(ay[i]),
                w=float(aw[i]), h=float(ah[i]),
                det_score=None if np.isnan(score) else score,
                embedding=aemb[i].astype(np.float32).tobytes(),
                detector_version=str(aver[i]) or None))
            existing.add(key)
            added += 1
            if len(batch) >= 1000 and not dry_run:
                db.add_all(batch); db.commit(); batch = []
        if batch and not dry_run:
            db.add_all(batch); db.commit()

        # Restore resume state too, so the next detection pass doesn't redo faceless photos.
        # One bulk UPDATE per detector version, not one query per photo (6,598 of those
        # was the other half of the slowness).
        marks = 0
        by_ver: dict[str, list[int]] = {}
        for sf, ver in zip(scanned_src, scanned_ver):
            pid = photo_by_src.get(str(sf))
            if pid is not None:
                by_ver.setdefault(str(ver), []).append(pid)
                marks += 1
        if not dry_run:
            for ver, pids in by_ver.items():
                for chunk in (pids[i:i + 500] for i in range(0, len(pids), 500)):
                    db.query(m.Photo).filter(m.Photo.id.in_(chunk)).update(
                        {"faces_scanned_version": ver}, synchronize_session=False)
            db.commit()
    finally:
        db.close()

    tag = " (DRY RUN)" if dry_run else ""
    print(f"\n=== IMPORT FACES DUMP{tag} ===")
    print(f"faces added:        {added}")
    print(f"already present:    {skipped}")
    print(f"scan markers set:   {marks}")
    print(f"unknown source_file:{unknown}  (photos not in this DB — safely skipped)")
    for s in unknown_examples:
        print(f"    e.g. {s}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Export/import detected faces (SPEC §14.8a)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", action="store_true")
    g.add_argument("--import", dest="do_import", action="store_true")
    ap.add_argument("--path", type=Path, default=Path(DEFAULT_PATH))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    p = args.path if args.path.is_absolute() else REVIEW_DIR / args.path.name
    if args.export:
        export_faces(p)
    else:
        import_faces(p, dry_run=args.dry_run)
