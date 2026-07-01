# Refresh DEV from PROD

Pull current production data down to the dev box so future feature work starts from
real content (people, edits, users), not a stale copy. **One direction only:
prod → dev.** Promoting the other way (dev → prod) is a separate, deliberate event —
see `DEPLOY.md`, never this.

| | DEV | PROD |
|---|---|---|
| Box | VM 201 `10.0.1.121` | LXC 209 `10.0.1.178` |
| Repo | `/home/aiuser/projects/photo-project` | `/opt/photo-project` |
| DB | `data/photos.db` | `/opt/photo-project/data/photos.db` |

**Connectivity:** LXC 209 → VM 201 works (used by the deploy); the reverse isn't set
up. So **prod pushes** the snapshot to dev.

## 1. Snapshot on prod
Production already writes a daily online-safe snapshot (`infra/db-snapshot.sh` via
`photo-backup.timer`) to `/opt/photo-project/snapshots/photos-<TS>.db.gz`. Use the
latest, or cut a fresh one on demand:
```bash
# on LXC 209
/opt/photo-project/infra/db-snapshot.sh
ls -t /opt/photo-project/snapshots/photos-*.db.gz | head -1
```

## 2. Push it to dev
```bash
# on LXC 209 (or from Steve's Mac, which can reach both)
LATEST=$(ls -t /opt/photo-project/snapshots/photos-*.db.gz | head -1)
scp "$LATEST" aiuser@10.0.1.121:/home/aiuser/projects/photo-project/data/incoming/
```

## 3. Load it on dev
```bash
# on VM 201
cd /home/aiuser/projects/photo-project
scripts/load-prod-snapshot.sh          # newest data/incoming/photos-*.db.gz
```
The loader backs up the current dev DB, integrity-checks the snapshot, swaps it in,
and runs `alembic upgrade head` (so a dev branch with a **newer** migration applies
it on top of prod's real data — the whole point of refreshing before feature work).
Then restart the dev backend so it reopens the DB:
```bash
(cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8077)   # --host 0.0.0.0!
```

## Slides / images (usually skip)
Dev and prod libraries are separate copies and match after a deploy. Only re-sync
when slides were re-exported/rotated on prod since the last refresh:
```bash
# on LXC 209 -> dev
rsync -av --delete /mnt/photos/library/slides/ aiuser@10.0.1.121:/mnt/photos/library/slides/
# then on dev, pre-warm derivatives so the gallery isn't regenerating on first view
python -m importer.make_thumbnails
```

## Notes / safety
- **Load-only:** `load-prod-snapshot.sh` never touches prod — a stray run can't harm
  production.
- **Discards dev divergence:** refreshing replaces the dev DB with prod's. That's the
  point; the loader keeps a `photos.db.devbak-*` in case you need something back.
- **PII:** the prod DB carries family emails/names — it stays on the internal LAN dev
  box (same data class dev already held); never copy it anywhere external.
- **Auth on dev** stays dev-bypass (admin) regardless of the users in the copied DB.
