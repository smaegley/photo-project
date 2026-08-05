"""Importer for born-digital photos backed by Backblaze B2 (SPEC §13.14 slice 4).

Consumes the sidecar that `read_lrcat --digital` emits — the Lightroom catalog is the
selection *and* the metadata source — and upserts one `origin='digital'` Photo row per
selected photo, pointing at a **B2 object key** rather than a local file:

    docker compose exec api python -m app.import_digital --dry-run
    docker compose exec api python -m app.import_digital
    (dev: from backend/, `python -m app.import_digital`)

How this differs from `import_photos` (scans), and why:

- **No filesystem walk.** Selection comes from the catalog (people rule, §13.14), so the
  sidecar is the whole input. Nothing is exported and no pixels move at import.
- **The key has to be *found*, not assumed.** The sidecar proposes `<stem>.jpg`, but the
  bucket holds the camera's own casing: most RAW originals (`.cr2/.orf/.cr3`) sit beside
  an UPPERCASE `.JPG` twin, and iPhone HEICs have no JPG twin at all. So each row probes
  a small ordered candidate list against B2 (HEAD) and records the key that actually
  exists, plus its ETag as `file_version` — after which serving never touches B2.
- **A missing object is normal, not an error.** DS418 -> B2 syncs nightly, so a photo
  tagged today can be up to ~24h ahead of the bucket. Those rows go to
  `review/not_in_b2_yet.csv` and are swept up by the next run (§13.8). Re-running is the
  workflow, not error recovery.
- **`--prune` gives deletion.** Untagging in Lightroom drops a photo from the sidecar;
  with `--prune` its row (and tags) are removed, making the catalog a true bidirectional
  control surface. Gated behind the flag so a truncated sidecar can't silently empty the
  gallery.

Shared with `import_photos` by import, not by copy: the people/event/place resolution
indexes, the union-add tagging rules, and the never-auto-create policy (unresolved names
and keywords go to review CSVs; run `seed_people` first).
"""
import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from app import metadata, models as m, storage
from app.config import settings
from app.database import SessionLocal
from app.import_photos import (KEYWORD_NOISE, REVIEW_DIR, build_event_index,
                               build_people_index, build_place_index, resolve_place)

DEFAULT_SIDECAR = "digital_sidecar.csv"
HEAD_WORKERS = 12          # concurrent B2 HEADs; ~4.7k rows is minutes sequentially
ORIGIN = "digital"

# Extensions whose originals are RAW/edited masters. Pillow can't render these and
# rendering them outside Lightroom would ignore develop settings (§13.2 #4), so if no
# JPG twin exists they are reported rather than imported.
RAW_EXTS = {"cr2", "cr3", "orf", "nef", "dng", "arw", "raf", "rw2"}
EDITED_EXTS = {"psd", "tif", "tiff"}

# Lightroom import/collection keywords that ride along on born-digital photos. These
# are sync plumbing, not events — 449 "Google Upload" + 92 "Photo Stream" in the
# 2026-07-26 catalog, which would otherwise dominate the unmatched-keyword report.
DIGITAL_NOISE = {"google upload", "photo stream", "google photos", "imported"}


def person_tokens(names: set[str]) -> set[str]:
    """Normalized full names plus their individual word tokens.

    The catalog names the same human two ways: the face/people field carries the full
    name ("Kate Maegley") while the keyword list carries a short form ("Kate"). Without
    expanding to tokens, every tagged person's first name reads as a stray event keyword
    — 638 "Kate" and 558 "Ryan" in this catalog alone. Only tokens from people tagged on
    *this* photo are suppressed, so a genuine event keyword is unaffected unless it
    collides with the name of someone in the same frame.
    """
    out: set[str] = set()
    for n in names:
        norm = metadata.norm_text(n)
        out.add(norm)
        out.update(t for t in norm.split() if len(t) >= 3)
    return out


def candidate_keys(b2_key: str, orig_exts: str) -> list[str]:
    """Ordered B2 keys to probe for a sidecar row, best guess first.

    `read_lrcat` proposes a lowercase `.jpg`; the bucket has whatever the camera and
    the sync chain produced. Ordering matters only for speed — the first HEAD that hits
    wins, so a good first guess keeps this to ~1 round trip per photo:
      - a JPG original is already right;
      - a RAW original's twin is UPPERCASE `.JPG` (verified across 875 rows, §13.14);
      - HEIC has no twin, so the HEIC itself is the master (decoded via pillow-heif).
    """
    stem = b2_key.rsplit(".", 1)[0]
    exts = {e.strip().lower() for e in (orig_exts or "").split("|") if e.strip()}
    jpgs = [f"{stem}{e}" for e in (".jpg", ".JPG", ".jpeg", ".JPEG")]

    if exts & {"jpg", "jpeg"}:
        return jpgs
    if "heic" in exts:
        return [f"{stem}.HEIC", f"{stem}.heic", *jpgs]
    if "png" in exts:
        return [f"{stem}.png", f"{stem}.PNG", *jpgs]
    if exts & RAW_EXTS:
        return [f"{stem}.JPG", f"{stem}.jpg", f"{stem}.jpeg", f"{stem}.JPEG"]
    return jpgs  # psd/tif and anything unexpected: try the twin, else report


def global_person_tokens(db) -> set[str]:
    """Every known person's normalized name, plus its individual word tokens.

    The per-photo check in `person_tokens` can't catch a keyword naming someone who
    isn't face-tagged in that particular frame — "Kate" shows up as a keyword on photos
    Kate isn't tagged in. Matching against all known people fixes that without
    swallowing real events: a multi-word keyword like "Baker Reunion" normalizes to
    "baker reunion" and never equals the single token "baker".

    This only affects *reporting*. The event vocabulary is consulted first, so a term
    that is a real event still becomes an event tag even if it collides with a person's
    name; the worst case here is a genuinely new event staying out of the review CSV.
    """
    out: set[str] = set()
    names = [n for (n,) in db.query(m.Person.canonical_name).all()]
    names += [a for (a,) in db.query(m.PersonAlias.alias).all()]
    for n in names:
        norm = metadata.norm_text(n)
        out.add(norm)
        out.update(t for t in norm.split() if len(t) >= 3)
    return out


def is_raw_only(orig_exts: str) -> bool:
    """True when the row's originals are all RAW/edited — i.e. a missing JPG twin is a
    permanent 'no servable master', not nightly-sync lag."""
    exts = {e.strip().lower() for e in (orig_exts or "").split("|") if e.strip()}
    return bool(exts) and not (exts & {"jpg", "jpeg", "heic", "png"})


def parse_capture(value: str):
    """Sidecar `capture_date` (catalog EXIF DateTimeOriginal) -> (date, precision, raw).

    EXIF leads for digital and is precise to the second, which inverts §12.4's
    filename-first rule for scans — deliberately: scan EXIF is scanner-derived, digital
    EXIF is camera-truth (§13.2 #5).
    """
    v = (value or "").strip()
    if not v:
        return None, None, None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, fmt).date(), "day", v
        except ValueError:
            continue
    try:  # bare year, or anything else with a leading year
        return date(int(v[:4]), 1, 1), "year", v
    except (ValueError, IndexError):
        return None, None, v


def parse_gps(value: str):
    """Sidecar `gps` ("lat,lon") -> (lat, lon) | None."""
    v = (value or "").strip()
    if not v or "," not in v:
        return None
    try:
        lat, lon = (float(x) for x in v.split(",", 1))
    except ValueError:
        return None
    return (lat, lon)


def _resolve_all(rows: list[dict]) -> dict[str, tuple[str | None, str | None]]:
    """HEAD each row's candidates in parallel -> {sidecar_key: (found_key, etag)}.

    The only place B2 is touched during an import, and the reason serving never has to:
    the ETag recorded here becomes `file_version` (§13.4).
    """
    def probe(row):
        for key in candidate_keys(row["b2_key"], row.get("orig_exts", "")):
            head = storage.b2_head_key(key)
            if head is not None:
                return row["b2_key"], (key, storage.b2_token(head))
        return row["b2_key"], (None, None)

    out: dict[str, tuple[str | None, str | None]] = {}
    with ThreadPoolExecutor(max_workers=HEAD_WORKERS) as pool:
        for i, (sidecar_key, found) in enumerate(pool.map(probe, rows), 1):
            out[sidecar_key] = found
            if i % 500 == 0:
                print(f"  resolved {i}/{len(rows)} keys against B2 ...", flush=True)
    return out


def run(sidecar: Path, dry_run: bool = False, prune: bool = False,
        limit: int | None = None) -> None:
    if not settings.b2_enabled:
        sys.exit("B2 is not configured — set B2_* in backend/.env (SPEC §13.3)")
    path = sidecar if sidecar.is_absolute() else REVIEW_DIR / sidecar.name
    if not path.exists():
        sys.exit(f"sidecar not found: {path}  (run: python -m app.read_lrcat --digital)")

    with open(path, newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("b2_key") or "").strip()]
    if limit:
        rows = rows[:limit]
    if not rows:
        print("sidecar has no rows — nothing to import")
        return

    print(f"resolving {len(rows)} keys against B2 ({HEAD_WORKERS} workers) ...", flush=True)
    resolved = _resolve_all(rows)

    db = SessionLocal()
    try:
        people_idx = build_people_index(db)
        valid_people = {r[0] for r in db.query(m.Person.id).all()}
        event_idx = build_event_index(db)
        place_names, place_geo = build_place_index(db)
        all_person_tokens = global_person_tokens(db)
        existing_pp = {(pp.photo_id, pp.person_id) for pp in db.query(m.PhotoPerson).all()}

        unresolved_people: dict[str, str] = {}
        unmatched_keywords: dict[str, str] = {}
        unresolved_places: list[tuple[str, str]] = []
        not_in_b2: list[tuple[str, str]] = []
        raw_only: list[tuple[str, str]] = []
        n_new = n_updated = n_people = n_events = n_placed = 0
        seen_keys: set[str] = set()

        for r in rows:
            sidecar_key = r["b2_key"]
            found_key, etag = resolved.get(sidecar_key, (None, None))
            if not found_key:
                # No servable master. RAW-only is permanent (no JPG twin was ever
                # written); anything else is almost certainly the nightly sync lag.
                if is_raw_only(r.get("orig_exts", "")):
                    raw_only.append((sidecar_key, r.get("orig_exts", "")))
                else:
                    not_in_b2.append((sidecar_key, r.get("orig_exts", "")))
                continue
            seen_keys.add(found_key)

            photo = db.query(m.Photo).filter(m.Photo.source_file == found_key).first()
            is_new = photo is None
            if is_new:
                photo = m.Photo(source_file=found_key, origin=ORIGIN,
                                imported_at=datetime.now(timezone.utc))
                db.add(photo)
            elif photo.origin != ORIGIN:
                continue  # never touch slides/scans (defensive; keys can't collide)

            # Storage identity — safe to refresh every run. A changed ETag means the
            # master changed, which also re-keys the derivative cache (§13.4).
            photo.origin = ORIGIN
            photo.storage_backend = "b2"
            photo.storage_path = found_key
            if photo.file_version and etag != photo.file_version and photo.rotation:
                # A replaced master presumably arrives correctly oriented (the usual
                # reason to re-export is baking a rotation in LR) — keeping the
                # display-time override would double-rotate it.
                print(f"  resetting rotation override on changed master: {found_key}")
                photo.rotation = 0
            photo.file_version = etag
            photo.original_filename = found_key.rsplit("/", 1)[-1]

            def fill(attr, value):
                if value and not getattr(photo, attr):
                    setattr(photo, attr, value)

            ds, prec, raw = parse_capture(r.get("capture_date", ""))
            if ds and not photo.date_start:
                photo.date_start, photo.date_end = ds, ds
                photo.date_precision, photo.date_raw = prec, raw
            photo.sort_date = photo.date_start  # materialized sort key (§12.5)

            gps = parse_gps(r.get("gps", ""))
            if gps:                      # keep the coordinates regardless of resolution
                photo.lat, photo.lon = gps
            if not photo.place_id:
                pid, note = resolve_place({}, gps, place_names, place_geo)
                if pid:
                    photo.place_id = pid
                    n_placed += 1
                elif note:
                    unresolved_places.append((found_key, note))

            db.flush()  # assign photo.id for new rows

            # People — union-add, never auto-create (seed_people first).
            people_names = {p for p in (r.get("people") or "").split("|") if p}
            for name in people_names:
                pids = {p for p in people_idx.get(metadata.norm_text(name), set())
                        if p in valid_people}
                if not pids:
                    unresolved_people.setdefault(name, found_key)
                    continue
                for pid in pids:
                    if (photo.id, pid) in existing_pp:
                        continue
                    if not dry_run:
                        db.add(m.PhotoPerson(photo_id=photo.id, person_id=pid,
                                             source=m.SOURCE_HUMAN, uncertain=False))
                    existing_pp.add((photo.id, pid))
                    n_people += 1

            # Events — union-add against the existing vocabulary only. Person names
            # echo into the catalog's keyword list, so skip any keyword naming a person
            # on this photo (else an unresolved friend also reads as a stray keyword).
            norm_people = person_tokens(people_names)
            seen_ev = {x[0] for x in db.query(m.PhotoEvent.event_id)
                       .filter(m.PhotoEvent.photo_id == photo.id).all()}
            for kw in (r.get("events") or "").split("|"):
                low = kw.lower().strip()
                nkw = metadata.norm_text(kw)
                if not low or low in KEYWORD_NOISE or low in DIGITAL_NOISE:
                    continue
                eid = event_idx.get(low)
                if eid:
                    if eid not in seen_ev:
                        if not dry_run:
                            db.add(m.PhotoEvent(photo_id=photo.id, event_id=eid,
                                                source=m.SOURCE_HUMAN))
                        seen_ev.add(eid)
                        n_events += 1
                elif (nkw not in people_idx and nkw not in norm_people
                      and nkw not in all_person_tokens):
                    unmatched_keywords.setdefault(kw, found_key)

            n_new += is_new
            n_updated += not is_new

        # Deletion by sidecar diff (§13.8, decision #7) — untagged in Lightroom.
        pruned = [p for p in db.query(m.Photo).filter(m.Photo.origin == ORIGIN).all()
                  if p.source_file not in seen_keys]
        if prune and not dry_run:
            for p in pruned:
                db.query(m.PhotoPerson).filter(m.PhotoPerson.photo_id == p.id).delete()
                db.query(m.PhotoEvent).filter(m.PhotoEvent.photo_id == p.id).delete()
                db.delete(p)

        if not dry_run:
            db.commit()

        _write_reports(unresolved_people, unmatched_keywords, unresolved_places,
                       not_in_b2, raw_only)

        tag = " (DRY RUN)" if dry_run else ""
        print(f"\n=== IMPORT DIGITAL{tag} ===")
        print(f"sidecar rows:       {len(rows)}")
        print(f"keys resolved:      {len(seen_keys)}")
        print(f"photos new:         {n_new}")
        print(f"photos updated:     {n_updated}")
        print(f"people tags added:  {n_people}")
        print(f"event tags added:   {n_events}")
        print(f"places resolved:    {n_placed}")
        print(f"not in B2 yet:      {len(not_in_b2)} -> review/not_in_b2_yet.csv (sync lag; re-run later)")
        print(f"raw-only (no JPG):  {len(raw_only)} -> review/raw_only.csv")
        print(f"unresolved people:  {len(unresolved_people)} -> review/unresolved_people_digital.csv")
        print(f"unmatched keywords: {len(unmatched_keywords)} -> review/unmatched_keywords_digital.csv")
        print(f"unresolved places:  {len(unresolved_places)} -> review/unresolved_places_digital.csv")
        if pruned:
            verb = "pruned" if (prune and not dry_run) else "WOULD prune (pass --prune)"
            print(f"no longer selected: {len(pruned)} {verb}")
        if not dry_run:
            print("\nNext: python -m app.prewarm   (fetches each master from B2 once)")
    finally:
        db.close()


def _write_reports(people, keywords, places, not_in_b2, raw_only) -> None:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    def dump(name, header, rows):
        with open(REVIEW_DIR / name, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    dump("unresolved_people_digital.csv", ["lr_name", "sample_key", "action"],
         [[n, s, "add via people_seed_digital.csv + seed_people, then re-run"]
          for n, s in sorted(people.items())])
    dump("unmatched_keywords_digital.csv", ["keyword", "sample_key", "action"],
         [[k, s, "add to event vocab / ignore"] for k, s in sorted(keywords.items())])
    dump("unresolved_places_digital.csv", ["b2_key", "note"], places)
    dump("not_in_b2_yet.csv", ["b2_key", "orig_exts"], not_in_b2)
    dump("raw_only.csv", ["b2_key", "orig_exts"], raw_only)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Import B2-backed digital photos (SPEC §13.14)")
    ap.add_argument("--sidecar", type=Path, default=Path(DEFAULT_SIDECAR),
                    help=f"read_lrcat --digital output (default: {DEFAULT_SIDECAR})")
    ap.add_argument("--dry-run", action="store_true", help="report only, no DB writes")
    ap.add_argument("--prune", action="store_true",
                    help="delete digital rows no longer in the sidecar (untagged in LR)")
    ap.add_argument("--limit", type=int, default=None, help="first N rows (testing)")
    args = ap.parse_args()
    run(args.sidecar, dry_run=args.dry_run, prune=args.prune, limit=args.limit)
