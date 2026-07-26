"""Master-file storage backends (SPEC §13.3).

A photo's *master* pixels live either on the local library volume (origin=slide|scan,
and any local digital) or in **Backblaze B2** (origin=digital masters). The row's
`storage_backend` selects; callers (the image server, prewarm) go through here instead
of assuming a local path, so a B2-backed photo serves through the same code once its
derivatives are cached locally.

Three operations, dispatched on `storage_backend`:
- `master_exists(photo)`  — cheap presence check (local: stat; b2: HEAD).
- `open_master(photo)`    — a binary file-like of the master bytes (fetched once by
                            prewarm to build derivatives; never on the browse path).
- `master_version(photo)` — a change-token for the ?v= cache-buster + derivative cache
                            key. Prefer the stored `photo.file_version`; fall back to a
                            live probe only when it's null (self-heals on next prewarm).

**Slice 1: the `local` backend only.** The `b2` backend (boto3, read-only, S3-compatible
— proven 2026-07-26) lands in slice 2; until then a `b2` row raises `StorageError`.
"""
from pathlib import Path

from app.config import settings


class StorageError(Exception):
    """Master file can't be located/read (missing key, path escape, backend down)."""


def backend_of(photo) -> str:
    return getattr(photo, "storage_backend", None) or "local"


def _local_path(photo) -> Path:
    """Resolve a local master under library_root, containment-guarded (matches the
    path-traversal guard in routers/images.py: resolve then require it stays under root)."""
    if not photo.storage_path:
        raise StorageError("photo has no storage_path")
    root = Path(settings.library_root).resolve()
    p = (root / photo.storage_path).resolve()
    if root != p and root not in p.parents:
        raise StorageError(f"storage_path escapes library root: {photo.storage_path!r}")
    return p


def master_exists(photo) -> bool:
    b = backend_of(photo)
    if b == "local":
        try:
            return _local_path(photo).exists()
        except StorageError:
            return False
    if b == "b2":
        raise StorageError("b2 backend not built yet (slice 2)")
    raise StorageError(f"unknown storage_backend {b!r}")


def open_master(photo):
    """Binary file-like of the master. Caller closes it (use `with`)."""
    b = backend_of(photo)
    if b == "local":
        return _local_path(photo).open("rb")
    if b == "b2":
        raise StorageError("b2 backend not built yet (slice 2)")
    raise StorageError(f"unknown storage_backend {b!r}")


def master_version(photo) -> str:
    """Change-token for ?v= + derivative cache keys. Stored value wins; else probe."""
    if getattr(photo, "file_version", None):
        return photo.file_version
    b = backend_of(photo)
    if b == "local":
        try:
            return str(int(_local_path(photo).stat().st_mtime))
        except (StorageError, OSError):
            return ""
    if b == "b2":
        return ""  # no stored version + no cheap probe -> unversioned until prewarm
    raise StorageError(f"unknown storage_backend {b!r}")
