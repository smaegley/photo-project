"""Image serving — originals, sized display derivatives, thumbnails, and index
cards. Photo files are located by **DB storage_path** (SPEC §11.5) and guarded to
stay under the library root (path-traversal safe), so any origin — slide, scan, or
digital — serves through the same path, not a filename regex. Derivatives are
generated on demand and cached; they self-heal when the source is newer (e.g. a
re-exported slide)."""
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app import derivatives, models as m
from app.auth import image_user
from app.config import settings
from app.database import SessionLocal

router = APIRouter(prefix="/api", tags=["images"])

CARD_RE = re.compile(r"^Mag\d+_card_(?:\d+|extra)\.jpg$")

# Image URLs are mtime-versioned (?v=), so a given URL's bytes never change.
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
DAY = {"Cache-Control": "public, max-age=86400"}


def photo_file(photo: "m.Photo | None") -> Path:
    """Locate a photo's original file under the library root (path-guarded).
    Works from the Photo row's storage_path — any origin, no filename regex."""
    if not photo or not photo.storage_path:
        raise HTTPException(404, "image not found")
    root = Path(settings.library_root).resolve()
    path = (root / photo.storage_path).resolve()
    if root != path and root not in path.parents:
        raise HTTPException(400, "bad storage path")
    if not path.exists():
        raise HTTPException(404, "image not found")
    return path


def _resolve(source_file: str) -> Path:
    """Locate a photo's file, holding a pooled connection only for the lookup.

    Image routes scope their own sessions rather than taking `Depends(get_db)`:
    a yield-dependency's session is only released after the response body has
    streamed, which would keep a connection checked out for the whole transfer.
    See `auth.image_user` for the full explanation.
    """
    with SessionLocal() as db:
        return photo_file(db.query(m.Photo).filter(m.Photo.source_file == source_file).first())


_safe_key = derivatives.safe_key  # shared with prewarm (SPEC §12.6)


# source_file may be a library-relative path with slashes (scan photos,
# photos/<batch>/<file>) — the :path converter captures those; slide names
# (Mag1_Slide01.JPG) have no slash and match too (SPEC §12.6).
@router.get("/images/{source_file:path}")
def full_image(source_file: str, _user=Depends(image_user)):
    """The original (full-res) file — used for download."""
    return FileResponse(_resolve(source_file), media_type="image/jpeg", headers=IMMUTABLE)


@router.get("/display/{source_file:path}")
def display_image(source_file: str, _user=Depends(image_user)):
    """Sized derivative for the lightbox (originals can be 24MP)."""
    src = _resolve(source_file)
    cache = settings.display_dir / _safe_key(source_file)
    derivatives.ensure(src, cache, derivatives.DISPLAY_MAX)
    return FileResponse(cache, media_type="image/jpeg", headers=IMMUTABLE)


@router.get("/photo-back/{photo_id}")
def photo_back(photo_id: int, _user=Depends(image_user)):
    """Display derivative of a scan's back-of-photo (_b) image (SPEC §12.8).

    Keyed by photo id (the back has no Photo row of its own). Back scans skip
    Lightroom, so they carry a live EXIF orientation flag — transpose it (§12.6)."""
    with SessionLocal() as db:
        photo = db.query(m.Photo).filter(m.Photo.id == photo_id).first()
        back_path = photo.back_path if photo else None
    if not back_path:
        raise HTTPException(404, "no back image")
    root = Path(settings.library_root).resolve()
    src = (root / back_path).resolve()
    if root not in src.parents or not src.exists():
        raise HTTPException(404, "back image not found")
    cache = settings.display_dir / _safe_key(back_path)
    derivatives.ensure(src, cache, derivatives.DISPLAY_MAX, transpose=True)
    return FileResponse(cache, media_type="image/jpeg", headers=IMMUTABLE)


@router.get("/thumbnails/{source_file:path}")
def thumbnail(source_file: str, _user=Depends(image_user)):
    cache = settings.thumbnails_dir / _safe_key(source_file)
    try:
        src = _resolve(source_file)
    except HTTPException:
        if cache.exists():  # serve a stale thumb rather than 404 if the source is gone
            return FileResponse(cache, media_type="image/jpeg", headers=IMMUTABLE)
        raise
    derivatives.ensure(src, cache, derivatives.THUMB_MAX)
    return FileResponse(cache, media_type="image/jpeg", headers=IMMUTABLE)


@router.get("/faces/{person_id}")
def face(person_id: str, _user=Depends(image_user)):
    """Cropped face thumbnail for a person's representative photo (SPEC §4.2)."""
    with SessionLocal() as db:
        person = db.get(m.Person, person_id)
        if not person or not person.representative_photo_id:
            raise HTTPException(404, "no representative photo")
        rep = db.get(m.Photo, person.representative_photo_id)
        src = photo_file(rep)
        pp = db.get(m.PhotoPerson, (rep.id, person_id))
        region = (pp.region_x, pp.region_y, pp.region_w, pp.region_h) if pp else None
        cache = settings.faces_dir / f"{_safe_key(person_id)}_{derivatives.face_version(rep.id, region)}.jpg"
    if not cache.exists() or cache.stat().st_mtime < src.stat().st_mtime:
        derivatives.face_thumb(src, cache, region)
    return FileResponse(cache, media_type="image/jpeg", headers=DAY)


@router.get("/cards/{filename}")
def index_card(filename: str, _user=Depends(image_user)):
    if not CARD_RE.match(filename):
        raise HTTPException(400, "bad card filename")
    path = settings.cards_dir / filename
    if not path.exists():
        raise HTTPException(404, "card not found")
    return FileResponse(path, media_type="image/jpeg", headers=DAY)


@router.get("/card-thumbs/{filename}")
def index_card_thumb(filename: str, _user=Depends(image_user)):
    """Sized card derivative for grid/panel views; /api/cards stays full-res."""
    if not CARD_RE.match(filename):
        raise HTTPException(400, "bad card filename")
    src = settings.cards_dir / filename
    if not src.exists():
        raise HTTPException(404, "card not found")
    cache = settings.card_thumbs_dir / filename
    derivatives.ensure(src, cache, 640)
    return FileResponse(cache, media_type="image/jpeg", headers=DAY)
