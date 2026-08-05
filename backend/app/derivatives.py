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


def cache_key(source_file: str, file_version: str | None = None,
              *, versioned: bool = False) -> str:
    """Cache filename for a derivative (SPEC §13.4).

    Local masters keep the bare `safe_key` and rely on the mtime predicate below, so
    every derivative already on disk stays valid. A **remote** master (B2) can't be
    stat'd cheaply, so its change-token goes *into* the key instead: when the master
    changes the key changes, the request lands on a fresh file, and the old one simply
    orphans (GC'd later). `versioned=True` with no token falls back to the bare key —
    a not-yet-stamped row still resolves rather than 404ing on a malformed name.
    """
    key = safe_key(source_file)
    return f"{key}.{safe_key(file_version)}" if versioned and file_version else key


def version_token(photo) -> str | None:
    """The change-token to key remote derivatives (and ?v= URLs) on.

    Folds the display-time `rotation` override into the stored `file_version`, so
    setting/clearing a rotation lands on a fresh cache key and a fresh URL without
    touching `file_version` itself — that column keeps meaning "the MASTER's content
    token" (import diffing depends on it). Accepts any object with `file_version`
    and `rotation` attributes.
    """
    fv = getattr(photo, "file_version", None)
    rot = getattr(photo, "rotation", 0) or 0
    if not rot:
        return fv
    return f"{fv or 'unstamped'}-r{rot}"


def needs_regen(src: Path | None, cache: Path) -> bool:
    """True if the cached derivative is missing, empty, or stale vs. the source.

    `src=None` means the master is remote (SPEC §13.3): there's no cheap source mtime
    to compare against, so freshness is existence + non-empty. Invalidation for those
    comes from the version token in `cache_key`, not from this predicate.
    """
    if not cache.exists():
        return True
    st = cache.stat()
    if st.st_size == 0:
        return True
    return src is not None and st.st_mtime < src.stat().st_mtime


def generate(src: Path, cache: Path, max_edge: int, transpose: bool = False,
             rotate: int = 0) -> None:
    """Write a downscaled JPEG (longest edge <= max_edge).

    `transpose` applies the EXIF orientation to the pixels — needed for raw scans
    that skip Lightroom (back-of-photo _b images), which carry a live orientation
    flag. It is a no-op when there's no flag, so it's safe but off by default to
    keep slide/LR derivatives (rotation already baked, orientation=1) untouched.

    `rotate` applies a clockwise display-time override (photo.rotation) — for
    B2-backed masters that are sideways but can't be rewritten."""
    cache.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        if not (transpose or rotate):
            im.draft("RGB", (max_edge, max_edge))  # fast downscale on decode
        if transpose:
            im = ImageOps.exif_transpose(im)
        if rotate:
            im = im.rotate(-rotate, expand=True)   # PIL is CCW-positive
        im.thumbnail((max_edge, max_edge))
        im.convert("RGB").save(cache, "JPEG", quality=82 if max_edge <= THUMB_MAX else 85)


def ensure(src: Path, cache: Path, max_edge: int, transpose: bool = False) -> bool:
    """(Re)generate only if stale. Returns True if it regenerated."""
    if not needs_regen(src, cache):
        return False
    generate(src, cache, max_edge, transpose=transpose)
    return True


# Bump when face_thumb's geometry changes, so already-cached crops are abandoned rather
# than served forever. v2: square crops near frame edges (were clipped to rectangles).
FACE_CROP_V = "v2"


def face_version(rep_id, region) -> str:
    """Cache/URL key for a person's face crop — changes when the rep photo OR the
    crop box changes, so both the disk cache and the browser refetch."""
    if region and region[2] is not None:  # region = (x, y, w, h)
        return f"{FACE_CROP_V}-{rep_id}-" + "-".join(str(int((v or 0) * 1000)) for v in region)
    return f"{FACE_CROP_V}-{rep_id}"


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
        else:
            side, x, y = min(W, H), W / 2, H / 2
        # SLIDE the square inside the frame instead of clipping it. Clamping each edge
        # independently silently returned a rectangle whenever a face sat near a border —
        # 37% of crops, ratios 0.51 to 1.92 — and a grid of ragged thumbnails is unusable
        # for the one job it has: comparing faces side by side.
        side = int(round(min(side, W, H)))
        left = max(0, min(int(round(x - side / 2)), W - side))
        top = max(0, min(int(round(y - side / 2)), H - side))
        crop = im.crop((left, top, left + side, top + side))
        crop.thumbnail((max_edge, max_edge))
        crop.save(cache, "JPEG", quality=85)
