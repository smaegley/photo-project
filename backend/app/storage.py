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

The B2 backend is boto3 / S3-compatible, read-only, keyed by the object key in
`storage_path` (e.g. `Photo Album/2015/…jpg`) — the derivation + auth were proven on
live data 2026-07-26 (SPEC §13.14). It is touched only at import (HEAD) and prewarm
(GET); steady-state browsing serves the locally-cached derivatives, never B2.
"""
import io
from functools import lru_cache
from pathlib import Path

from app.config import settings


class StorageError(Exception):
    """Master file can't be located/read (missing key, path escape, backend down)."""


def backend_of(photo) -> str:
    return getattr(photo, "storage_backend", None) or "local"


# ---- local ----------------------------------------------------------------------
def local_path(photo) -> Path:
    """Resolve a local master under library_root, containment-guarded (resolve first,
    then require it stays under the root — catches symlink escapes too). This is the
    single copy of that guard; routers/images.py calls it rather than repeating it."""
    if not photo.storage_path:
        raise StorageError("photo has no storage_path")
    root = Path(settings.library_root).resolve()
    p = (root / photo.storage_path).resolve()
    if root != p and root not in p.parents:
        raise StorageError(f"storage_path escapes library root: {photo.storage_path!r}")
    return p


# ---- b2 (read-only, S3-compatible) ----------------------------------------------
@lru_cache(maxsize=1)
def _b2_client():
    if not settings.b2_enabled:
        raise StorageError("B2 not configured — set B2_* in backend/.env (SPEC §13)")
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.b2_endpoint}",
        aws_access_key_id=settings.b2_key_id,
        aws_secret_access_key=settings.b2_app_key,
        region_name=settings.b2_endpoint.split(".")[1],  # e.g. us-west-001
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def _b2_head(photo):
    """HEAD the object → the response dict, or None if it doesn't exist (yet)."""
    from botocore.exceptions import ClientError
    if not photo.storage_path:
        raise StorageError("photo has no storage_path (B2 object key)")
    try:
        return _b2_client().head_object(Bucket=settings.b2_bucket, Key=photo.storage_path)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return None  # not synced to B2 yet (nightly lag) — caller defers it
        raise StorageError(f"B2 HEAD failed for {photo.storage_path!r}: {code or e}")


def b2_token(head: dict) -> str:
    """Stable change-token from a HEAD/GET response (ETag, quotes stripped)."""
    return (head.get("ETag") or "").strip('"') if head else ""


# ---- dispatch -------------------------------------------------------------------
def master_exists(photo) -> bool:
    b = backend_of(photo)
    if b == "local":
        try:
            return local_path(photo).exists()
        except StorageError:
            return False
    if b == "b2":
        return _b2_head(photo) is not None
    raise StorageError(f"unknown storage_backend {b!r}")


def open_master(photo):
    """Binary file-like of the master. Local: the file (caller closes). B2: an
    in-memory buffer of the fetched bytes."""
    b = backend_of(photo)
    if b == "local":
        return local_path(photo).open("rb")
    if b == "b2":
        obj = _b2_client().get_object(Bucket=settings.b2_bucket, Key=photo.storage_path)
        return io.BytesIO(obj["Body"].read())
    raise StorageError(f"unknown storage_backend {b!r}")


def probe_version(photo) -> str:
    """Derive the change-token from the master *now*, ignoring any stored value.

    For callers that just changed the master (admin rotate) or are stamping the
    column (prewarm, import). Costs a stat (local) or a HEAD (b2), so it must not
    be called on the browse path — use `master_version` there.
    """
    b = backend_of(photo)
    if b == "local":
        try:
            return str(int(local_path(photo).stat().st_mtime))
        except (StorageError, OSError):
            return ""
    if b == "b2":
        return b2_token(_b2_head(photo))
    raise StorageError(f"unknown storage_backend {b!r}")


def master_version(photo) -> str:
    """Change-token for ?v= + derivative cache keys — the serving path's entry point.

    The stored `file_version` wins and costs nothing. Falling through to a live probe
    happens only for rows stamped before this column existed; the next prewarm fills
    them in and the probe stops (SPEC §13.4)."""
    if getattr(photo, "file_version", None):
        return photo.file_version
    return probe_version(photo)
