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


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Read scan people/events from the LR catalog (SPEC §11.6)")
    ap.add_argument("--lrcat", type=Path, default=Path(DEFAULT_LRCAT))
    args = ap.parse_args()
    run(args.lrcat)
