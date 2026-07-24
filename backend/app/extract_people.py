"""People-seed extractor for the scanned/digital import (SPEC §12.6/§12.7).

Walks the exported photo library (`<library_root>/photos/**`) and lists every
distinct person tagged in Lightroom (PersonInImage + `mwg-rs` face regions),
with a photo count and a sample file, so the whole cast can be reviewed and
seeded **before** the real import — rather than discovering them one at a time
in `import_photos`' `unresolved_people.csv`.

For each name it flags whether that name **already resolves** against the current
DB's people vocabulary (canonical names + aliases — the exact index
`import_photos` uses), so names that already exist (slide-era family) are marked
`resolves=yes` and skipped by the downstream seeding step; genuinely new people
(mostly non-family scan-era friends, §12.7) are marked `no` and get a blank
`is_family` column for Steve to fill.

This is read-only — it touches no rows and imports nothing. Run on dev against a
staged export:
    python -m app.extract_people                 # from backend/
    python -m app.extract_people --photos-dir /some/other/tree
Output: data/review/people_seed.csv (+ a console summary).

Note: `resolves` is evaluated against whatever DB this runs on. On dev that's the
slide-era canon, which mirrors prod for the family names — a good approximation.
The downstream seeding step is idempotent, so a name mis-flagged `no` that in
fact exists is never duplicated.
"""
import argparse
import csv
from collections import defaultdict
from pathlib import Path

from app import metadata
from app.config import settings
from app.database import SessionLocal
from app.import_photos import IMAGE_EXTS, REVIEW_DIR, build_people_index


def _names(data: dict) -> set[str]:
    """Every person name on a photo: PersonInImage plus face-region names."""
    names = set(data.get("people") or ())
    names |= {n for (n, *_rest) in (data.get("face_regions") or ())}
    return {n.strip() for n in names if n and n.strip()}


def run(photos_dir: Path | None = None, out_path: Path | None = None) -> None:
    root = settings.library_root_path
    photos_dir = photos_dir or (root / "photos")
    if not photos_dir.exists():
        print(f"no photos dir at {photos_dir} — nothing to extract")
        return

    db = SessionLocal()
    try:
        people_idx = build_people_index(db)  # norm(name) -> {person_id}
    finally:
        db.close()

    # Aggregate per raw LR name.
    count: dict[str, int] = defaultdict(int)
    sample: dict[str, str] = {}
    norm_collisions: dict[str, set[str]] = defaultdict(set)  # norm -> {raw names}
    n_files = 0

    for f in sorted(photos_dir.rglob("*")):
        if not f.is_file() or f.name.startswith("._"):
            continue
        if f.suffix.lower() not in IMAGE_EXTS or f.stem.endswith("_b"):
            continue  # skip non-images and back-of-photo scans
        n_files += 1
        rel = f.relative_to(root).as_posix()
        for name in _names(metadata.extract(str(f))):
            count[name] += 1
            sample.setdefault(name, rel)
            norm_collisions[metadata.norm_text(name)].add(name)

    def resolves(name: str) -> str | None:
        pids = people_idx.get(metadata.norm_text(name))
        return sorted(pids)[0] if pids else None

    # Build rows: unresolved (need action) first, each block by photo count desc.
    rows = []
    for name in sorted(count, key=lambda n: (resolves(n) is not None, -count[n], n)):
        pid = resolves(name)
        variants = norm_collisions[metadata.norm_text(name)] - {name}
        note = f"shares normalized name with: {', '.join(sorted(variants))}" if variants else ""
        rows.append([
            name,
            count[name],
            "yes" if pid else "no",
            pid or "",
            "",  # is_family — Steve fills Y/N for the 'no' rows (blank = not needed)
            sample[name],
            note,
        ])

    out_path = out_path or (REVIEW_DIR / "people_seed.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["lr_name", "photo_count", "resolves", "resolved_person_id",
                    "is_family", "sample_file", "notes"])
        w.writerows(rows)

    n_new = sum(1 for r in rows if r[2] == "no")
    n_collide = sum(1 for v in norm_collisions.values() if len(v) > 1)
    print(f"scanned {n_files} photos → {len(count)} distinct people "
          f"({n_new} new / {len(count) - n_new} already resolve)")
    if n_collide:
        print(f"⚠ {n_collide} normalized-name collision(s) — likely alias variants; "
              f"see the 'notes' column and merge before seeding.")
    print(f"wrote {out_path}")
    print("Next: fill is_family (Y/N) on the 'no' rows, then the seeding step "
          "creates those person + alias rows.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Extract distinct LR people for seeding (SPEC §12.7)")
    ap.add_argument("--photos-dir", type=Path, default=None,
                    help="override the export tree (default: <library_root>/photos)")
    ap.add_argument("--out", type=Path, default=None,
                    help="output CSV path (default: data/review/people_seed.csv)")
    args = ap.parse_args()
    run(photos_dir=args.photos_dir, out_path=args.out)
