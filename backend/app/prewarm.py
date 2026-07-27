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

# iPhone HEICs are ~3% of the digital set and Pillow can't open them unaided (SPEC
# §13.14 slice 5). Registering the opener is a no-op for every other format, so it is
# done once here rather than guarded at each call site. Optional at import time so a
# dev box without the wheel still prewarms slides/scans.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIF_OK = True
except ImportError:  # pragma: no cover
    HEIF_OK = False


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


def prewarm_b2(force: bool = False, limit: int | None = None) -> tuple[int, int, int]:
    """Fetch each B2-backed master **once** and build its derivatives (SPEC §13.7).

    This is the only place the app reads B2 in bulk, and the reason browsing never
    does: one GET per photo, both derivatives generated from the same in-memory bytes,
    then the master is discarded. Steady-state serving is identical to a slide.

    Cache keys carry `file_version` for remote masters (§13.4), so a changed master
    lands on a new key and this simply generates it — no staleness comparison is
    possible or needed. `--force` regenerates regardless, which costs a re-fetch.

    Also fills `width`/`height` while the image is decoded — free here, and otherwise
    unknowable for a digital row without another B2 round trip.
    """
    import io
    from PIL import Image, ImageFile
    from app.database import SessionLocal
    from app import models as m, storage

    def _decode(buf):
        """Open an image, tolerating a truncated file but never hiding it.

        Strict first: a clean file decodes normally. Only on a truncation error do we
        retry with `LOAD_TRUNCATED_IMAGES`, which recovers everything up to the damage
        (a JPEG missing its last bytes loses at most a sliver of the bottom edge). The
        photo is salvaged *and* reported, so a corrupt master in the archive surfaces
        instead of silently becoming a slightly-wrong thumbnail. Seen once in the
        2026-07-27 run: a B2 object short 48 bytes — the stored file, not the transfer.
        """
        try:
            im = Image.open(buf); im.load()
            return im, False
        except OSError as e:
            if "truncated" not in str(e).lower():
                raise
            ImageFile.LOAD_TRUNCATED_IMAGES = True
            try:
                buf.seek(0)
                im = Image.open(buf); im.load()
                return im, True
            finally:
                ImageFile.LOAD_TRUNCATED_IMAGES = False

    kinds = [
        (settings.thumbnails_dir, derivatives.THUMB_MAX),
        (settings.display_dir, derivatives.DISPLAY_MAX),
    ]
    with SessionLocal() as db:
        rows = (db.query(m.Photo.id, m.Photo.source_file, m.Photo.storage_path,
                         m.Photo.file_version, m.Photo.width)
                .filter(m.Photo.storage_backend == "b2",
                        m.Photo.storage_path.isnot(None))
                .order_by(m.Photo.id).all())
    if limit:
        rows = rows[:limit]
    if not rows:
        print("no b2-backed photos — nothing to fetch")
        return 0, 0, 0

    fetched = skipped = errors = salvaged = 0
    truncated: list[str] = []
    dims: dict[int, tuple[int, int]] = {}
    t0 = time.time()
    for i, (pid, source_file, storage_path, file_version, width) in enumerate(rows, 1):
        targets = [(out_dir / derivatives.cache_key(source_file, file_version, versioned=True),
                    max_edge) for out_dir, max_edge in kinds]
        if not force and all(not derivatives.needs_regen(None, c) for c, _ in targets) \
                and width:
            skipped += 1
            continue
        photo = type("P", (), {"storage_backend": "b2", "storage_path": storage_path,
                               "file_version": file_version})()
        try:
            buf = storage.open_master(photo)          # the single B2 read
            im, was_truncated = _decode(buf)
            if was_truncated:
                salvaged += 1
                truncated.append(storage_path)
                print(f"  ~ salvaged truncated master: {storage_path}", file=sys.stderr)
            with im:
                if not width:
                    dims[pid] = im.size
                for cache, max_edge in targets:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    out = im.convert("RGB").copy()
                    out.thumbnail((max_edge, max_edge))
                    out.save(cache, "JPEG",
                             quality=82 if max_edge <= derivatives.THUMB_MAX else 85)
            del buf                                    # discard the master
            fetched += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ! {storage_path}: {type(e).__name__}: {e}", file=sys.stderr)
            errors += 1
        if i % 100 == 0:
            rate = i / max(time.time() - t0, 0.001)
            print(f"  {i}/{len(rows)}  ({rate:.1f}/s, {fetched} fetched, "
                  f"{skipped} cached, {errors} errors)", flush=True)

    if dims:  # one write for all the sizes we learned
        with SessionLocal() as db:
            for pid, (w, h) in dims.items():
                p = db.get(m.Photo, pid)
                if p and not p.width:
                    p.width, p.height = w, h
            db.commit()

    print(f"b2 prewarm: {fetched} fetched+derived, {skipped} already cached, "
          f"{errors} errors in {time.time()-t0:.1f}s  ({len(dims)} dimensions filled)")
    if truncated:
        print(f"  ⚠ {salvaged} master(s) truncated in B2 — derived anyway, but the stored "
              f"object is damaged and worth checking at the source:")
        for k in truncated[:10]:
            print(f"      {k}")
    if errors and not HEIF_OK:
        print("  NOTE: pillow-heif is not installed — .HEIC masters cannot decode.",
              file=sys.stderr)
    return fetched, skipped, errors


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
    import argparse
    ap = argparse.ArgumentParser(description="Pre-generate derivatives (SPEC §12.6/§13.7)")
    ap.add_argument("--force", action="store_true", help="regenerate even if fresh")
    ap.add_argument("--b2-only", action="store_true",
                    help="only fetch B2-backed masters (skip local slides/scans)")
    ap.add_argument("--skip-b2", action="store_true",
                    help="only local slides/scans (no B2 reads)")
    ap.add_argument("--limit", type=int, default=None,
                    help="first N b2 photos (for a trial run before the full fetch)")
    args = ap.parse_args()

    if not args.b2_only:
        prewarm_all(force=args.force)
        stamp_versions()
    if not args.skip_b2:
        prewarm_b2(force=args.force, limit=args.limit)
