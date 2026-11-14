"""Nightly ingest: new B2 photos -> pending inbox rows (SPEC §15.4, slice 3).

    python -m app.ingest [--dry-run] [--limit N] [--skip-faces]

Runs on **prod** in the `ingest` image. Each run:

1. **Discover** — list the bucket under `ingest_prefix`; a key is new if it is a
   .jpg/.jpeg/.heic outside the scan/slide trees, uploaded on/after `ingest_cutover`,
   and has **no photo row in any state** (so a culled photo never comes back).
2. **Fetch once** — one GET per new photo. From the bytes in hand: both derivatives,
   capture time + GPS + orientation from EXIF, embedded keywords, pixel size, ETag.
   The master is then discarded (§13.7).
3. **Stage** — insert an `origin='digital'`, `visibility='pending'` row. Nothing here is
   visible to the family until an admin publishes it from the inbox.
4. **Faces** — detect + embed the new photos, then refresh match suggestions.

Idempotent and resumable: a photo is committed as soon as it is staged, so an
interrupted run (OOM, the nightly container backup) just picks up the rest next time.
A file that can't be decoded is recorded under `errors` and skipped — it does not fail
the run (there is a known truncated master in the bucket).

5. **Outings + triage** — `app.outings` groups the photos and decides which tab of the
   inbox each lands in (§15.5).

Between 4 and 5, `app.junk` scores each new photo for the Likely junk tab.

6. **Stacks** — `app.stacks` groups near-duplicate bursts for the inbox.

Not yet: auto-tagging (slice 7).
"""
import argparse
import io
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import pillow_heif
from PIL import ExifTags, Image, IptcImagePlugin

pillow_heif.register_heif_opener()     # most phone photos are HEIC; without this none decode

from app import derivatives, models as m, reconcile, storage
from app.config import settings
from app.database import SessionLocal
from app.import_photos import build_place_index, resolve_place

EXTS = {"jpg", "jpeg", "heic"}
# Trees under the prefix that are not born-digital photos: archive copies of the scans
# and slides the app already serves locally, and the sync tool's own bookkeeping.
# A HARD filter — without it the first run would stage ~3.5k scans and slides (verified
# against prod by ops, 2026-11-13).
EXCLUDE_TREES = ("FastFoto/", "PreProcess_Slides/", "_gsdata_/")
ORIENT_TO_CW = {3: 180, 6: 90, 8: 270}     # EXIF Orientation -> clockwise degrees
FOLDER_DATE = re.compile(r"/(\d{4}-\d{2}-\d{2})/")


def photo_keys(listing: dict) -> tuple[set[str], dict]:
    """Keys in the listing that could be photos -> (keys, skipped {reason: n})."""
    prefix = settings.ingest_prefix
    keys, skipped = set(), {}
    for key, (_etag, _size, modified) in listing.items():
        rel = key[len(prefix):]
        name = rel.rsplit("/", 1)[-1]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if rel.startswith(EXCLUDE_TREES):
            continue                          # not counted: tens of thousands, by design
        if name.startswith("._") or ext not in EXTS:
            if modified >= _cutover():        # only report skips among new uploads
                skipped[f"type:{ext or 'none'}"] = skipped.get(f"type:{ext or 'none'}", 0) + 1
            continue
        keys.add(key)
    return keys, skipped


def _cutover() -> datetime:
    return datetime.strptime(settings.ingest_cutover, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def discover(db, listing: dict, keys: set[str], hold: set[str] = frozenset()):
    """-> candidates [(key, etag)]: photo keys uploaded on/after the cutover with no
    photo row in any state. `hold` = keys reconcile wants left alone this run."""
    cutover = _cutover()
    known = {sf.lower() for (sf,) in db.query(m.Photo.source_file).all()}
    known |= {sp.lower() for (sp,) in db.query(m.Photo.storage_path)
              .filter(m.Photo.storage_path.isnot(None)).all()}
    return sorted((k, listing[k][0]) for k in keys
                  if listing[k][2] >= cutover and k.lower() not in known and k not in hold)


def _decode(buf):
    """Strict decode. A truncated file is an error here, not something to salvage:
    an inbox item with a damaged bottom edge is worse than a line in the report."""
    im = Image.open(buf)
    im.load()
    return im


def read_meta(im, raw: bytes, key: str) -> dict:
    """Capture time, GPS, orientation and embedded keywords from the file itself."""
    ex = im.getexif()
    sub = ex.get_ifd(ExifTags.IFD.Exif)
    meta = {"taken": None, "taken_exact": False, "gps": None, "rotation": 0, "keywords": []}

    for v in (sub.get(36867), sub.get(36868), ex.get(306)):   # Original, Digitized, DateTime
        if v:
            try:
                meta["taken"] = datetime.strptime(str(v)[:19], "%Y:%m:%d %H:%M:%S")
                meta["taken_exact"] = True
                break
            except ValueError:
                pass
    if meta["taken"] is None:
        # Saved/received images carry no EXIF time. The dated import folder is the
        # best remaining signal — it is the IMPORT date, hence taken_exact=False.
        mm = FOLDER_DATE.search(key)
        if mm:
            meta["taken"] = datetime.strptime(mm.group(1), "%Y-%m-%d")

    try:
        g = ex.get_ifd(ExifTags.IFD.GPSInfo)
        if g and 2 in g and 4 in g:
            dms = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600   # noqa: E731
            lat = dms(g[2]) * (-1 if g.get(1) == "S" else 1)
            lon = dms(g[4]) * (-1 if g.get(3) == "W" else 1)
            if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0.0, 0.0):
                meta["gps"] = (lat, lon)
    except Exception:  # noqa: BLE001 — malformed GPS is common and never fatal
        pass

    # pillow-heif already applies HEIC orientation; JPEGs keep a live flag. Record it as
    # the display-time rotation override so every derivative path agrees (§13 rotation).
    meta["rotation"] = ORIENT_TO_CW.get(ex.get(274), 0) if (im.format or "").upper() != "HEIF" else 0

    kw: set[str] = set()
    mm = re.search(rb"<dc:subject>(.*?)</dc:subject>", raw, re.S)
    if mm:
        kw |= {x.decode("utf8", "replace").strip()
               for x in re.findall(rb"<rdf:li[^>]*>(.*?)</rdf:li>", mm.group(1), re.S)}
    try:
        v = (IptcImagePlugin.getiptcinfo(im) or {}).get((2, 25))
        v = [v] if isinstance(v, bytes) else (v or [])
        kw |= {x.decode("utf8", "replace").strip() for x in v}
    except Exception:  # noqa: BLE001
        pass
    meta["keywords"] = sorted(k for k in kw if k)
    return meta


def write_derivatives(im, key: str, etag: str, rotation: int) -> None:
    """Thumbnail + display JPEG on the same versioned cache keys prewarm/serving use."""
    token = derivatives.version_token(
        type("P", (), {"file_version": etag, "rotation": rotation})())
    for out_dir, max_edge in ((settings.thumbnails_dir, derivatives.THUMB_MAX),
                              (settings.display_dir, derivatives.DISPLAY_MAX)):
        cache = out_dir / derivatives.cache_key(key, token, versioned=True)
        cache.parent.mkdir(parents=True, exist_ok=True)
        out = im.convert("RGB").copy()
        if rotation:
            out = out.rotate(-rotation, expand=True)          # PIL is CCW-positive
        out.thumbnail((max_edge, max_edge))
        tmp = cache.with_suffix(cache.suffix + ".part")
        out.save(tmp, "JPEG", quality=82 if max_edge <= derivatives.THUMB_MAX else 85)
        tmp.replace(cache)                                    # never leave a half file


def _fetch(client, key: str):
    """One GET -> (decoded image, raw size, meta). The caller closes the image."""
    raw = client.get_object(Bucket=settings.b2_bucket, Key=key)["Body"].read()
    im = _decode(io.BytesIO(raw))
    return im, len(raw), read_meta(im, raw, key)


def refresh(db, client, item: dict) -> None:
    """A master replaced in the bucket (decision 17): rebuild the derivatives, keep
    every tag, and mark the photo for face re-detection."""
    p = db.get(m.Photo, item["id"])
    old_token = derivatives.version_token(p)
    im, size, meta = _fetch(client, p.storage_path)
    with im:
        write_derivatives(im, p.source_file, item["etag"], meta["rotation"])
        w, h = im.size
    for d in (settings.thumbnails_dir, settings.display_dir):
        (d / derivatives.cache_key(p.source_file, old_token, versioned=True)).unlink(missing_ok=True)
    p.file_version, p.file_size, p.width, p.height = item["etag"], size, w, h
    p.rotation = meta["rotation"]            # a re-export usually has the rotation baked in
    p.faces_scanned_version = None
    db.commit()


def run(dry_run: bool = False, limit: int | None = None, skip_faces: bool = False) -> int:
    if not settings.b2_enabled:
        print("B2 is not configured (B2_* in .env) — nothing to ingest", file=sys.stderr)
        return 2
    t0 = time.time()
    db = SessionLocal()
    run_row = None
    new = 0
    try:
        client = storage._b2_client()
        listing = reconcile.list_bucket(client)
        listed = len(listing)
        keys, skipped = photo_keys(listing)
        apply = settings.ingest_reconcile == "apply"
        pl = reconcile.plan(db, listing, keys)
        rec = reconcile.summary(pl)
        rec["mode"] = settings.ingest_reconcile
        # A moved photo's new key is never staged as a new photo: in apply mode the row
        # is re-pointed to it below; in report mode it waits.
        hold = {mv["new"] for mv in pl["moved"]}
        cands = discover(db, listing, keys, hold)
        todo = cands[:limit] if limit else cands
        print(f"listed {listed} objects under {settings.ingest_prefix!r}; "
              f"{len(cands)} new candidate(s) since {settings.ingest_cutover}; "
              f"skipped types: {skipped or 'none'}")
        print(f"reconcile ({rec['mode']}): {rec['rows']} B2 photos checked — "
              f"{rec['n_moved']} moved, {rec['n_deleted']} deleted, "
              f"{rec['n_replaced']} replaced, {rec['returned']} back")
        for old, new_key in rec["moved"]:
            print(f"  moved    {old} -> {new_key}")
        for k in rec["deleted"]:
            print(f"  deleted  {k}")
        for k in rec["replaced"]:
            print(f"  replaced {k}")
        if dry_run:
            for key, _ in todo[:20]:
                print("  would ingest", key)
            if len(todo) > 20:
                print(f"  … and {len(todo) - 20} more")
            return 0

        run_row = m.IngestRun(listed=listed, candidates=len(cands))
        db.add(run_row)
        db.commit()
        reconcile.backfill_sizes(db, pl)
        if apply:
            reconcile.apply_moves(db, pl)

        place_names, place_geo = build_place_index(db)
        err_keys = []
        for i, (key, etag) in enumerate(todo, 1):
            try:
                im, size, meta = _fetch(client, key)
                with im:
                    write_derivatives(im, key, etag, meta["rotation"])
                    w, h = im.size
                taken = meta["taken"]
                p = m.Photo(
                    source_file=key, origin="digital", storage_backend="b2",
                    storage_path=key, file_version=etag, file_size=size,
                    original_filename=key.rsplit("/", 1)[-1],
                    width=w, height=h, rotation=meta["rotation"],
                    visibility=m.VIS_PENDING, ingest_run_id=run_row.id,
                    imported_at=datetime.now(timezone.utc),
                    keywords_raw="|".join(meta["keywords"]) or None,
                )
                if taken:
                    if meta["taken_exact"]:
                        p.taken_at = taken
                    p.date_start = p.date_end = p.sort_date = taken.date()
                    p.date_precision = "day"
                    p.date_raw = (taken.strftime("%Y-%m-%dT%H:%M:%S") if meta["taken_exact"]
                                  else taken.strftime("%Y-%m-%d"))
                if meta["gps"]:
                    p.lat, p.lon = meta["gps"]
                    pid, _note = resolve_place({}, meta["gps"], place_names, place_geo)
                    if pid:
                        p.place_id = pid
                db.add(p)
                db.commit()                      # one photo = one commit: resumable
                new += 1
            except Exception as e:  # noqa: BLE001 — one bad file must not stop the run
                db.rollback()
                err_keys.append({"key": key, "error": f"{type(e).__name__}: {e}"[:200]})
                print(f"  ! {key}: {type(e).__name__}: {e}", file=sys.stderr)
            if i % 50 == 0:
                print(f"  {i}/{len(todo)}  {new} staged, {len(err_keys)} errors, "
                      f"{time.time() - t0:.0f}s", flush=True)

        # --- reconcile, write half: vanished and replaced masters ---
        guards, refreshed = [], 0
        if apply:
            r = reconcile.apply_missing(db, pl)
            rec["culled"], rec["restored"] = r["culled"], r["restored"]
            if r["guard"]:
                guards.append(r["guard"])
            if len(pl["replaced"]) > settings.ingest_max_refresh:
                guards.append(f"{len(pl['replaced'])} masters look replaced "
                              f"(limit {settings.ingest_max_refresh}) — none refreshed")
            else:
                for item in pl["replaced"]:
                    try:
                        refresh(db, client, item)
                        refreshed += 1
                    except Exception as e:  # noqa: BLE001
                        db.rollback()
                        err_keys.append({"key": item["key"],
                                         "error": f"refresh: {type(e).__name__}: {e}"[:200]})
            rec["refreshed"] = refreshed
        rec["guards"] = guards

        run_row.new, run_row.errors = new, len(err_keys)
        run_row.detail = json.dumps({"skipped": skipped, "errors": err_keys, "reconcile": rec})
        db.commit()
        # A few undecodable files are normal (there is a known truncated master). Most
        # of a batch failing is not — that is a broken decoder or a B2 fault, and it
        # must raise the alarm instead of reporting a quiet "ok" (which is exactly what
        # the first dev run did: "ok, 1 new, 59 errors" with the HEIC decoder missing).
        if len(err_keys) > max(5, len(todo) // 4):
            raise RuntimeError(f"{len(err_keys)} of {len(todo)} files failed — "
                               f"first: {err_keys[0]['error']}")

        if (new or refreshed) and not skip_faces:
            # Detection reads the display derivatives just written; both steps are
            # idempotent and cover any photo an earlier interrupted run left unscanned.
            from app import detect_faces, match_faces
            detect_faces.run(origin="digital")
            match_faces.run()

        if not skip_faces:
            # Junk score for anything not scored yet (skips itself if the model is absent).
            from app import junk
            junk.run()

        # Outings + triage: cheap, and worth redoing every night even with nothing new
        # (a face named or rejected since yesterday changes which photos are selected).
        from app import outings, stacks
        outings.run()
        stacks.run()                         # near-duplicate stacks; needs the outings

        if guards:
            # Everything else was done; the guard alone makes the run a failure so the
            # alert fires and a person looks at the listing before anything is culled.
            raise RuntimeError("reconcile guard: " + "; ".join(guards))

        run_row.state, run_row.finished_at = "ok", datetime.now(timezone.utc)
        db.commit()
        print(f"ingest ok: {new} staged as pending, {len(err_keys)} file error(s), "
              f"{time.time() - t0:.0f}s")
        # Machine-readable last line for the systemd wrapper / MQTT status (§15.12).
        print("INGEST_RESULT " + json.dumps({"state": "ok", "new": new, "errors": len(err_keys)}))
        return 0
    except Exception as e:  # noqa: BLE001 — run-level fault
        db.rollback()
        if run_row is not None:
            run_row.state, run_row.finished_at = "failed", datetime.now(timezone.utc)
            try:
                detail = json.loads(run_row.detail or "{}")
            except ValueError:
                detail = {}
            detail["fatal"] = f"{type(e).__name__}: {e}"[:500]
            run_row.detail = json.dumps(detail)
            db.commit()
        print(f"ingest FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        print("INGEST_RESULT " + json.dumps({"state": "failed", "new": new, "errors": 1}))
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Nightly B2 ingest -> pending inbox (SPEC §15.4)")
    ap.add_argument("--dry-run", action="store_true", help="list what would be ingested; no writes")
    ap.add_argument("--limit", type=int, default=None, help="first N candidates (testing)")
    ap.add_argument("--skip-faces", action="store_true", help="stage only; no detection/matching")
    a = ap.parse_args()
    sys.exit(run(dry_run=a.dry_run, limit=a.limit, skip_faces=a.skip_faces))
