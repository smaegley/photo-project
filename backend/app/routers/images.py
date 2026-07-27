"""Image serving — originals, sized display derivatives, thumbnails, and index
cards. Photo files are located by **DB storage_path** (SPEC §11.5) and guarded to
stay under the library root (path-traversal safe), so any origin — slide, scan, or
digital — serves through the same path, not a filename regex. Derivatives are
generated on demand and cached; they self-heal when the source is newer (e.g. a
re-exported slide)."""
import re
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app import derivatives, models as m, storage
from app.auth import image_user
from app.config import settings
from app.database import SessionLocal

router = APIRouter(prefix="/api", tags=["images"])

CARD_RE = re.compile(r"^Mag\d+_card_(?:\d+|extra)\.jpg$")

# Image URLs are mtime-versioned (?v=), so a given URL's bytes never change.
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
DAY = {"Cache-Control": "public, max-age=86400"}


def photo_file(photo: "m.Photo | None") -> Path:
    """Locate a photo's original file **on the local library volume** (path-guarded).
    Works from the Photo row's storage_path — any origin, no filename regex.

    Raises 404 for a B2-backed master (SPEC §13.3): there is no local file to hand to
    FileResponse. Those rows serve from their locally-cached derivatives instead, and
    the master is fetched only by prewarm.
    """
    if not photo or not photo.storage_path:
        raise HTTPException(404, "image not found")
    if storage.backend_of(photo) != "local":
        raise HTTPException(404, "no local master for this photo")
    try:
        path = storage.local_path(photo)
    except storage.StorageError:
        raise HTTPException(400, "bad storage path")
    if not path.exists():
        raise HTTPException(404, "image not found")
    return path


def _lookup(source_file: str) -> SimpleNamespace:
    """The Photo fields the image routes need, holding a pooled connection only for
    the lookup.

    Image routes scope their own sessions rather than taking `Depends(get_db)`:
    a yield-dependency's session is only released after the response body has
    streamed, which would keep a connection checked out for the whole transfer.
    See `auth.image_user` for the full explanation. Returning a detached snapshot
    (not the ORM row) keeps that guarantee — nothing can lazy-load post-response.
    """
    with SessionLocal() as db:
        p = db.query(m.Photo).filter(m.Photo.source_file == source_file).first()
        if not p:
            raise HTTPException(404, "image not found")
        return SimpleNamespace(storage_path=p.storage_path,
                               storage_backend=storage.backend_of(p),
                               file_version=p.file_version)


def _derivative(source_file: str, out_dir: Path, max_edge: int, *, stale_ok: bool = False):
    """Serve a sized derivative, generating it on demand.

    **Local masters: unchanged** — resolve the master, regenerate if the cache is
    older, serve. **Remote (B2) masters:** the local cache is authoritative and its
    key carries `file_version` (§13.4), so a changed master lands on a new key. A miss
    means the row hasn't been prewarmed; that 404s rather than pulling from B2 inline,
    because a multi-MB fetch inside a request is the §10.16 slow-client shape (SPEC
    §13.7 — prewarm is the fetch point).
    """
    p = _lookup(source_file)
    remote = p.storage_backend != "local"
    cache = out_dir / derivatives.cache_key(source_file, p.file_version, versioned=remote)
    if remote:
        if not derivatives.needs_regen(None, cache):
            return FileResponse(cache, media_type="image/jpeg", headers=IMMUTABLE)
        raise HTTPException(404, "derivative not generated yet — run prewarm")
    try:
        src = photo_file(p)
    except HTTPException:
        if stale_ok and cache.exists():  # serve a stale copy rather than 404
            return FileResponse(cache, media_type="image/jpeg", headers=IMMUTABLE)
        raise
    derivatives.ensure(src, cache, max_edge)
    return FileResponse(cache, media_type="image/jpeg", headers=IMMUTABLE)


_safe_key = derivatives.safe_key  # shared with prewarm (SPEC §12.6)


# source_file may be a library-relative path with slashes (scan photos,
# photos/<batch>/<file>) — the :path converter captures those; slide names
# (Mag1_Slide01.JPG) have no slash and match too (SPEC §12.6).
@router.get("/images/{source_file:path}")
def full_image(source_file: str, _user=Depends(image_user)):
    """The original (full-res) file — used for download.

    Local masters only. `origin=digital` masters live in B2 and are deliberately not
    downloadable at full res (SPEC §13.2 #6): the lightbox's digital download points
    at `/api/display/` instead, so this 404s for them rather than proxying B2.
    """
    return FileResponse(photo_file(_lookup(source_file)),
                        media_type="image/jpeg", headers=IMMUTABLE)


@router.get("/display/{source_file:path}")
def display_image(source_file: str, _user=Depends(image_user)):
    """Sized derivative for the lightbox (originals can be 24MP)."""
    return _derivative(source_file, settings.display_dir, derivatives.DISPLAY_MAX)


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
    return _derivative(source_file, settings.thumbnails_dir, derivatives.THUMB_MAX,
                       stale_ok=True)


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


@router.get("/face-crop/{face_id}")
def face_crop(face_id: int, _user=Depends(image_user)):
    """Cropped face for the §14 confirm queue.

    Crops the **display derivative**, which is already local and already sized — the same
    file detection ran on, so the box lines up exactly. Cached per face id; face boxes are
    immutable once detected (a re-detect writes new rows with a new detector_version), so
    the cache never goes stale."""
    with SessionLocal() as db:
        f = db.get(m.Face, face_id)
        if not f:
            raise HTTPException(404, "face not found")
        p = db.get(m.Photo, f.photo_id)
        region = (f.x, f.y, f.w, f.h)
        src_file, fv, backend = p.source_file, p.file_version, storage.backend_of(p)
    src = settings.display_dir / derivatives.cache_key(
        src_file, fv, versioned=(backend == "b2"))
    if not src.exists():
        raise HTTPException(404, "no display derivative — run prewarm")
    cache = settings.faces_dir / f"face{face_id}.jpg"
    if not cache.exists():
        derivatives.face_thumb(src, cache, region)
    return FileResponse(cache, media_type="image/jpeg", headers=IMMUTABLE)


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
