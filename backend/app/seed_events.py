"""Seed event-vocabulary terms from a reviewed keywords CSV (SPEC §3.6, §13.14).

The importers never auto-create vocabulary — an unmatched Lightroom keyword is
*reported*, not silently promoted to an event, so the facet list stays curated. This
is the other half of that loop: it takes the reviewed report and creates the terms you
marked `add`.

    python -m app.seed_events [--csv events_seed_digital.csv] [--dry-run]
    then re-run the importer to attach the tags:
    python -m app.import_digital

CSV contract (the importer's `unmatched_keywords_*.csv` with the `action` column filled
in — copy it to a `*_seed_*.csv` name first, since the report is regenerated on every
import run and would overwrite your decisions):
  - `action == add`     -> create the event if it doesn't already exist.
  - `action == ignore`  -> recorded as a skip; nothing written. Re-reporting these on
        the next import is expected — the CSV is the durable record of the decision.
  - anything else       -> REFUSED (reported, nothing written), so no term is created
        from an un-reviewed row.

Idempotent and non-destructive: it only ever INSERTs missing events, never renames,
merges or deletes. Matching is case-insensitive on the event name, mirroring how
`import_photos.build_event_index` resolves keywords.
"""
import argparse
import csv
from pathlib import Path

from app import models as m
from app.database import SessionLocal
from app.import_photos import REVIEW_DIR


def run(csv_path: Path, dry_run: bool = False) -> None:
    if not csv_path.is_absolute():
        csv_path = REVIEW_DIR / csv_path.name
    if not csv_path.exists():
        print(f"seed CSV not found: {csv_path}")
        return

    db = SessionLocal()
    try:
        existing = {name.lower(): eid for eid, name in db.query(m.Event.id, m.Event.name).all()}
        created = skipped = ignored = refused = 0
        refusals, added = [], []

        with open(csv_path, newline="") as fh:
            for r in csv.DictReader(fh):
                name = (r.get("keyword") or "").strip()
                action = (r.get("action") or "").strip().lower()
                if not name:
                    continue
                if action == "ignore":
                    ignored += 1
                    continue
                if action != "add":
                    refusals.append((name, f"action {r.get('action')!r} is not add/ignore"))
                    refused += 1
                    continue
                if name.lower() in existing:
                    skipped += 1
                    continue
                if not dry_run:
                    db.add(m.Event(name=name))
                existing[name.lower()] = -1  # reserve within this run
                added.append(name)
                created += 1

        if not dry_run:
            db.commit()
    finally:
        db.close()

    tag = " (DRY RUN)" if dry_run else ""
    print(f"=== SEED EVENTS{tag} ===")
    print(f"created : {created}")
    print(f"skipped : {skipped} (already in the vocabulary)")
    print(f"ignored : {ignored} (marked ignore — stays out of the vocabulary)")
    print(f"refused : {refused}")
    for name, why in refusals:
        print(f"   ! {name}: {why}")
    if added:
        print("\ncreated events: " + ", ".join(sorted(added)))
    if created and not dry_run:
        print("\nNext: re-run the importer to attach these to photos:")
        print("  python -m app.import_digital")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed event vocabulary from a reviewed CSV")
    ap.add_argument("--csv", type=Path, default=Path("events_seed_digital.csv"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    run(args.csv, dry_run=args.dry_run)
