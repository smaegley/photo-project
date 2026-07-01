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
from sqlalchemy.orm import Session

from app import derivatives, models as m
from app.auth import current_user
from app.config import settings
from app.database import get_db

router = APIRouter(prefix="/api", tags=["images"])

CARD_RE = re.compile(r"^Mag\d+_card_(?:\d+|extra)\.jpg$")


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


def _resolve(db: Session, source_file: str) -> Path:
    return photo_file(db.query(m.Photo).filter(m.Photo.source_file == source_file).first())


def _safe_key(source_file: str) -> str:
    """Filesystem-safe cache filename (slide names like Mag1_Slide01.JPG pass through)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", source_file)


@router.get("/images/{source_file}")
def full_image(source_file: str, db: Session = Depends(get_db), _user=Depends(current_user)):
    """The original (full-res) file — used for download."""
    return FileResponse(_resolve(db, source_file), media_type="image/jpeg")


@router.get("/display/{source_file}")
def display_image(source_file: str, db: Session = Depends(get_db), _user=Depends(current_user)):
    """Sized derivative for the lightbox (originals can be 24MP)."""
    src = _resolve(db, source_file)
    cache = settings.display_dir / _safe_key(source_file)
    derivatives.ensure(src, cache, derivatives.DISPLAY_MAX)
    return FileResponse(cache, media_type="image/jpeg")


@router.get("/thumbnails/{source_file}")
def thumbnail(source_file: str, db: Session = Depends(get_db), _user=Depends(current_user)):
    cache = settings.thumbnails_dir / _safe_key(source_file)
    try:
        src = _resolve(db, source_file)
    except HTTPException:
        if cache.exists():  # serve a stale thumb rather than 404 if the source is gone
            return FileResponse(cache, media_type="image/jpeg")
        raise
    derivatives.ensure(src, cache, derivatives.THUMB_MAX)
    return FileResponse(cache, media_type="image/jpeg")


@router.get("/faces/{person_id}")
def face(person_id: str, db: Session = Depends(get_db), _user=Depends(current_user)):
    """Cropped face thumbnail for a person's representative photo (SPEC §4.2)."""
    person = db.get(m.Person, person_id)
    if not person or not person.representative_photo_id:
        raise HTTPException(404, "no representative photo")
    rep = db.get(m.Photo, person.representative_photo_id)
    src = photo_file(rep)
    pp = db.get(m.PhotoPerson, (rep.id, person_id))
    region = (pp.region_x, pp.region_y, pp.region_w, pp.region_h) if pp else None
    cache = settings.faces_dir / f"{_safe_key(person_id)}_{rep.id}.jpg"
    if not cache.exists() or cache.stat().st_mtime < src.stat().st_mtime:
        derivatives.face_thumb(src, cache, region)
    return FileResponse(cache, media_type="image/jpeg")


@router.get("/cards/{filename}")
def index_card(filename: str, _user=Depends(current_user)):
    if not CARD_RE.match(filename):
        raise HTTPException(400, "bad card filename")
    path = settings.cards_dir / filename
    if not path.exists():
        raise HTTPException(404, "card not found")
    return FileResponse(path, media_type="image/jpeg")
