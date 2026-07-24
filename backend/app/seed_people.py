"""Seed scan-era people into the DB from a reviewed `people_seed.csv` (SPEC §12.7).

Idempotent and non-destructive: creates `person` (+ a `person_alias`) rows for the
new scan people so `import_photos` can resolve them, and never modifies or deletes
anything that already exists. Safe to run repeatedly and safe on the live prod DB.

CSV contract (from `read_lrcat` / `extract_people`, reviewed by Steve):
  - `resolves == yes`                         -> already in the DB; skipped.
  - `resolves == no`, `resolved_person_id` set -> add `lr_name` as an ALIAS of that
        existing person (the Moebius-misspelling case), no new person.
  - `resolves == no`, `is_family` in {Y,N,Pet} -> CREATE a person + alias.
        Y = family; N = friend/non-family; Pet = non-family + `notes='pet'` (a
        durable marker for a future Pets grouping — behaves as non-family for now).
  - `resolves == no` with neither set          -> REFUSED (reported, nothing written)
        so no one is seeded half-specified.

    python -m app.seed_people [--csv data/review/people_seed.csv] [--dry-run]
"""
import argparse
import csv
import re
from pathlib import Path

from app import metadata, models as m
from app.config import settings
from app.database import SessionLocal
from app.import_photos import REVIEW_DIR, build_people_index


def slugify(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "person"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    return slug


def run(csv_path: Path, dry_run: bool = False) -> None:
    if not csv_path.is_absolute():
        csv_path = REVIEW_DIR / csv_path.name
    if not csv_path.exists():
        print(f"seed CSV not found: {csv_path}")
        return

    db = SessionLocal()
    try:
        idx = build_people_index(db)  # norm(name) -> {person_id}
        taken = {r[0] for r in db.query(m.Person.id).all()}
        existing_alias = {(a.person_id, metadata.norm_text(a.alias))
                          for a in db.query(m.PersonAlias).all()}

        n_created = n_aliased = n_updated = n_skipped = n_refused = 0
        refused = []
        with open(csv_path, newline="") as fh:
            for r in csv.DictReader(fh):
                name = (r["lr_name"] or "").strip()
                if not name:
                    continue
                norm = metadata.norm_text(name)
                target = (r.get("resolved_person_id") or "").strip()
                fam = (r.get("is_family") or "").strip().lower()

                # resolves=yes -> a pre-existing real person; never touch it.
                if (r.get("resolves") or "").strip().lower() == "yes":
                    n_skipped += 1
                    continue

                if target:  # ALIAS onto an existing person
                    if target not in taken:
                        refused.append((name, f"resolved_person_id '{target}' not found"))
                        n_refused += 1
                        continue
                    if (target, norm) in existing_alias:
                        n_skipped += 1
                        continue
                    if not dry_run:
                        db.add(m.PersonAlias(person_id=target, alias=name))
                    existing_alias.add((target, norm))
                    idx.setdefault(norm, set()).add(target)
                    n_aliased += 1
                    continue

                # CREATE (or, if seeded on a prior run, UPDATE) a person. Reaching
                # here means resolves=no at read time, so any current match is one
                # this seeder created — safe to sync is_family/notes to the CSV.
                if fam not in {"y", "n", "pet"}:
                    refused.append((name, f"is_family '{r.get('is_family')}' not Y/N/Pet"))
                    n_refused += 1
                    continue
                new_family, new_notes = (fam == "y"), ("pet" if fam == "pet" else None)
                if idx.get(norm):
                    pid = sorted(idx[norm])[0]
                    if not dry_run:
                        p = db.get(m.Person, pid)
                        if p and (p.is_family != new_family or p.notes != new_notes):
                            p.is_family, p.notes = new_family, new_notes
                    n_updated += 1
                    continue
                slug = slugify(name, taken)
                taken.add(slug)
                if not dry_run:
                    db.add(m.Person(id=slug, canonical_name=name,
                                    is_family=new_family, notes=new_notes))
                    db.add(m.PersonAlias(person_id=slug, alias=name))
                existing_alias.add((slug, norm))
                idx.setdefault(norm, set()).add(slug)
                n_created += 1

        if not dry_run:
            db.commit()
    finally:
        db.close()

    tag = " (DRY RUN)" if dry_run else ""
    print(f"=== SEED PEOPLE{tag} ===")
    print(f"created : {n_created}")
    print(f"updated : {n_updated} (is_family/notes synced)")
    print(f"aliased : {n_aliased}")
    print(f"skipped : {n_skipped} (pre-existing, untouched)")
    print(f"refused : {n_refused}")
    for name, why in refused:
        print(f"   ! {name}: {why}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed scan people from a reviewed CSV (SPEC §12.7)")
    ap.add_argument("--csv", type=Path, default=Path("people_seed.csv"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(args.csv, dry_run=args.dry_run)
