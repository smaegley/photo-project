"""Batch pre-generate slide derivatives (thumbnails + display images).

Run as a **deploy step** after a slide rsync to avoid the lazy first-view
regeneration burst during browsing. Lives in the backend package so it runs inside
the prod container:

    docker compose exec api python -m app.prewarm            # regenerate stale only
    docker compose exec api python -m app.prewarm --force    # regenerate everything

`importer/make_thumbnails.py` is the dev-side wrapper over this. Staleness-aware
(same predicate as the server) — a plain run only touches missing/zero-byte/stale
derivatives, so it's cheap to run on every deploy.

A run also **stamps `photo.file_version`** (`stamp_versions`, SPEC §13.4), which is
what lets the serving path emit `?v=` from a column instead of stat'ing every master
during serialization. Also idempotent, so the deploy-step guidance is unchanged.
"""
import sys
import time

from app import derivatives
from app.config import settings


def prewarm_all(force: bool = False) -> tuple[int, int, int]:
    slides = sorted(settings.slides_dir.glob("Mag*/Mag*_Slide*.JPG"))
    kinds = [
        (settings.thumbnails_dir, derivatives.THUMB_MAX),
        (settings.display_dir, derivatives.DISPLAY_MAX),
    ]
    made = skipped = errors = 0
    t0 = time.time()
    for i, src in enumerate(slides, 1):
        for out_dir, max_edge in kinds:
            cache = out_dir / src.name
            try:
                if force:
                    derivatives.generate(src, cache, max_edge)
                    made += 1
                elif derivatives.ensure(src, cache, max_edge):
                    made += 1
                else:
                    skipped += 1
            except Exception as e:  # noqa: BLE001
                print(f"  ! {src.name}: {type(e).__name__}: {e}", file=sys.stderr)
                errors += 1
        if i % 200 == 0:
            print(f"  {i}/{len(slides)} ...")

    # Non-slide photos (scan/digital, SPEC §12.6): keyed by the path-based cache
    # name the image server uses, located by DB storage_path (not a filename glob).
    from app.database import SessionLocal
    from app import models as m
    n_scan = 0
    with SessionLocal() as db:
        rows = (db.query(m.Photo.source_file, m.Photo.storage_path)
                .filter(m.Photo.origin != "slide",
                        m.Photo.storage_path.isnot(None),
                        m.Photo.storage_backend == "local").all())
    for source_file, storage_path in rows:
        src = settings.library_root_path / storage_path
        if not src.exists():
            continue
        n_scan += 1
        for out_dir, max_edge in kinds:
            cache = out_dir / derivatives.safe_key(source_file)
            try:
                if force:
                    derivatives.generate(src, cache, max_edge)
                    made += 1
                elif derivatives.ensure(src, cache, max_edge):
                    made += 1
                else:
                    skipped += 1
            except Exception as e:  # noqa: BLE001
                print(f"  ! {source_file}: {type(e).__name__}: {e}", file=sys.stderr)
                errors += 1

    print(f"derivatives: {made} made, {skipped} fresh/skipped, {errors} errors "
          f"in {time.time()-t0:.1f}s  ({len(slides)} slides + {n_scan} scan/digital)")
    return made, skipped, errors


def stamp_versions() -> tuple[int, int]:
    """Record each local master's change-token in `photo.file_version` (SPEC §13.4).

    This is what actually retires the per-photo stat: until a row is stamped,
    `storage.master_version` falls back to probing the master during serialization.
    Idempotent — only writes rows whose token actually moved, so a re-run after a
    rotate or a re-export costs one stat per photo and no writes.

    B2-backed rows are skipped: their token is the object ETag, recorded at import
    and refreshed by the (slice 5) B2 prewarm from the same response that fetches
    the bytes — stamping them here would mean a HEAD per row for no benefit.
    """
    from app.database import SessionLocal
    from app import models as m
    from app import storage

    stamped = unchanged = 0
    with SessionLocal() as db:
        rows = (db.query(m.Photo)
                .filter(m.Photo.storage_path.isnot(None),
                        m.Photo.storage_backend == "local").all())
        for p in rows:
            token = storage.probe_version(p)
            if token and token != p.file_version:
                p.file_version = token
                stamped += 1
            else:
                unchanged += 1
        db.commit()
    print(f"file_version: {stamped} stamped, {unchanged} unchanged")
    return stamped, unchanged


if __name__ == "__main__":
    prewarm_all(force="--force" in sys.argv)
    stamp_versions()
