"""Maegley Photo Album — importer (SPEC §3.9).

Loads the manifest + three curation files into SQLite via the SQLAlchemy models.
Idempotent: rebuilds the manifest-derived content tables on each run (users,
invites and contributions are left untouched). Emits review reports for tags it
could not resolve, so curation can iterate and the importer be re-run.

Run:  python -m importer.import_data        (from repo root, with backend on path)
"""
import csv
import glob
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app import models as m  # noqa: E402
from app.config import settings  # noqa: E402
from app.dates import parse_date_raw  # noqa: E402
from app.metadata import image_size, norm_text as _norm, canon_alias_key  # noqa: E402


def slide_rel_path(magazine: int, source_file: str) -> str:
    """Library-relative path for a slide (SPEC §11.5 storage_path)."""
    return f"slides/Mag{magazine}/{source_file}"

# --- inputs ---
MANIFEST = glob.glob("/mnt/photos/**/slide_manifest.csv", recursive=True)[0]
PEOPLE_CANON = REPO_ROOT / "people_canon_firstpass.csv"
PLACES_GAZ = REPO_ROOT / "places_gazetteer_firstpass.csv"
EVENTS_FP = REPO_ROOT / "events_firstpass.csv"
REVIEW_DIR = REPO_ROOT / "importer" / "review"

# Full event vocabulary (SPEC §3.6) — seeded even where 0 photos match.
EVENT_VOCAB = [
    "Birth", "Baptism", "Adoption", "First Communion", "Graduation", "Wedding",
    "Funeral", "Birthday", "Milestone", "Christmas", "Easter", "Thanksgiving",
    "Independence Day", "Vacation/Trip", "Move/New Home",
]


# ---------- normalization (SPEC §3.9 people rules; shared helpers in metadata.py) ----------
def manifest_token_key(t: str) -> str:
    """Manifest people token: drop parenthetical role hints like (Dad)/(Mom)."""
    t = re.sub(r"\([^)]*\)", "", t)
    return _norm(t)


# ---------- loaders ----------
def load_people(session, rows):
    """canon -> person + person_alias; returns alias_key -> {person_id,...}.

    Two-pass: insert all persons with null self-FKs first, then wire
    father/mother/spouse (forward references within one batch otherwise trip the
    self-referential FK constraint on SQLite).
    """
    alias_map = defaultdict(set)
    persons = {}
    rels = {}
    for r in rows:
        if r.get("include", "Y").strip().upper() != "Y":
            continue
        pid = r["person_id"].strip()
        persons[pid] = m.Person(
            id=pid,
            canonical_name=r["canonical_name"].strip(),
            notes=r["notes"].strip() or None,
            # NB: relationship_to_steve is review-only and intentionally NOT stored.
        )
        session.add(persons[pid])
        rels[pid] = (
            r["father_id"].strip() or None,
            r["mother_id"].strip() or None,
            r["spouse_id"].strip() or None,
        )
        keys = [r["canonical_name"]] + r["aliases_seen"].split(";")
        seen = set()
        for k in keys:
            key = canon_alias_key(k)
            if key:
                alias_map[key].add(pid)
                if key not in seen:
                    session.add(m.PersonAlias(person_id=pid, alias=key))
                    seen.add(key)
    session.flush()
    # Second pass: now every person row exists, so the FKs resolve.
    for pid, (f, mo, sp) in rels.items():
        p = persons[pid]
        p.father_id = f if f in persons else None
        p.mother_id = mo if mo in persons else None
        p.spouse_id = sp if sp in persons else None
    session.flush()
    return alias_map


def load_places(session, rows):
    """gazetteer -> place; returns raw_variant_key -> place_id."""
    variant_map = {}
    for r in rows:
        pid = r["place_id"].strip()
        lat = r["lat"].strip()
        lon = r["lon"].strip()
        session.add(m.Place(
            id=pid,
            canonical_name=r["canonical_name"].strip(),
            region=r["region"].strip() or None,
            precision=r["precision"].strip() or "unknown",
            lat=float(lat) if lat else None,
            lon=float(lon) if lon else None,
        ))
        for variant in r["raw_variants_merged"].split("|"):
            key = _norm(variant)
            if key:
                variant_map[key] = pid
        variant_map.setdefault(_norm(r["canonical_name"]), pid)
    session.flush()
    return variant_map


def load_events(session):
    """Seed the event vocabulary; returns name -> Event."""
    by_name = {}
    for name in EVENT_VOCAB:
        ev = m.Event(name=name)
        session.add(ev)
        by_name[name] = ev
    session.flush()
    return by_name


def load_magazines(session, manifest_rows):
    """Derive 32 magazine rows from the manifest; attach card images by filename."""
    cards = defaultdict(list)
    cards_dir = Path("/mnt/photos/library/index_cards")
    if cards_dir.exists():
        for f in sorted(cards_dir.glob("Mag*_card_*.jpg")):
            mm = re.match(r"Mag(\d+)_card", f.name)
            if mm:
                cards[int(mm.group(1))].append(f.name)
    by_id = {}
    grouped = defaultdict(list)
    for r in manifest_rows:
        grouped[int(r["magazine"])].append(r)
    for num, rs in sorted(grouped.items()):
        first = rs[0]
        ds, de, _ = parse_date_raw(first["mag_date_span"]) if first.get("mag_date_span") else (None, None, None)
        mag = m.Magazine(
            id=num,
            title=(first["mag_subject"].strip() or None),
            span_label=(first["mag_date_span"].strip() or None),
            date_start=ds, date_end=de,
            slide_count=len(rs),
            card_image_paths=json.dumps(cards.get(num, [])),
        )
        session.add(mag)
        by_id[num] = mag
    session.flush()
    return by_id


# ---------- main pass ----------
def wipe_content(session):
    """Rebuild content tables; leave user/invite/contribution intact.

    Clear person.representative_photo_id (FK -> photo) first, otherwise deleting
    photos trips the FK when an admin has chosen a person thumbnail. Persons are
    recreated from the canon below, so this value isn't preserved across a reimport
    anyway.
    """
    session.query(m.Person).update({m.Person.representative_photo_id: None})
    session.flush()
    for model in (m.PhotoPerson, m.PhotoEvent, m.Photo, m.PersonAlias,
                  m.Person, m.Place, m.Event, m.Magazine):
        session.query(model).delete()
    session.flush()


def run():
    # Schema is owned by Alembic (run `alembic upgrade head` first). Fall back to
    # create_all only if the tables are missing, so a fresh dev DB still works.
    from sqlalchemy import inspect
    if not inspect(engine).has_table("photo"):
        Base.metadata.create_all(engine)
    manifest = list(csv.DictReader(open(MANIFEST)))
    people_rows = list(csv.DictReader(open(PEOPLE_CANON)))
    place_rows = list(csv.DictReader(open(PLACES_GAZ)))
    event_rows = list(csv.DictReader(open(EVENTS_FP)))
    events_by_file = {r["organized_file"]: r for r in event_rows}

    session = SessionLocal()
    try:
        wipe_content(session)
        alias_map = load_people(session, people_rows)
        variant_map = load_places(session, place_rows)
        events_by_name = load_events(session)
        load_magazines(session, manifest)

        unresolved = Counter()
        unresolved_samples = {}
        place_misses = Counter()
        date_misses = []
        n_people_tags = n_event_tags = 0

        for r in manifest:
            ds, de, prec = parse_date_raw(r["date_raw"])
            if ds is None and r["date_raw"].strip():
                date_misses.append(r["date_raw"])

            place_id = None
            if r["place"].strip():
                key = _norm(r["place"])
                place_id = variant_map.get(key)
                if place_id is None:
                    place_misses[r["place"].strip()] += 1

            source_file = r["organized_file"].strip()
            mag_num = int(r["magazine"])
            rel_path = slide_rel_path(mag_num, source_file)
            slide_path = Path(settings.library_root) / rel_path
            w, h = image_size(slide_path) if slide_path.exists() else (None, None)

            photo = m.Photo(
                source_file=source_file,
                origin="slide",
                storage_path=rel_path,
                width=w, height=h,
                caption=r["card_caption"].strip() or None,
                original_subject=r["mag_subject"].strip() or None,
                date_start=ds, date_end=de, date_precision=prec,
                date_raw=r["date_raw"].strip() or None,
                place_id=place_id,
                magazine_id=mag_num,
                slide_in_mag=int(r["slide_in_mag"]) if r["slide_in_mag"].strip() else None,
                validation=r["validation"].strip() or None,
                notes=r["notes"].strip() or None,
            )
            session.add(photo)
            session.flush()  # assign photo.id

            # --- people tags (SPEC §3.9) ---
            tagged = set()  # (photo_id, person_id) already applied — dedupes across sources
            if r["people"].strip():
                for raw in r["people"].split(";"):
                    raw = raw.strip()
                    if not raw:
                        continue
                    uncertain = raw.endswith("?")
                    key = manifest_token_key(raw.rstrip("?"))
                    if not key:
                        continue
                    pids = alias_map.get(key)
                    if not pids:
                        unresolved[key] += 1
                        unresolved_samples.setdefault(key, r["card_caption"][:60])
                        continue
                    for pid in pids:  # group form -> all members
                        if (photo.id, pid) in tagged:
                            continue
                        session.add(m.PhotoPerson(
                            photo_id=photo.id, person_id=pid,
                            source=m.SOURCE_MANIFEST, uncertain=uncertain,
                        ))
                        tagged.add((photo.id, pid))
                        n_people_tags += 1
            # Lightroom face-tag people are applied as a separate non-destructive
            # overlay (importer/apply_lr_people.py, SPEC §11.6) — run after this.

            # --- event tags (SPEC §3.9) ---
            ev_row = events_by_file.get(r["organized_file"].strip())
            if ev_row and ev_row["suggested_events"].strip():
                for name in ev_row["suggested_events"].split(";"):
                    name = name.strip()
                    if not name:
                        continue
                    ev = events_by_name.get(name)
                    if ev is None:
                        ev = m.Event(name=name)
                        session.add(ev)
                        session.flush()
                        events_by_name[name] = ev
                    session.add(m.PhotoEvent(
                        photo_id=photo.id, event_id=ev.id, source=m.SOURCE_AUTO,
                    ))
                    n_event_tags += 1

        session.commit()

        # --- review reports ---
        REVIEW_DIR.mkdir(parents=True, exist_ok=True)
        with open(REVIEW_DIR / "people_unresolved.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["token", "count", "sample_caption", "likely"])
            for tok, cnt in unresolved.most_common():
                likely = "collective/friend (drop?)" if tok in {
                    "family", "friends", "cousins", "kids", "the gang", "their kids"
                } else "review: add alias or confirm out-of-scope"
                w.writerow([tok, cnt, unresolved_samples.get(tok, ""), likely])
        with open(REVIEW_DIR / "places_unresolved.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["raw_place", "count"])
            for p, c in place_misses.most_common():
                w.writerow([p, c])

        # --- summary ---
        print("=== IMPORT COMPLETE ===")
        print(f"photos:        {session.query(m.Photo).count()}")
        print(f"magazines:     {session.query(m.Magazine).count()}")
        print(f"persons:       {session.query(m.Person).count()}")
        print(f"person_alias:  {session.query(m.PersonAlias).count()}")
        print(f"places:        {session.query(m.Place).count()}")
        print(f"  geocoded:    {session.query(m.Place).filter(m.Place.lat.isnot(None)).count()}")
        print(f"events vocab:  {session.query(m.Event).count()}")
        print(f"photo_person:  {n_people_tags} manifest tags "
              f"({session.query(m.Photo).join(m.PhotoPerson).distinct().count()} photos)")
        print(f"photo_event:   {n_event_tags} tags "
              f"({session.query(m.Photo).join(m.PhotoEvent).distinct().count()} photos)")
        print(f"date misses:   {len(date_misses)} (non-empty unparsed)")
        print(f"people unresolved tokens: {len(unresolved)} distinct "
              f"({sum(unresolved.values())} instances) -> review/people_unresolved.csv")
        print(f"place  unresolved strings: {len(place_misses)} distinct "
              f"-> review/places_unresolved.csv")
    finally:
        session.close()


if __name__ == "__main__":
    run()
