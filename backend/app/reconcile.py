"""Keep B2-backed photo rows in step with the bucket (SPEC §15.4 step 8).

Lightroom is where Steve deletes, moves, renames and re-exports; the sync mirrors that
into B2, and each night the listing tells us what changed:

  moved / renamed  — the row's key is gone and exactly one *new* key has the same
                     content. The row is re-pointed and keeps its tags, faces, event
                     and visibility (decision 20). "Same content" = same ETag (an MD5
                     for all but ~100 multipart uploads in this bucket; two identical
                     copies were verified to share one), else same byte size and the
                     same file name or the same folder.
  deleted          — the key is gone and nothing matches: the photo is culled, once
                     (decision 16). It stays restorable from the Culled view; a
                     restored photo is not culled again while its master stays gone.
                     If the master comes back (a mistaken delete, fixed in Lightroom)
                     the photo returns to where it was by itself.
  replaced         — the key is there with a different ETag: derivatives are rebuilt
                     and faces re-detected by the caller (decision 17).

Guards: if more than `ingest_max_auto_cull` masters vanish at once, none are culled and
the run is flagged — a partial listing must never empty the gallery. `ingest_reconcile`
is "report" until someone has read a few nights' reports; then "apply".

`plan()` only reads. The apply functions write in small chunks with a pause between
them (tests/check_write_contention.py).
"""
import time
from collections import defaultdict
from datetime import datetime, timezone

from app import derivatives, models as m
from app.config import settings

CHUNK = 200
YIELD_S = 0.05
AUTO_CULL_BY = "ingest:deleted-from-b2"


def list_bucket(client) -> dict:
    """key -> (etag, size, last_modified) for everything under the ingest prefix."""
    out = {}
    for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=settings.b2_bucket, Prefix=settings.ingest_prefix):
        for o in page.get("Contents", []):
            out[o["Key"]] = (o["ETag"].strip('"'), o["Size"], o["LastModified"])
    return out


def _folder(key: str) -> str:
    return key.rsplit("/", 1)[0] if "/" in key else ""


def _name(key: str) -> str:
    return key.rsplit("/", 1)[-1].lower()


def plan(db, listing: dict, photo_keys: set[str]) -> dict:
    """What the listing implies for existing rows. `photo_keys` = the keys in the
    listing that could be photos (right type, outside the scan/slide trees)."""
    rows = (db.query(m.Photo.id, m.Photo.storage_path, m.Photo.file_version,
                     m.Photo.file_size, m.Photo.visibility, m.Photo.master_missing)
            .filter(m.Photo.storage_backend == "b2",
                    m.Photo.storage_path.like(settings.ingest_prefix.replace("%", r"\%") + "%"))
            .all())
    present_ci = {k.lower() for k in listing}
    known_ci = {r.storage_path.lower() for r in rows}
    known_ci |= {sf.lower() for (sf,) in db.query(m.Photo.source_file).all()}
    unknown = [k for k in photo_keys if k.lower() not in known_ci]
    by_etag, by_size = defaultdict(list), defaultdict(list)
    for k in unknown:
        by_etag[listing[k][0]].append(k)
        by_size[listing[k][1]].append(k)

    gone = [r for r in rows if r.storage_path.lower() not in present_ci]
    moved, claimed = [], set()
    # A match must be unique in BOTH directions, or it is not a match.
    want = defaultdict(list)
    for r in gone:
        c = by_etag.get(r.file_version) or []
        if not c and r.file_size:
            c = [k for k in by_size.get(r.file_size, [])
                 if _name(k) == _name(r.storage_path) or _folder(k) == _folder(r.storage_path)]
        if len(c) == 1:
            want[c[0]].append(r)
    for new_key, rs in want.items():
        if len(rs) == 1:
            moved.append({"id": rs[0].id, "old": rs[0].storage_path, "new": new_key,
                          "etag": listing[new_key][0], "size": listing[new_key][1]})
            claimed.add(rs[0].id)

    vanished = [r for r in gone if r.id not in claimed]
    return {
        "rows": len(rows),
        "moved": moved,
        # newly missing -> cull; already flagged -> leave alone (restored by an admin)
        "deleted": [{"id": r.id, "key": r.storage_path} for r in vanished
                    if not r.master_missing and r.visibility != m.VIS_CULLED],
        "flag_missing": [r.id for r in vanished if not r.master_missing],
        "missing": [r.id for r in vanished],          # every row whose master is absent
        "returned": [r.id for r in rows if r.master_missing
                     and r.storage_path.lower() in present_ci],
        "replaced": [{"id": r.id, "key": r.storage_path, "etag": listing[r.storage_path][0]}
                     for r in rows if r.storage_path in listing and r.file_version
                     and listing[r.storage_path][0] != r.file_version],
        "sizes": {r.id: listing[r.storage_path][1] for r in rows
                  if r.storage_path in listing and r.file_size is None
                  and listing[r.storage_path][0] == r.file_version},
    }


def _chunked(db, items, fn):
    for k in range(0, len(items), CHUNK):
        for it in items[k:k + CHUNK]:
            fn(it)
        db.commit()
        time.sleep(YIELD_S)


def backfill_sizes(db, pl: dict) -> int:
    """Harmless bookkeeping, done in report mode too: sizes are what let a later move
    of a multipart-uploaded master be recognised."""
    _chunked(db, list(pl["sizes"].items()), lambda kv: db.query(m.Photo)
             .filter(m.Photo.id == kv[0]).update({"file_size": kv[1]}, synchronize_session=False))
    return len(pl["sizes"])


def apply_moves(db, pl: dict) -> int:
    """Re-point moved rows. Must run BEFORE new keys are staged, or the new key would
    come in as a second, untagged copy of the same photo."""
    def one(mv):
        p = db.get(m.Photo, mv["id"])
        if p is None:
            return
        old_token = derivatives.version_token(p)
        old_key = p.storage_path
        if p.source_file == old_key:
            p.source_file = mv["new"]
        p.storage_path = mv["new"]
        p.original_filename = mv["new"].rsplit("/", 1)[-1]
        p.file_version, p.file_size, p.master_missing = mv["etag"], mv["size"], False
        new_token = derivatives.version_token(p)
        # Same pixels: carry the cached derivatives across instead of re-fetching.
        for d in (settings.thumbnails_dir, settings.display_dir):
            src = d / derivatives.cache_key(old_key, old_token, versioned=True)
            dst = d / derivatives.cache_key(p.source_file, new_token, versioned=True)
            try:
                if src.exists() and not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    src.rename(dst)
            except OSError:
                pass                         # serving regenerates on demand
    _chunked(db, pl["moved"], one)
    return len(pl["moved"])


def apply_missing(db, pl: dict) -> dict:
    """Cull newly vanished masters (guarded) and maintain the master_missing flag."""
    over = len(pl["deleted"]) > settings.ingest_max_auto_cull
    if over:
        return {"culled": 0, "restored": 0, "guard": f"{len(pl['deleted'])} masters missing from the listing "
                f"(limit {settings.ingest_max_auto_cull}) — none culled"}
    now = datetime.now(timezone.utc)
    cull = {d["id"] for d in pl["deleted"]}

    def one(pid):
        p = db.get(m.Photo, pid)
        if p is None:
            return
        p.master_missing = True
        if pid in cull and p.visibility != m.VIS_CULLED:
            p.culled_from, p.culled_at, p.culled_by = p.visibility, now, AUTO_CULL_BY
            p.visibility = m.VIS_CULLED
    _chunked(db, pl["flag_missing"], one)
    restored = []

    def back(pid):
        p = db.get(m.Photo, pid)
        if p is None:
            return
        p.master_missing = False
        # Only undo a cull this module made. A photo an admin culled stays culled.
        if p.visibility == m.VIS_CULLED and p.culled_by == AUTO_CULL_BY:
            p.visibility = p.culled_from or m.VIS_PUBLISHED
            p.culled_from = p.culled_at = p.culled_by = None
            restored.append(pid)
    _chunked(db, pl["returned"], back)
    return {"culled": len(cull), "restored": len(restored), "guard": None}


def summary(pl: dict, limit: int = 20) -> dict:
    """The part of a plan worth keeping in the run record."""
    return {"rows": pl["rows"],
            "moved": [[x["old"], x["new"]] for x in pl["moved"][:limit]], "n_moved": len(pl["moved"]),
            "deleted": [x["key"] for x in pl["deleted"][:limit]], "n_deleted": len(pl["deleted"]),
            "replaced": [x["key"] for x in pl["replaced"][:limit]], "n_replaced": len(pl["replaced"]),
            "returned": len(pl["returned"]),
            # ids for the Inbox screen's "Missing from B2" tab (both modes)
            "missing_ids": pl["missing"][:1000], "n_missing": len(pl["missing"])}
