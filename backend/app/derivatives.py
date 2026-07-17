"""Sized-JPEG derivative generation — gallery thumbnails + lightbox display images.

Shared by the image server (`routers/images.py`, lazy + self-healing) and the batch
pre-warm tool (`importer/make_thumbnails.py`, run as a deploy step). One predicate
in both places keeps them consistent.

A derivative is (re)generated when it is **missing, zero-byte, or older than its
source**. That mtime rule is deliberate: a re-exported/rotated slide dropped in
under the same name refreshes its thumbnail automatically (SPEC §10.6). To avoid the
lazy first-view regeneration *burst* after a bulk slide update, run the batch
pre-warm at deploy time — it uses the same predicate, so it only touches what
actually changed and leaves browsing with nothing to regenerate.
"""
import re
from pathlib import Path

from PIL import Image, ImageOps

THUMB_MAX = 400      # px, longest edge — gallery grid
DISPLAY_MAX = 2560   # px, longest edge — lightbox (originals can be 24MP)
FACE_MAX = 240       # px, square — People-filter face thumbnail


def safe_key(source_file: str) -> str:
    """Filesystem-safe cache filename for a photo's derivative. Slide names
    (Mag1_Slide01.JPG) pass through unchanged; scan paths (photos/<batch>/<file>)
    have their slashes/spaces flattened, so the key is unique and path-free. Shared
    by the image server and the prewarm tool so both address the same cache file."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", source_file)


def needs_regen(src: Path, cache: Path) -> bool:
    """True if the cached derivative is missing, empty, or stale vs. the source."""
    if not cache.exists():
        return True
    st = cache.stat()
    return st.st_size == 0 or st.st_mtime < src.stat().st_mtime


def generate(src: Path, cache: Path, max_edge: int, transpose: bool = False) -> None:
    """Write a downscaled JPEG (longest edge <= max_edge).

    `transpose` applies the EXIF orientation to the pixels — needed for raw scans
    that skip Lightroom (back-of-photo _b images), which carry a live orientation
    flag. It is a no-op when there's no flag, so it's safe but off by default to
    keep slide/LR derivatives (rotation already baked, orientation=1) untouched."""
    cache.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        if not transpose:
            im.draft("RGB", (max_edge, max_edge))  # fast downscale on decode
        if transpose:
            im = ImageOps.exif_transpose(im)
        im.thumbnail((max_edge, max_edge))
        im.convert("RGB").save(cache, "JPEG", quality=82 if max_edge <= THUMB_MAX else 85)


def ensure(src: Path, cache: Path, max_edge: int, transpose: bool = False) -> bool:
    """(Re)generate only if stale. Returns True if it regenerated."""
    if not needs_regen(src, cache):
        return False
    generate(src, cache, max_edge, transpose=transpose)
    return True


def face_version(rep_id, region) -> str:
    """Cache/URL key for a person's face crop — changes when the rep photo OR the
    crop box changes, so both the disk cache and the browser refetch."""
    if region and region[2] is not None:  # region = (x, y, w, h)
        return f"{rep_id}-" + "-".join(str(int((v or 0) * 1000)) for v in region)
    return str(rep_id)


def face_thumb(src: Path, cache: Path, region, max_edge: int = FACE_MAX) -> None:
    """Square face crop from a normalized region (cx,cy,w,h in 0..1), padded for
    headroom. Falls back to a centre-square crop when there's no region."""
    cache.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGB")
        W, H = im.size
        if region and all(v is not None for v in region):
            cx, cy, w, h = region
            side = max(min(w * 1.6, 1.0) * W, min(h * 1.9, 1.0) * H)  # pad + squarify
            x, y = cx * W, cy * H
            box = (x - side / 2, y - side / 2, x + side / 2, y + side / 2)
        else:
            s = min(W, H)
            box = ((W - s) / 2, (H - s) / 2, (W + s) / 2, (H + s) / 2)
        crop = im.crop((max(0, int(box[0])), max(0, int(box[1])),
                        min(W, int(box[2])), min(H, int(box[3]))))
        crop.thumbnail((max_edge, max_edge))
        crop.save(cache, "JPEG", quality=85)
