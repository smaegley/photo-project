"""Pre-generate gallery thumbnails for all slides (SPEC §8).

Reads library/slides/Mag<N>/*.JPG, writes <=400px JPEGs to library/thumbnails/.
Idempotent: skips files already thumbnailed unless --force. Run:
    python -m importer.make_thumbnails [--force]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from PIL import Image  # noqa: E402

from app.config import settings  # noqa: E402

THUMB_MAX = 400


def run(force: bool = False):
    src_dir = settings.slides_dir
    out_dir = settings.thumbnails_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    slides = sorted(src_dir.glob("Mag*/Mag*_Slide*.JPG"))
    made = skipped = errors = 0
    t0 = time.time()
    for i, src in enumerate(slides, 1):
        dst = out_dir / src.name
        if dst.exists() and not force:
            skipped += 1
            continue
        try:
            with Image.open(src) as im:
                im.draft("RGB", (THUMB_MAX, THUMB_MAX))  # fast downscale on decode
                im.thumbnail((THUMB_MAX, THUMB_MAX))
                im.convert("RGB").save(dst, "JPEG", quality=82)
            made += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ! {src.name}: {type(e).__name__}: {e}", file=sys.stderr)
            errors += 1
        if i % 200 == 0:
            print(f"  {i}/{len(slides)} ...")
    print(f"thumbnails: {made} made, {skipped} skipped, {errors} errors "
          f"in {time.time()-t0:.1f}s -> {out_dir}")


if __name__ == "__main__":
    run(force="--force" in sys.argv)
