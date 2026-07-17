"""Non-destructive importer for scanned/digital photos (SPEC §12.6).

Walks the exported photo library (`<library_root>/photos/**`), reads each file's
embedded Lightroom metadata (people/face-regions, keywords, caption, IPTC
location, GPS) plus the FastFoto batch date encoded in the folder/filename, and
upserts one `photo` row per front image — resolving tags against the *existing*
DB vocabularies (person aliases, event names, gazetteer places). It NEVER touches
`origin='slide'` rows and is safe to run repeatedly on the live prod DB.

Runs inside the API container (no `importer/` dependency):
    docker compose exec api python -m app.import_photos [--dry-run]
    (dev: from backend/, `python -m app.import_photos`)

Design choices (SPEC §12.6):
- Identity = library-relative path (`photos/<batch>/<file>`), globally unique.
- Non-destructive & idempotent: tags are UNION-added (never deleted — an admin's
  in-app tags survive a re-run); scalar fields (caption/title/date/place/dims) are
  filled only when currently empty, so re-imports never clobber human edits.
- Places resolve against existing DB places only (proximity for GPS, name match for
  IPTC text). New places are NOT auto-created — unresolved GPS/text go to the review
  report for Steve to add via the admin pin editor, then re-run. (Reverse-geocoding
  is intentionally omitted: `importer/geocode.py` isn't in the container image and
  network calls during a prod-DB import are undesirable.)
- People/events are never auto-created — unresolved names/keywords go to review.
"""
import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime, timezone
from math import cos, radians
from pathlib import Path

from app import metadata, models as m
from app.config import settings
from app.database import SessionLocal
from app.dates import parse_scan_date

PHOTOS_SUBDIR = "photos"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
# Hierarchy parents / batch noise that are never a vocabulary term.
KEYWORD_NOISE = {"people", "places", "events", "slides", "scans", "scanned"}
# Max distance (km) for snapping a GPS point to an existing gazetteer place.
GPS_MATCH_KM = 25.0

REVIEW_DIR = Path(settings.db_path).resolve().parent / "review"


# ---- resolution indexes (built once from the live DB) ---------------------------
def build_people_index(db):
    """norm(name) -> {person_id}, from person_alias + canonical names."""
    idx: dict[str, set[str]] = defaultdict(set)
    for pid, name in db.query(m.Person.id, m.Person.canonical_name).all():
        idx[metadata.norm_text(name)].add(pid)
    for pid, alias in db.query(m.PersonAlias.person_id, m.PersonAlias.alias).all():
        idx[metadata.norm_text(alias)].add(pid)
    return idx


def build_event_index(db):
    """lower(name) -> event_id."""
    return {name.lower(): eid for eid, name in db.query(m.Event.id, m.Event.name).all()}


def build_place_index(db):
    """(name_map: norm(name)->place_id, geo: [(id, lat, lon)]) for text + GPS match."""
    name_map: dict[str, str] = {}
    geo: list[tuple[str, float, float]] = []
    for pl in db.query(m.Place).all():
        name_map.setdefault(metadata.norm_text(pl.canonical_name), pl.id)
        if pl.region:
            name_map.setdefault(metadata.norm_text(pl.region), pl.id)
        if pl.lat is not None and pl.lon is not None:
            geo.append((pl.id, pl.lat, pl.lon))
    return name_map, geo


def _km(lat1, lon1, lat2, lon2) -> float:
    """Cheap equirectangular distance — fine at gazetteer scale."""
    x = (lon2 - lon1) * cos(radians((lat1 + lat2) / 2))
    y = lat2 - lat1
    return 111.0 * (x * x + y * y) ** 0.5


def resolve_place(loc: dict, gps, name_map, geo):
    """Return (place_id | None, review_note | None). GPS wins; on a GPS miss, fall
    back to IPTC text before giving up (SPEC §12.6)."""
    gps_note = None
    if gps:
        lat, lon = gps
        best, best_d = None, GPS_MATCH_KM
        for pid, plat, plon in geo:
            d = _km(lat, lon, plat, plon)
            if d < best_d:
                best, best_d = pid, d
        if best:
            return best, None
        gps_note = f"gps {lat:.5f},{lon:.5f} (no place within {GPS_MATCH_KM:.0f}km)"
    # IPTC text, most-specific first.
    city, state = loc.get("city"), loc.get("state")
    candidates = [loc.get("sublocation"), city,
                  f"{city}, {state}" if city and state else None, state]
    for c in candidates:
        if c and metadata.norm_text(c) in name_map:
            return name_map[metadata.norm_text(c)], None
    label = ", ".join(v for v in (loc.get("sublocation"), city, state) if v)
    if label:
        return None, f"{gps_note + '; ' if gps_note else ''}iptc '{label}' (no matching place)"
    return None, gps_note


# ---- file classification (SPEC §12.6) -------------------------------------------
def _stem(path: Path) -> str:
    return path.name[: -len(path.suffix)]


def classify_folder(files: list[Path]):
    """Split a folder's image files into (chosen_fronts, back_by_base, dup_pairs,
    unpaired_backs). Handles _b backs and _a enhanced-copy dedup."""
    fronts = [f for f in files if not _stem(f).endswith("_b")]
    backs = {_stem(f)[:-2]: f for f in files if _stem(f).endswith("_b")}  # base -> back file
    front_stems = {_stem(f): f for f in fronts}

    dup_pairs, skip = [], set()
    for stem, f in front_stems.items():
        if stem.endswith("_a"):
            base = stem[:-2]
            if base in front_stems:
                dup_pairs.append((front_stems[base], f))
                skip.add(base)  # prefer the enhanced _a copy

    chosen, used_backs = [], set()
    for stem, f in front_stems.items():
        if stem in skip:
            continue
        base = stem[:-2] if stem.endswith("_a") else stem
        back = backs.get(base)
        if back:
            used_backs.add(base)
        chosen.append((f, back))
    unpaired = [bf for base, bf in backs.items() if base not in used_backs]
    return chosen, dup_pairs, unpaired


# ---- main -----------------------------------------------------------------------
def run(dry_run: bool = False) -> None:
    root = settings.library_root_path
    photos_dir = root / PHOTOS_SUBDIR
    if not photos_dir.exists():
        print(f"no photos dir at {photos_dir} — nothing to import")
        return

    db = SessionLocal()
    try:
        people_idx = build_people_index(db)
        valid_people = {r[0] for r in db.query(m.Person.id).all()}
        event_idx = build_event_index(db)
        place_names, place_geo = build_place_index(db)
        existing_pp = {(pp.photo_id, pp.person_id) for pp in db.query(m.PhotoPerson).all()}

        # Gather image files by folder.
        by_folder: dict[Path, list[Path]] = defaultdict(list)
        for f in photos_dir.rglob("*"):
            if not f.is_file() or f.name.startswith("._"):
                continue
            if f.suffix.lower() in IMAGE_EXTS:
                by_folder[f.parent].append(f)

        unresolved_people: dict[str, str] = {}
        unmatched_keywords: dict[str, str] = {}
        unresolved_places: list[tuple[str, str]] = []
        dup_report: list[tuple[str, str]] = []
        unpaired_backs: list[str] = []
        n_new = n_updated = n_people = n_events = n_placed = n_backs = 0

        for folder, files in sorted(by_folder.items()):
            rel_parent = folder.relative_to(photos_dir)
            batch = None if rel_parent == Path(".") else rel_parent.as_posix()
            chosen, dups, unpaired = classify_folder(files)
            for a, b in dups:
                dup_report.append((a.relative_to(root).as_posix(),
                                   b.relative_to(root).as_posix()))
            for bf in unpaired:
                unpaired_backs.append(bf.relative_to(root).as_posix())

            for front, back in chosen:
                source_file = front.relative_to(root).as_posix()
                back_path = back.relative_to(root).as_posix() if back else None
                if back:
                    n_backs += 1
                data = metadata.extract(front)
                ds, de, prec, raw = parse_scan_date(batch or _stem(front))

                photo = db.query(m.Photo).filter(m.Photo.source_file == source_file).first()
                is_new = photo is None
                if is_new:
                    photo = m.Photo(source_file=source_file, origin="scan",
                                    imported_at=datetime.now(timezone.utc))
                    db.add(photo)
                elif photo.origin == "slide":
                    continue  # never touch slides (defensive; scans never collide here)

                # Storage + provenance (safe to refresh every run).
                photo.origin = "scan"
                photo.storage_path = source_file
                photo.original_filename = front.name
                photo.batch = batch
                photo.back_path = back_path
                if data["width"]:
                    photo.width, photo.height = data["width"], data["height"]

                # Scalar metadata: fill-if-empty (never clobber human/in-app edits).
                def fill(attr, value):
                    if value and not getattr(photo, attr):
                        setattr(photo, attr, value)

                fill("caption", data["caption"])
                fill("original_subject", data["title"])
                if ds and not photo.date_start:
                    photo.date_start, photo.date_end = ds, de
                    photo.date_precision, photo.date_raw = prec, raw
                photo.sort_date = photo.date_start  # materialized sort key (§12.5)

                # Place (fill-if-empty).
                if not photo.place_id:
                    pid, note = resolve_place(data["location"], data["gps"],
                                              place_names, place_geo)
                    if pid:
                        photo.place_id = pid
                        n_placed += 1
                    elif note:
                        unresolved_places.append((source_file, note))

                db.flush()  # assign photo.id for new rows

                # People (union-add; store face-region box when named).
                region_by_pid: dict[str, tuple] = {}
                for name, cx, cy, w, h in data["face_regions"]:
                    for pid in people_idx.get(metadata.norm_text(name), set()):
                        if pid in valid_people:
                            region_by_pid[pid] = (cx, cy, w, h)
                resolved_pids: set[str] = set()
                for name in data["people"]:
                    pids = {p for p in people_idx.get(metadata.norm_text(name), set())
                            if p in valid_people}
                    if not pids:
                        unresolved_people.setdefault(name, source_file)
                        continue
                    resolved_pids |= pids
                for pid in resolved_pids | set(region_by_pid):
                    box = region_by_pid.get(pid)
                    if (photo.id, pid) in existing_pp:
                        if box and not dry_run:  # backfill/refresh a face box
                            pp = db.get(m.PhotoPerson, (photo.id, pid))
                            if pp and pp.region_w is None:
                                pp.region_x, pp.region_y, pp.region_w, pp.region_h = box
                        continue
                    if not dry_run:
                        db.add(m.PhotoPerson(
                            photo_id=photo.id, person_id=pid, source=m.SOURCE_HUMAN,
                            uncertain=False,
                            region_x=box[0] if box else None,
                            region_y=box[1] if box else None,
                            region_w=box[2] if box else None,
                            region_h=box[3] if box else None))
                    existing_pp.add((photo.id, pid))
                    n_people += 1

                # Events (union-add). People names echo into dc:subject — skip any
                # keyword that names a person on this photo (resolved or not) so an
                # unresolved friend doesn't also show up as an "unmatched keyword".
                people_names = {metadata.norm_text(n) for n in data["people"]}
                kw_terms = list(data["keywords"])
                for hk in data["hierarchical_keywords"]:
                    kw_terms.append(hk.split("|")[-1])  # leaf term
                seen_ev = {r[0] for r in db.query(m.PhotoEvent.event_id)
                           .filter(m.PhotoEvent.photo_id == photo.id).all()}
                for kw in kw_terms:
                    low = kw.lower().strip()
                    nkw = metadata.norm_text(kw)
                    if not low or low in KEYWORD_NOISE:
                        continue
                    eid = event_idx.get(low)
                    if eid:
                        if eid not in seen_ev:
                            if not dry_run:
                                db.add(m.PhotoEvent(photo_id=photo.id, event_id=eid,
                                                    source=m.SOURCE_HUMAN))
                            seen_ev.add(eid)
                            n_events += 1
                    elif nkw not in people_idx and nkw not in people_names:
                        unmatched_keywords.setdefault(kw, source_file)

                n_new += is_new
                n_updated += not is_new

        if not dry_run:
            db.commit()

        _write_reports(unresolved_people, unmatched_keywords, unresolved_places,
                       dup_report, unpaired_backs)

        tag = " (DRY RUN)" if dry_run else ""
        print(f"=== IMPORT PHOTOS{tag} ===")
        print(f"folders:            {len(by_folder)}")
        print(f"photos new:         {n_new}")
        print(f"photos updated:     {n_updated}")
        print(f"people tags added:  {n_people}")
        print(f"event tags added:   {n_events}")
        print(f"places resolved:    {n_placed}")
        print(f"backs linked:       {n_backs}")
        print(f"unresolved people:  {len(unresolved_people)} -> review/unresolved_people.csv")
        print(f"unmatched keywords: {len(unmatched_keywords)} -> review/unmatched_keywords.csv")
        print(f"unresolved places:  {len(unresolved_places)} -> review/unresolved_places.csv")
        print(f"duplicate versions: {len(dup_report)} -> review/duplicate_versions.csv")
        print(f"unpaired backs:     {len(unpaired_backs)} -> review/unpaired_backs.csv")
        if not dry_run:
            print("\nNext: python -m app.prewarm   (thumbnails + display derivatives)")
    finally:
        db.close()


def _write_reports(people, keywords, places, dups, backs) -> None:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    def dump(name, header, rows):
        with open(REVIEW_DIR / name, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    dump("unresolved_people.csv", ["lr_name", "sample_file", "action"],
         [[n, s, "add person / alias (as family or non-family) then re-run"]
          for n, s in sorted(people.items())])
    dump("unmatched_keywords.csv", ["keyword", "sample_file", "action"],
         [[k, s, "add to event vocab / ignore"] for k, s in sorted(keywords.items())])
    dump("unresolved_places.csv", ["source_file", "note"], places)
    dump("duplicate_versions.csv", ["base", "enhanced_a (imported)"], dups)
    dump("unpaired_backs.csv", ["back_file"], [[b] for b in backs])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Import scanned/digital photos (SPEC §12.6)")
    ap.add_argument("--dry-run", action="store_true", help="report only, no DB writes")
    args = ap.parse_args()
    run(dry_run=args.dry_run)
