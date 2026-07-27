"""Import Lightroom face regions for digital photos (SPEC §14, probe P-F1).

`import_digital` reads *image-level* keyword links — who is in a photo, but not where.
Lightroom also stores per-face geometry, and there is far more of it than the archive has
ever held: **49,459 named regions** in the PhotoAlbum tree against 1,784 in the DB,
covering the 2010s/2020s where our enrollment was completely empty. That geometry is what
a face matcher trains on (§14.3), so this brings it in.

    python -m app.read_lrcat --faces          # -> review/faces_digital.csv
    # review people_seed_faces.csv, then:
    python -m app.seed_people --csv people_seed_faces.csv
    python -m app.import_faces [--dry-run]

Two behaviours worth knowing:

- **It adds people tags, not just geometry** (Steve's call, 2026-07-27). Face-level data
  is richer than the image-level keyword links, so a face can name someone the image
  keywords missed. Names that resolve to no person are *reported, never auto-created* —
  the §12.6 rule — so the way to exclude someone is to mark them `ignore` in
  `people_seed_faces.csv` and they simply stop appearing in the report.
- **One region per (photo, person).** `photo_person` is keyed that way, so when the same
  person is detected more than once in a frame the **largest** box wins — it is the most
  likely to be the real, usable face rather than a background mis-detection.

Non-destructive and idempotent: tags are union-added, an existing region is never
overwritten by a smaller one, and nothing is ever deleted.
"""
import argparse
import csv
from collections import defaultdict
from pathlib import Path

from app import metadata, models as m
from app.database import SessionLocal
from app.import_photos import REVIEW_DIR, build_people_index

DEFAULT_FACES = "faces_digital.csv"


def _stem(key: str) -> str:
    """Extension-less, case-folded key — the join between the catalog's proposed `.jpg`
    and whatever casing/extension the importer actually resolved against B2 (`.JPG`,
    `.HEIC`, …). Matching on the stem sidesteps that entirely."""
    return key.rsplit(".", 1)[0].lower()


def run(faces_csv: Path, dry_run: bool = False) -> None:
    path = faces_csv if faces_csv.is_absolute() else REVIEW_DIR / faces_csv.name
    if not path.exists():
        print(f"faces CSV not found: {path}  (run: python -m app.read_lrcat --faces)")
        return

    db = SessionLocal()
    try:
        people_idx = build_people_index(db)
        valid = {r[0] for r in db.query(m.Person.id).all()}
        # stem -> photo.id, for every B2-backed row we actually imported
        photos = {_stem(sf): pid for pid, sf in
                  db.query(m.Photo.id, m.Photo.source_file)
                  .filter(m.Photo.storage_backend == "b2").all()}
        existing = {(pp.photo_id, pp.person_id): pp
                    for pp in db.query(m.PhotoPerson).all()}

        # Collapse to one box per (photo, person): the largest wins.
        best: dict[tuple[int, str], tuple[float, tuple]] = {}
        unresolved: dict[str, str] = {}
        no_photo: set[str] = set()
        n_rows = 0

        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                n_rows += 1
                pid = photos.get(_stem(r["b2_key"]))
                if pid is None:
                    no_photo.add(r["b2_key"])
                    continue
                names = {p for p in people_idx.get(metadata.norm_text(r["person"]), set())
                         if p in valid}
                if not names:
                    unresolved.setdefault(r["person"], r["b2_key"])
                    continue
                try:
                    box = (float(r["cx"]), float(r["cy"]), float(r["w"]), float(r["h"]))
                except ValueError:
                    continue
                area = box[2] * box[3]
                for person_id in names:
                    k = (pid, person_id)
                    if k not in best or area > best[k][0]:
                        best[k] = (area, box)

        n_tags = n_regions = n_kept = 0
        for (pid, person_id), (_area, box) in best.items():
            pp = existing.get((pid, person_id))
            if pp is None:
                if not dry_run:
                    db.add(m.PhotoPerson(photo_id=pid, person_id=person_id,
                                         source=m.SOURCE_HUMAN, uncertain=False,
                                         region_x=box[0], region_y=box[1],
                                         region_w=box[2], region_h=box[3]))
                n_tags += 1
            elif pp.region_w is None:
                if not dry_run:
                    pp.region_x, pp.region_y, pp.region_w, pp.region_h = box
                n_regions += 1
            else:
                n_kept += 1          # already has geometry — never overwrite

        if not dry_run:
            db.commit()
    finally:
        db.close()

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    with open(REVIEW_DIR / "unresolved_people_faces.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["lr_name", "sample_key", "action"])
        w.writerows([[n, s, "add via people_seed_faces.csv (or mark ignore), then re-run"]
                     for n, s in sorted(unresolved.items())])

    tag = " (DRY RUN)" if dry_run else ""
    print(f"=== IMPORT FACES{tag} ===")
    print(f"face rows read:      {n_rows}")
    print(f"new people tags:     {n_tags}  (with a face box)")
    print(f"regions backfilled:  {n_regions}  (tag existed, geometry didn't)")
    print(f"regions kept as-is:  {n_kept}")
    print(f"faces w/o a photo:   {len(no_photo)} (catalog images not in the digital set)")
    print(f"unresolved people:   {len(unresolved)} -> review/unresolved_people_faces.csv")
    if not dry_run and (n_tags or n_regions):
        print("\nEnrollment for face matching (SPEC §14) is now materially larger.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Import LR face regions for digital photos (SPEC §14)")
    ap.add_argument("--faces-csv", type=Path, default=Path(DEFAULT_FACES))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(args.faces_csv, dry_run=args.dry_run)
