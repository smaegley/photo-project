"""Non-destructive Lightroom people overlay (SPEC §11.2 / §11.6).

Reads each slide's Lightroom face-tags (embedded XMP) and adds them to the DB as
`human-confirmed` photo_person tags, UNION-merged with the manifest tags already
loaded by import_data. Manifest wins on overlap (Wendel's cards are authoritative
for who he named); Lightroom adds anyone the manifest didn't.

Unlike import_data this does NOT wipe or rebuild anything — it only INSERTS
missing photo_person rows (and backfills width/height). So it is safe to run on
the live/prod DB and is idempotent: re-running adds nothing new. Lightroom names
that don't resolve against the people canon are written to a review report so they
can be curated (add to canon / add an alias / confirm out-of-scope) and the pass
re-run.

Run:  python -m importer.apply_lr_people             (apply)
      python -m importer.apply_lr_people --dry-run    (report only, no writes)
"""
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.database import SessionLocal  # noqa: E402
from app import models as m  # noqa: E402
from app.config import settings  # noqa: E402
from importer.metadata import (  # noqa: E402
    read_xmp, people_from_xmp, face_regions_from_xmp, image_size, norm_text, canon_alias_key,
)

PEOPLE_CANON = REPO_ROOT / "people_canon_firstpass.csv"
REVIEW_DIR = REPO_ROOT / "importer" / "review"


def build_alias_map() -> dict[str, set[str]]:
    """alias_key -> {person_id}, from the canon CSV (same rules as import_data)."""
    alias_map: dict[str, set[str]] = defaultdict(set)
    for r in csv.DictReader(open(PEOPLE_CANON)):
        if r.get("include", "Y").strip().upper() != "Y":
            continue
        pid = r["person_id"].strip()
        for k in [r["canonical_name"]] + r["aliases_seen"].split(";"):
            key = canon_alias_key(k)
            if key:
                alias_map[key].add(pid)
    return alias_map


def run(dry_run: bool = False) -> None:
    alias_map = build_alias_map()
    db = SessionLocal()
    try:
        existing = {(pp.photo_id, pp.person_id) for pp in db.query(m.PhotoPerson).all()}
        valid_pids = {r[0] for r in db.query(m.Person.id).all()}
        unresolved: Counter = Counter()
        unresolved_samples: dict[str, str] = {}
        added = scanned = no_file = sized = regions_set = 0

        def resolve(name):
            return {pid for pid in alias_map.get(norm_text(name), set()) if pid in valid_pids}

        for p in db.query(m.Photo).filter(m.Photo.origin == "slide").all():
            if not p.storage_path:
                continue
            path = settings.library_root_path / p.storage_path
            if not path.exists():
                no_file += 1
                continue
            scanned += 1
            if p.width is None or p.height is None:
                w, h = image_size(path)
                if w:
                    sized += 1
                    if not dry_run:
                        p.width, p.height = w, h
            xmp = read_xmp(path)
            if not xmp:
                continue
            for name in people_from_xmp(xmp):
                pids = resolve(name)
                if not pids:
                    unresolved[name] += 1
                    unresolved_samples.setdefault(name, p.source_file)
                    continue
                for pid in pids:
                    if (p.id, pid) in existing:
                        continue  # manifest already named them, or already applied
                    if not dry_run:
                        db.add(m.PhotoPerson(
                            photo_id=p.id, person_id=pid,
                            source=m.SOURCE_HUMAN, uncertain=False,
                        ))
                    existing.add((p.id, pid))
                    added += 1

            # Named face-region boxes -> store on photo_person for face thumbnails.
            for name, cx, cy, w, h in face_regions_from_xmp(xmp):
                for pid in resolve(name):
                    if dry_run:
                        regions_set += 1
                        continue
                    pp = db.get(m.PhotoPerson, (p.id, pid))
                    if pp is None:
                        pp = m.PhotoPerson(photo_id=p.id, person_id=pid, source=m.SOURCE_HUMAN)
                        db.add(pp)
                        existing.add((p.id, pid))
                    pp.region_x, pp.region_y, pp.region_w, pp.region_h = cx, cy, w, h
                    regions_set += 1

        if not dry_run:
            db.flush()

        # Auto-pick a representative face for each person who doesn't have one:
        # the largest named region wins (a manual ★ later overrides this).
        picked = 0
        if not dry_run:  # noqa: SIM102
            for person in db.query(m.Person).filter(m.Person.representative_photo_id.is_(None)).all():
                rows = (db.query(m.PhotoPerson)
                        .filter(m.PhotoPerson.person_id == person.id,
                                m.PhotoPerson.region_w.isnot(None)).all())
                if not rows:
                    continue
                best = max(rows, key=lambda r: (r.region_w or 0) * (r.region_h or 0))
                person.representative_photo_id = best.photo_id
                picked += 1
            db.commit()

        REVIEW_DIR.mkdir(parents=True, exist_ok=True)
        with open(REVIEW_DIR / "people_lr_unresolved.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["lr_name", "count", "sample_slide", "action"])
            for nm, c in unresolved.most_common():
                w.writerow([nm, c, unresolved_samples.get(nm, ""),
                            "add to canon / add alias / confirm out-of-scope"])

        print(f"=== APPLY LR PEOPLE {'(DRY RUN)' if dry_run else ''} ===")
        print(f"slides scanned:       {scanned}")
        print(f"missing files:        {no_file}")
        print(f"width/height set:     {sized}")
        print(f"LR tags {'to add' if dry_run else 'added'}: {added} (human-confirmed)")
        print(f"face regions set:     {regions_set}")
        print(f"representatives auto-picked: {picked}")
        print(f"unresolved LR names:  {len(unresolved)} distinct "
              f"({sum(unresolved.values())} instances) -> review/people_lr_unresolved.csv")
        for nm, c in unresolved.most_common():
            print(f"    · {nm} ({c})")
        print(f"photos with >=1 person now: "
              f"{db.query(m.Photo).join(m.PhotoPerson).distinct().count()}")
    finally:
        db.close()


if __name__ == "__main__":
    run(dry_run="--dry-run" in sys.argv)
