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
from pathlib import Path

from PIL import Image

THUMB_MAX = 400      # px, longest edge — gallery grid
DISPLAY_MAX = 2560   # px, longest edge — lightbox (originals can be 24MP)


def needs_regen(src: Path, cache: Path) -> bool:
    """True if the cached derivative is missing, empty, or stale vs. the source."""
    if not cache.exists():
        return True
    st = cache.stat()
    return st.st_size == 0 or st.st_mtime < src.stat().st_mtime


def generate(src: Path, cache: Path, max_edge: int) -> None:
    """Write a downscaled JPEG (longest edge <= max_edge)."""
    cache.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im.draft("RGB", (max_edge, max_edge))  # fast downscale on decode
        im.thumbnail((max_edge, max_edge))
        im.convert("RGB").save(cache, "JPEG", quality=82 if max_edge <= THUMB_MAX else 85)


def ensure(src: Path, cache: Path, max_edge: int) -> bool:
    """(Re)generate only if stale. Returns True if it regenerated."""
    if not needs_regen(src, cache):
        return False
    generate(src, cache, max_edge)
    return True
