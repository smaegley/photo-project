"""Image serving — full slides, thumbnails (generated on demand + cached), and
index-card scans. All filenames are pattern-validated to prevent path traversal.
Images are mounted read-only from /mnt/photos/library (SPEC §3.1/§8)."""
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from PIL import Image

from app.auth import current_user
from app.config import settings

router = APIRouter(prefix="/api", tags=["images"])

SLIDE_RE = re.compile(r"^Mag(\d+)_Slide(\d+)\.JPG$")
CARD_RE = re.compile(r"^Mag\d+_card_(?:\d+|extra)\.jpg$")
THUMB_MAX = 400  # px, longest edge


def _slide_path(source_file: str):
    mm = SLIDE_RE.match(source_file)
    if not mm:
        raise HTTPException(400, "bad slide filename")
    path = settings.slides_dir / f"Mag{int(mm.group(1))}" / source_file
    if not path.exists():
        raise HTTPException(404, "image not found")
    return path


@router.get("/images/{source_file}")
def full_image(source_file: str, _user=Depends(current_user)):
    return FileResponse(_slide_path(source_file), media_type="image/jpeg")


@router.get("/thumbnails/{source_file}")
def thumbnail(source_file: str, _user=Depends(current_user)):
    if not SLIDE_RE.match(source_file):
        raise HTTPException(400, "bad slide filename")
    thumb = settings.thumbnails_dir / source_file
    if not thumb.exists():
        # generate on demand (the batch pre-generates these; this is the fallback)
        src = _slide_path(source_file)
        thumb.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im.draft("RGB", (THUMB_MAX, THUMB_MAX))
            im.thumbnail((THUMB_MAX, THUMB_MAX))
            im.convert("RGB").save(thumb, "JPEG", quality=82)
    return FileResponse(thumb, media_type="image/jpeg")


@router.get("/cards/{filename}")
def index_card(filename: str, _user=Depends(current_user)):
    if not CARD_RE.match(filename):
        raise HTTPException(400, "bad card filename")
    path = settings.cards_dir / filename
    if not path.exists():
        raise HTTPException(404, "card not found")
    return FileResponse(path, media_type="image/jpeg")
