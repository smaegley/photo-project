"""Move confirmed people-tags from dev to prod (SPEC §14.8a).

Face enrichment runs on dev and prod receives the *result* — not the model, not the
embeddings, not the review machinery. This is that hand-off, and it is the only path by
which hours of review reach the live site.

    # on dev
    python -m app.tags_io --export                 # -> data/review/tags_export.json.gz
    # ship it out-of-band (data/ is gitignored), then on prod:
    python -m app.tags_io --import --dry-run
    python -m app.tags_io --import                 # additive
    python -m app.tags_io --import --prune         # also remove what dev removed

**Keyed on `photo.source_file` and `person.id`, never row ids** (§14.8a): ids are
assigned per database and an id-keyed export would silently attach tags to the wrong
photos and people.

**Why `--prune` exists.** Reviewing is as much about *removing* wrong tags as adding
right ones — a mis-tagged face deleted on dev has to disappear on prod too, or the
cleanup never lands where the family actually looks. Prune is gated and dry-run-first
because a truncated export could otherwise strip a photo bare.

Scope is deliberately limited to photos **present in the export**. Prod photos the export
says nothing about are left completely alone, so this can never damage material dev has
never seen.
"""
import argparse
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

from app import models as m
from app.database import SessionLocal
from app.import_photos import REVIEW_DIR

DEFAULT_PATH = "tags_export.json.gz"


def export_tags(path: Path) -> None:
    db = SessionLocal()
    try:
        photos = dict(db.query(m.Photo.id, m.Photo.source_file).all())
        # Every photo dev knows about, not just those that still have tags. If a photo's
        # tags were ALL removed during review, listing only tagged photos would drop it
        # from prune's scope and prod would keep the stale tags forever — silently, since
        # nothing would report a difference. Cost 20 tags in testing.
        all_source_files = sorted(photos.values())
        rows = db.query(m.PhotoPerson).all()
        # Ship the people too: Steve creates them during review (Veronica, Katie
        # Kissinger…), and a tag referencing a person prod has never heard of would
        # otherwise be unresolvable. These are human decisions, not auto-created.
        people = [{"id": p.id, "name": p.canonical_name, "is_family": bool(p.is_family),
                   "notes": p.notes}
                  for p in db.query(m.Person).all()]
        aliases = [{"person_id": a.person_id, "alias": a.alias}
                   for a in db.query(m.PersonAlias).all()]
        tags = []
        for pp in rows:
            sf = photos.get(pp.photo_id)
            if not sf:
                continue
            tags.append({
                "source_file": sf, "person_id": pp.person_id,
                "source": pp.source, "uncertain": bool(pp.uncertain),
                "box": ([round(pp.region_x, 5), round(pp.region_y, 5),
                         round(pp.region_w, 5), round(pp.region_h, 5)]
                        if pp.region_w is not None else None),
            })
    finally:
        db.close()

    payload = {"exported_at": datetime.now(timezone.utc).isoformat(),
               "photos": all_source_files,
               "people": people, "aliases": aliases, "tags": tags}
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)
    mb = path.stat().st_size / 1048576
    tagged = len({t["source_file"] for t in tags})
    print(f"exported {len(tags)} tags on {tagged} photos "
          f"({len(all_source_files)} photos in scope), {len(people)} people "
          f"-> {path} ({mb:.2f} MB)")


def import_tags(path: Path, dry_run: bool = False, prune: bool = False) -> None:
    if not path.exists():
        print(f"export not found: {path}")
        return
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    print(f"loaded export from {payload.get('exported_at')}: "
          f"{len(payload['tags'])} tags, {len(payload['people'])} people")

    db = SessionLocal()
    try:
        photo_by_src = dict(db.query(m.Photo.source_file, m.Photo.id).all())
        have_people = {p.id for p in db.query(m.Person).all()}

        # 1. people this side has never seen — human-decided on dev, so apply them
        new_people = [p for p in payload["people"] if p["id"] not in have_people]
        for p in new_people:
            if not dry_run:
                db.add(m.Person(id=p["id"], canonical_name=p["name"],
                                is_family=p["is_family"], notes=p.get("notes")))
            have_people.add(p["id"])
        if not dry_run and new_people:
            db.flush()
        have_alias = {(a.person_id, a.alias.lower())
                      for a in db.query(m.PersonAlias).all()}
        n_alias = 0
        for a in payload.get("aliases", []):
            if a["person_id"] in have_people and \
                    (a["person_id"], a["alias"].lower()) not in have_alias:
                if not dry_run:
                    db.add(m.PersonAlias(person_id=a["person_id"], alias=a["alias"]))
                have_alias.add((a["person_id"], a["alias"].lower()))
                n_alias += 1

        # 2. tags
        existing = {(pp.photo_id, pp.person_id): pp for pp in db.query(m.PhotoPerson).all()}
        wanted: set[tuple[int, str]] = set()
        added = boxed = unchanged = 0
        missing_photos: set[str] = set()
        for t in payload["tags"]:
            pid = photo_by_src.get(t["source_file"])
            if pid is None:
                missing_photos.add(t["source_file"])
                continue
            if t["person_id"] not in have_people:
                continue
            key = (pid, t["person_id"])
            wanted.add(key)
            box = t.get("box")
            pp = existing.get(key)
            if pp is None:
                if not dry_run:
                    db.add(m.PhotoPerson(
                        photo_id=pid, person_id=t["person_id"],
                        source=t.get("source") or m.SOURCE_HUMAN,
                        uncertain=t.get("uncertain", False),
                        region_x=box[0] if box else None, region_y=box[1] if box else None,
                        region_w=box[2] if box else None, region_h=box[3] if box else None))
                added += 1
            elif box and pp.region_w is None:
                if not dry_run:
                    pp.region_x, pp.region_y, pp.region_w, pp.region_h = box
                boxed += 1
            else:
                unchanged += 1

        # 3. removals — only for photos the export actually covers
        covered = {photo_by_src[s] for s in payload["photos"] if s in photo_by_src}
        stale = [(k, pp) for k, pp in existing.items()
                 if k[0] in covered and k not in wanted]
        if prune and not dry_run:
            for _k, pp in stale:
                db.delete(pp)

        if not dry_run:
            db.commit()
    finally:
        db.close()

    tag = " (DRY RUN)" if dry_run else ""
    print(f"\n=== IMPORT TAGS{tag} ===")
    print(f"people created:     {len(new_people)}")
    print(f"aliases added:      {n_alias}")
    print(f"tags added:         {added}")
    print(f"regions filled in:  {boxed}")
    print(f"already correct:    {unchanged}")
    print(f"photos not here:    {len(missing_photos)} (export mentions photos this DB lacks)")
    verb = "pruned" if (prune and not dry_run) else "WOULD prune (pass --prune)"
    print(f"tags dev removed:   {len(stale)} {verb}")
    if stale and not prune:
        for (pid, per), _pp in stale[:8]:
            print(f"    photo {pid} · {per}")
    if new_people:
        print("\ncreated people: " + ", ".join(sorted(p["name"] for p in new_people)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Export/import people-tags dev->prod (SPEC §14.8a)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", action="store_true")
    g.add_argument("--import", dest="do_import", action="store_true")
    ap.add_argument("--path", type=Path, default=Path(DEFAULT_PATH))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", action="store_true",
                    help="also remove tags that dev no longer has (mis-IDs you deleted)")
    args = ap.parse_args()
    p = args.path if args.path.is_absolute() else REVIEW_DIR / args.path.name
    if args.export:
        export_tags(p)
    else:
        import_tags(p, dry_run=args.dry_run, prune=args.prune)
