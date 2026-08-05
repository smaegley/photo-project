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
import re
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
        # Ship the people too — the WHOLE person, not just existence. Review edits
        # renames, family links, is_family flips and representative photos on dev
        # (23 renames + 16 link edits + 2 merges in the §14 pass alone), and an export
        # that only created missing people silently stranded all of that on dev.
        # representative_photo_id travels as the photo's source_file (row ids differ
        # per database, §14.8a).
        people = [{"id": p.id, "name": p.canonical_name, "is_family": bool(p.is_family),
                   "notes": p.notes, "father_id": p.father_id, "mother_id": p.mother_id,
                   "spouse_id": p.spouse_id,
                   "representative": photos.get(p.representative_photo_id)}
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
        # Places travel too, but ADDITIVE-ONLY on import: both sides name places
        # independently (Steve named 26 on prod while dev had 14 of its own), so
        # unlike people there is no "dev wins" — a prod place or assignment is never
        # overwritten. Keyed by name (place ids are allocated per database).
        places = [{"name": pl.canonical_name, "region": pl.region,
                   "precision": pl.precision, "lat": pl.lat, "lon": pl.lon}
                  for pl in db.query(m.Place).all()]
        place_names = dict(db.query(m.Place.id, m.Place.canonical_name).all())
        photo_places = [[sf, place_names[plid]]
                        for sf, plid in db.query(m.Photo.source_file, m.Photo.place_id)
                        .filter(m.Photo.place_id.isnot(None)).all()]
        # Display-time rotation overrides for B2-backed photos (non-zero only; the
        # import zeroes anything it doesn't list, so a cleared override clears there
        # too). Regions were rotated together with the override on this side, so tags
        # and rotations in one export are always mutually consistent.
        rotations = [[sf, rot] for sf, rot in
                     db.query(m.Photo.source_file, m.Photo.rotation)
                     .filter(m.Photo.rotation != 0).all()]
    finally:
        db.close()

    payload = {"exported_at": datetime.now(timezone.utc).isoformat(),
               "photos": all_source_files,
               "people": people, "aliases": aliases, "tags": tags,
               "places": places, "photo_places": photo_places,
               "rotations": rotations}
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

        # 1. people. Dev is the review environment, so dev wins: create the missing,
        # and update name/is_family/notes/links/representative on those already here.
        # Links land in a second pass so a link to a person created this run resolves.
        exported_ids = {p["id"] for p in payload["people"]}
        new_people = [p for p in payload["people"] if p["id"] not in have_people]
        for p in new_people:
            if not dry_run:
                db.add(m.Person(id=p["id"], canonical_name=p["name"],
                                is_family=p["is_family"], notes=p.get("notes")))
            have_people.add(p["id"])
        if not dry_run and new_people:
            db.flush()
        n_updated = 0
        created_ids = {np["id"] for np in new_people}
        by_id = {p.id: p for p in db.query(m.Person).all()}
        for p in payload["people"]:
            cur = by_id.get(p["id"])
            rep_id = photo_by_src.get(p["representative"]) if p.get("representative") else None
            wanted_fields = {"canonical_name": p["name"], "is_family": p["is_family"],
                             "notes": p.get("notes"),
                             "father_id": p.get("father_id") if p.get("father_id") in have_people else None,
                             "mother_id": p.get("mother_id") if p.get("mother_id") in have_people else None,
                             "spouse_id": p.get("spouse_id") if p.get("spouse_id") in have_people else None,
                             "representative_photo_id": rep_id}
            if cur is None:          # dry run: person doesn't exist yet, nothing to diff
                continue
            if p["id"] in created_ids:   # created this run — links/rep still need setting
                for k, v in wanted_fields.items():
                    setattr(cur, k, v)
                continue
            changed = any(getattr(cur, k) != v for k, v in wanted_fields.items())
            if changed and not dry_run:
                for k, v in wanted_fields.items():
                    setattr(cur, k, v)
            n_updated += changed
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

        # 1b. places — ADDITIVE ONLY, prod wins. Create places this side has never
        # heard of (matched by name, case-insensitive) and fill the place on photos
        # that have none; never move a photo that is already placed, never touch an
        # existing place's coords or name. Both sides curate places independently.
        place_by_name = {pl.canonical_name.lower(): pl for pl in db.query(m.Place).all()}
        used_nums = [int(g.group(1)) for pl in place_by_name.values()
                     if (g := re.fullmatch(r"plc(\d+)", pl.id))]
        next_num = (max(used_nums) + 1) if used_nums else 1
        n_places = 0
        for p in payload.get("places", []):
            if p["name"].lower() in place_by_name:
                continue
            n_places += 1
            pl = m.Place(id=f"plc{next_num:03d}", canonical_name=p["name"],
                         region=p.get("region"), precision=p.get("precision") or "unknown",
                         lat=p.get("lat"), lon=p.get("lon"))
            next_num += 1
            place_by_name[p["name"].lower()] = pl
            if not dry_run:
                db.add(pl)
        if not dry_run and n_places:
            db.flush()
        photo_place = dict(db.query(m.Photo.id, m.Photo.place_id).all())
        n_placed = 0
        for sf, place_name in payload.get("photo_places", []):
            pid = photo_by_src.get(sf)
            pl = place_by_name.get(place_name.lower())
            if pid is None or pl is None or photo_place.get(pid) is not None:
                continue
            n_placed += 1
            photo_place[pid] = pl.id
            if not dry_run:
                db.get(m.Photo, pid).place_id = pl.id

        # 1c. rotation overrides — dev wins, including zeroing overrides dev cleared.
        # ⚠ Every change here re-keys that photo's derivative cache: run prewarm after
        # importing, or the affected photos 404 their thumbnails until someone does.
        n_rot = 0
        if "rotations" in payload:
            rot_map = {sf: rot for sf, rot in payload["rotations"]}
            src_by_photo = {v: k for k, v in photo_by_src.items()}
            current = {src_by_photo[ph.id]: ph for ph in
                       db.query(m.Photo).filter(m.Photo.rotation != 0).all()
                       if ph.id in src_by_photo}
            for sf in set(rot_map) | set(current):
                pid = photo_by_src.get(sf)
                if pid is None:
                    continue
                want = rot_map.get(sf, 0)
                ph = db.get(m.Photo, pid)
                if (ph.rotation or 0) != want:
                    n_rot += 1
                    if not dry_run:
                        ph.rotation = want

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
                continue
            # Dev's box wins outright — review moves and deletes boxes, not just adds
            # them (FaceTagEditor edits, the square-crop fix), so "fill only when
            # empty" would freeze every box prod already had.
            have_box = ([round(pp.region_x, 5), round(pp.region_y, 5),
                         round(pp.region_w, 5), round(pp.region_h, 5)]
                        if pp.region_w is not None else None)
            if have_box != box:
                if not dry_run:
                    pp.region_x, pp.region_y, pp.region_w, pp.region_h = \
                        box if box else (None, None, None, None)
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

        # 4. people dev no longer has — merged-away duplicates (drew_leyman) or deleted
        # typos. Only under --prune, and only when nothing points at them anymore: any
        # remaining tag, kin link, user link or alias-of-others reference means this DB
        # knows something the export doesn't, so leave the row and say so.
        stale_tag_keys = {k for k, _pp in stale} if prune else set()
        people_pruned, people_kept = [], []
        if prune:
            for pid in sorted(set(by_id) - exported_ids):
                tags_left = [k for k, _pp in existing.items()
                             if k[1] == pid and k not in stale_tag_keys]
                kin = db.query(m.Person).filter(
                    (m.Person.father_id == pid) | (m.Person.mother_id == pid)
                    | (m.Person.spouse_id == pid)).count()
                users = db.query(m.User).filter(m.User.person_id == pid).count()
                if tags_left or kin or users:
                    people_kept.append((pid, f"{len(tags_left)} tags, {kin} kin, {users} users"))
                    continue
                people_pruned.append(pid)
                if not dry_run:
                    db.query(m.PersonAlias).filter(m.PersonAlias.person_id == pid).delete(
                        synchronize_session=False)
                    db.query(m.FaceSuggestion).filter(m.FaceSuggestion.person_id == pid).delete(
                        synchronize_session=False)
                    db.query(m.FaceCluster).filter(m.FaceCluster.person_id == pid).update(
                        {"person_id": None}, synchronize_session=False)
                    p = db.get(m.Person, pid)
                    if p is not None:
                        db.delete(p)

        if not dry_run:
            db.commit()
    finally:
        db.close()

    tag = " (DRY RUN)" if dry_run else ""
    print(f"\n=== IMPORT TAGS{tag} ===")
    print(f"people created:     {len(new_people)}")
    print(f"people updated:     {n_updated} (rename / links / family flag / rep photo)")
    print(f"aliases added:      {n_alias}")
    print(f"places created:     {n_places}")
    print(f"photos placed:      {n_placed} (only photos that had no place here)")
    print(f"rotations synced:   {n_rot} (run prewarm after import if nonzero)")
    print(f"tags added:         {added}")
    print(f"regions synced:     {boxed}")
    print(f"already correct:    {unchanged}")
    print(f"photos not here:    {len(missing_photos)} (export mentions photos this DB lacks)")
    verb = "pruned" if (prune and not dry_run) else "WOULD prune (pass --prune)"
    print(f"tags dev removed:   {len(stale)} {verb}")
    if stale and not prune:
        for (pid, per), _pp in stale[:8]:
            print(f"    photo {pid} · {per}")
    if prune:
        print(f"people dev removed: {len(people_pruned)} {verb}"
              + (f" ({', '.join(people_pruned)})" if people_pruned else ""))
        for pid, why in people_kept:
            print(f"    KEPT {pid} — still referenced here: {why}")
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
