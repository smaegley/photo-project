"""Batch pre-generate slide derivatives (thumbnails + display images).

Run as a **deploy step** after a slide rsync to avoid the lazy first-view
regeneration burst during browsing. Lives in the backend package so it runs inside
the prod container:

    docker compose exec api python -m app.prewarm            # regenerate stale only
    docker compose exec api python -m app.prewarm --force    # regenerate everything

`importer/make_thumbnails.py` is the dev-side wrapper over this. Staleness-aware
(same predicate as the server) — a plain run only touches missing/zero-byte/stale
derivatives, so it's cheap to run on every deploy.
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
    print(f"derivatives: {made} made, {skipped} fresh/skipped, {errors} errors "
          f"in {time.time()-t0:.1f}s  (thumbnails/ + display/)")
    return made, skipped, errors


if __name__ == "__main__":
    prewarm_all(force="--force" in sys.argv)
