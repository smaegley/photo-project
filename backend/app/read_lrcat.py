"""Read people + event keywords for the scanned photos straight from the
Lightroom catalog (SPEC §11.6 sidecar path) — bypassing file-export metadata.

**Why this exists:** Lightroom's per-keyword `includeOnExport` flag silently drops
tags from exported JPEGs when it's unchecked (discovered 2026-07-24: the `Abby`
and `Toby` pet keywords had `includeOnExport=0`, so *no* export — not even
"Save Metadata to Files" — ever wrote them, while `Floyd`/`Muffy` with the flag on
exported fine). The exported files are therefore an unreliable source; the catalog
is authoritative. This reads it **read-only / immutable** and emits:

  - `data/review/lr_people.csv`  — per-photo sidecar: `base_name, people, events`
    (pipe-joined), consumed by `import_photos --people-csv`. People = keywords with
    `keywordType='person'`; events = ordinary keywords (hierarchy parents dropped).
  - `data/review/people_seed.csv` — the complete distinct-people cast for seeding,
    with a `resolves` flag against the current DB and confident `is_family`
    pre-fills (superseding the file-based `extract_people` for the scan set).

Run with Lightroom **closed** (so the catalog isn't locked):
    python -m app.read_lrcat --lrcat "/mnt/photos/lrcat-drop/Lightroom Database-v13-3.lrcat"
"""
import argparse
import csv
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from app import metadata
from app.config import settings
from app.database import SessionLocal
from app.import_photos import REVIEW_DIR, build_people_index

DEFAULT_LRCAT = "/mnt/photos/lrcat-drop/Lightroom Database-v13-3.lrcat"
FASTFOTO_GLOB = "FastFoto/%"           # catalog folder holding the scan exports
# Keyword-hierarchy parents / batch labels that are never a real event term.
KW_NOISE = {"events", "places", "people", "holiday", "slides", "scans", "scanned"}

# Confident pre-fills for the seed (Steve corrects the rest). Kept in sync with the
# import_photos §12.7 CSV contract: is_family = Y | N | Pet; a person_id in
# resolved_person_id on a 'no' row means "alias to that existing person".
FAMILY = {"Kate Maegley", "Leslie Maegley"}
PET = {"Muffy", "Floyd", "Abby", "Toby"}
ALIAS = {"AJ Mobius": "aj_moebius", "Mary Catherine Mobius": "mary_catherine"}

# --- digital (origin=digital, B2-backed) selection (SPEC §13.14) ---
# A people gallery: include a PhotoAlbum photo iff tagged with >=1 person from this
# set. Editable — extends as Steve tags more family/friends he wants in the gallery.
DIGITAL_INCLUDE_PEOPLE = {
    "Steve Maegley", "Cori Johnson", "Kate Maegley", "Ryan Maegley",
    "Marilyn Maegley", "Wendel Maegley",
}
PHOTOALBUM_MARKER = "PhotoAlbum/"   # on-disk folder name; B2 renames it "Photo Album/"
B2_ALBUM_PREFIX = "Photo Album/"    # confirmed 2026-07-26 (P-D1)


def read_catalog(lrcat: Path):
    """base_name -> (people:set, events:set), scoped to the FastFoto folder."""
    con = sqlite3.connect(f"file:{lrcat}?mode=ro&immutable=1", uri=True)
    q = """
    SELECT f.baseName, k.name, k.keywordType
    FROM AgLibraryFile f
    JOIN AgLibraryFolder fo ON fo.id_local = f.folder AND fo.pathFromRoot LIKE ?
    JOIN Adobe_images i          ON i.rootFile = f.id_local
    JOIN AgLibraryKeywordImage ki ON ki.image = i.id_local
    JOIN AgLibraryKeyword k      ON k.id_local = ki.tag
    """
    per_photo: dict[str, tuple[set, set]] = defaultdict(lambda: (set(), set()))
    for base, name, ktype in con.execute(q, (FASTFOTO_GLOB,)):
        ppl, evt = per_photo[base]
        if ktype == "person":
            ppl.add(name)
        elif name.lower() not in KW_NOISE:
            evt.add(name)
    con.close()
    return per_photo


def run(lrcat: Path) -> None:
    if not lrcat.exists():
        print(f"catalog not found: {lrcat}")
        return
    per_photo = read_catalog(lrcat)

    # --- sidecar: base_name, people, events (pipe-joined) -------------------------
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    sidecar = REVIEW_DIR / "lr_people.csv"
    with sidecar.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["base_name", "people", "events"])
        for base in sorted(per_photo):
            ppl, evt = per_photo[base]
            w.writerow([base, "|".join(sorted(ppl)), "|".join(sorted(evt))])

    # --- people-seed: distinct cast, resolves-flag, confident pre-fills -----------
    counts: Counter = Counter()
    sample: dict[str, str] = {}
    for base, (ppl, _evt) in per_photo.items():
        for name in ppl:
            counts[name] += 1
            sample.setdefault(name, base)

    db = SessionLocal()
    try:
        idx = build_people_index(db)
    finally:
        db.close()

    def resolves(name):
        pids = idx.get(metadata.norm_text(name))
        return sorted(pids)[0] if pids else None

    seed = REVIEW_DIR / "people_seed.csv"
    rows = []
    for name in sorted(counts, key=lambda n: (resolves(n) is not None, -counts[n], n)):
        pid = resolves(name)
        if pid:
            fam, alias = "", pid
        elif name in ALIAS:
            fam, alias = "", ALIAS[name]
        elif name in FAMILY:
            fam, alias = "Y", ""
        elif name in PET:
            fam, alias = "Pet", ""
        else:
            fam, alias = "N", ""
        note = "alias, not new" if (not pid and name in ALIAS) else ""
        rows.append([name, counts[name], "yes" if pid else "no", alias, fam,
                     f"photos/{sample[name].split('_')[0]}/{sample[name]}.JPG", note])
    with seed.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["lr_name", "photo_count", "resolves", "resolved_person_id",
                    "is_family", "sample_file", "notes"])
        w.writerows(rows)

    events = Counter()
    for _b, (_p, evt) in per_photo.items():
        events.update(evt)
    n_new = sum(1 for r in rows if r[2] == "no")
    print(f"catalog: {len(per_photo)} FastFoto photos tagged")
    print(f"people : {len(counts)} distinct ({n_new} new / {len(counts)-n_new} resolve)")
    print(f"events : {dict(events.most_common())}")
    print(f"wrote  : {sidecar}")
    print(f"         {seed}")
    print("Next: review people_seed.csv → seed_people → import_photos --people-csv "
          f"{sidecar.name}")


def b2_key_for(full_path: str) -> str | None:
    """On-disk catalog path -> B2 object key, or None if not under a PhotoAlbum tree.
    Anchors on the last 'PhotoAlbum/' so it's **mount-independent** (survives Steve's
    LR re-links), and swaps the extension to .jpg — we serve the JPG of a RAW/JPG pair
    (§13.6); the importer HEAD-verifies the key and routes misses to review."""
    i = full_path.rfind(PHOTOALBUM_MARKER)
    if i < 0:
        return None
    rel = full_path[i + len(PHOTOALBUM_MARKER):]           # e.g. 2015/…/file.cr2
    if "." in rel.rsplit("/", 1)[-1]:                       # strip extension if present
        rel = rel[:rel.rfind(".")]
    return B2_ALBUM_PREFIX + rel + ".jpg"


def _write_seed(counts: Counter, sample: dict, out_path: Path) -> int:
    """Write a people_seed CSV (shared shape with the scan flow); returns new-count."""
    db = SessionLocal()
    try:
        idx = build_people_index(db)
    finally:
        db.close()

    def resolves(name):
        pids = idx.get(metadata.norm_text(name))
        return sorted(pids)[0] if pids else None

    rows = []
    for name in sorted(counts, key=lambda n: (resolves(n) is not None, -counts[n], n)):
        pid = resolves(name)
        if pid:
            fam, alias = "", pid
        elif name in ALIAS:
            fam, alias = "", ALIAS[name]
        elif name in FAMILY or name in DIGITAL_INCLUDE_PEOPLE:
            fam, alias = "Y", ""
        elif name in PET:
            fam, alias = "Pet", ""
        else:
            fam, alias = "N", ""
        note = "alias, not new" if (not pid and name in ALIAS) else ""
        rows.append([name, counts[name], "yes" if pid else "no", alias, fam,
                     sample[name], note])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["lr_name", "photo_count", "resolves", "resolved_person_id",
                    "is_family", "sample_file", "notes"])
        w.writerows(rows)
    return sum(1 for r in rows if r[2] == "no")


def run_digital(lrcat: Path) -> None:
    """Emit the digital sidecar (B2-backed origin=digital, SPEC §13.14): every PhotoAlbum
    photo tagged with >=1 DIGITAL_INCLUDE_PEOPLE person -> its B2 key (JPG of pair),
    people/events, capture date, GPS. RAW+JPG pairs collapse to one row by key."""
    con = sqlite3.connect(f"file:{lrcat}?mode=ro&immutable=1", uri=True)
    inc = "','".join(sorted(DIGITAL_INCLUDE_PEOPLE))
    sel = (  # image ids in a PhotoAlbum tree tagged with >=1 include-person
        "SELECT i.id_local FROM Adobe_images i "
        "JOIN AgLibraryFile f ON f.id_local=i.rootFile "
        "JOIN AgLibraryFolder fo ON fo.id_local=f.folder "
        "JOIN AgLibraryRootFolder rf ON rf.id_local=fo.rootFolder "
        "WHERE (rf.absolutePath || fo.pathFromRoot) LIKE '%PhotoAlbum/%' "
        "AND i.id_local IN (SELECT ki.image FROM AgLibraryKeywordImage ki "
        f"JOIN AgLibraryKeyword k ON k.id_local=ki.tag WHERE k.name IN ('{inc}'))")
    detail = con.execute(
        "SELECT i.id_local, rf.absolutePath||fo.pathFromRoot||f.baseName||'.'||f.extension, "
        "  lower(f.extension), i.captureTime, e.gpsLatitude, e.gpsLongitude "
        "FROM Adobe_images i JOIN AgLibraryFile f ON f.id_local=i.rootFile "
        "JOIN AgLibraryFolder fo ON fo.id_local=f.folder "
        "JOIN AgLibraryRootFolder rf ON rf.id_local=fo.rootFolder "
        "LEFT JOIN AgHarvestedExifMetadata e ON e.image=i.id_local "
        f"WHERE i.id_local IN ({sel})").fetchall()
    kwrows = con.execute(
        "SELECT ki.image, k.name, k.keywordType FROM AgLibraryKeywordImage ki "
        f"JOIN AgLibraryKeyword k ON k.id_local=ki.tag WHERE ki.image IN ({sel})").fetchall()
    con.close()

    img_people, img_events = defaultdict(set), defaultdict(set)
    for img, name, ktype in kwrows:
        if ktype == "person":
            img_people[img].add(name)
        elif name.lower() not in KW_NOISE:
            img_events[img].add(name)

    grouped: dict[str, dict] = {}   # b2_key -> merged people/events/date/gps/exts
    for img, fullpath, ext, capture, glat, glon in detail:
        key = b2_key_for(fullpath)
        if not key:
            continue
        g = grouped.setdefault(key, {"people": set(), "events": set(),
                                     "date": None, "gps": "", "exts": set()})
        g["people"] |= img_people.get(img, set())
        g["events"] |= img_events.get(img, set())
        g["exts"].add(ext)
        if capture and (g["date"] is None or capture < g["date"]):
            g["date"] = capture          # earliest capture across the pair
        if glat is not None and glon is not None and not g["gps"]:
            g["gps"] = f"{glat},{glon}"

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    sidecar = REVIEW_DIR / "digital_sidecar.csv"
    with sidecar.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["b2_key", "people", "events", "capture_date", "gps", "orig_exts"])
        for key in sorted(grouped):
            g = grouped[key]
            w.writerow([key, "|".join(sorted(g["people"])), "|".join(sorted(g["events"])),
                        (g["date"] or "")[:19], g["gps"], "|".join(sorted(g["exts"]))])

    counts, sample = Counter(), {}
    for key, g in grouped.items():
        for name in g["people"]:
            counts[name] += 1
            sample.setdefault(name, key)
    n_new = _write_seed(counts, sample, REVIEW_DIR / "people_seed_digital.csv")

    n_jpgless = sum(1 for g in grouped.values() if not (g["exts"] & {"jpg", "jpeg"}))
    print(f"digital: {len(grouped)} photos selected "
          f"(rule: tagged with any of {sorted(DIGITAL_INCLUDE_PEOPLE)})")
    print(f"  people on them: {len(counts)} distinct ({n_new} new to seed)")
    print(f"  {n_jpgless} have no in-catalog JPG (RAW/HEIC/PSD) — importer HEAD-verifies "
          f"the derived .jpg key, misses → review")
    print(f"wrote  : {sidecar}")
    print(f"         {REVIEW_DIR / 'people_seed_digital.csv'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read people/events from the LR catalog (SPEC §11.6/§13.14)")
    ap.add_argument("--lrcat", type=Path, default=Path(DEFAULT_LRCAT))
    ap.add_argument("--digital", action="store_true",
                    help="emit the B2-backed origin=digital sidecar (people-rule) instead of scans")
    args = ap.parse_args()
    (run_digital if args.digital else run)(args.lrcat)
