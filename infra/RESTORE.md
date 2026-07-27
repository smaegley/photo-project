# Restore the Maegley Photo Album database

Snapshots are gzipped SQLite files in `/opt/photo-project/snapshots/`
(`photos-YYYYMMDD-HHMMSS.db.gz`), written daily by `photo-backup.timer`. The
LXC/Proxmox backup is the off-site layer; this is the fast local restore.

## Restore a snapshot

```bash
cd /opt/photo-project

# 1. Pick a snapshot
ls -lt snapshots/

# 2. Stop the API so nothing writes during the swap
docker compose stop api

# 3. Back up the current DB first (in case you picked the wrong snapshot).
#    NOT `cp` — the DB is in WAL mode and recent commits live in photos.db-wal, so
#    copying the main file alone silently yields a STALE database (measured: 1,148
#    rows against a live 1,918, no error). Use the online backup API.
python3 -c "
import sqlite3
s = sqlite3.connect('data/photos.db'); d = sqlite3.connect('data/photos.db.before-restore')
with d: s.backup(d)
print('pre-restore backup:', d.execute('SELECT count(*) FROM photo').fetchone()[0], 'photos')
s.close(); d.close()"

# 4. Remove the WAL sidecars BEFORE writing the new DB.
#    They belong to the OLD database; leaving them means SQLite replays a foreign WAL
#    over the new pages -> "malformed disk image". This is the single most likely way
#    to corrupt the DB while restoring it.
rm -f data/photos.db-wal data/photos.db-shm

# 5. Restore
gunzip -c snapshots/photos-YYYYMMDD-HHMMSS.db.gz > data/photos.db

# 6. Sanity check the restored file, then bring the API back.
python3 -c "
import sqlite3
c = sqlite3.connect('data/photos.db')
print('integrity:', c.execute('PRAGMA integrity_check').fetchone()[0])   # expect: ok
print('photos   :', c.execute('SELECT count(*) FROM photo').fetchone()[0])
print('people   :', c.execute('SELECT count(*) FROM photo_person').fetchone()[0])"
# Compare the counts against the pre-restore backup above and against how old the
# snapshot is — don't check them against a number written in this file, which goes
# stale (it was 1,140 slides-only; 1,918 after scans; more once digital lands).
docker compose start api
curl -s http://127.0.0.1:8077/health   # via caddy: https://photos.maegley.org/health
```

## Off-box layer — Proxmox LXC backup (recorded 2026-07-27)

The local `snapshots/` directory lives on the **same LXC root disk** as the live DB,
so it is a fast restore point, **not** disaster coverage. The off-box layer is the
Proxmox backup job, whose settings are:

| | |
|---|---|
| Storage | **SynDS418** (Synology DS418 — a separate box from the Proxmox host) |
| Schedule | **daily 02:00** |
| Mode | **Stop** |
| Compression | ZSTD |
| Guests | 9 selected, including **209 `photo-album`** (node `NUC2c`) |
| Retention | **5 daily, 1 weekly, 6 monthly** |

**Mode `Stop` is the right choice here and worth preserving.** It shuts the container
down before imaging, so SQLite is quiesced — `photos.db` and its `-wal`/`-shm` are
captured consistently, with no torn write and no WAL belonging to a half-finished
transaction. A `Snapshot`-mode job would not give that guarantee for this workload.
The cost is a brief nightly outage at 02:00, which is irrelevant for a family archive.

No collision with the in-container snapshot timer, which runs at **03:30**
(`photo-backup.timer`) — after the container is back up.

### Residual risks (known, not yet addressed)

- **✅ DB snapshots are now replicated to B2 (Steve + Ops agent, 2026-07-27).** This is
  the layer that matters most for §13: it puts the *database* off-site, not just the
  pixels. **Details still to be recorded here** — bucket/prefix, retention, what pushes
  it and when, and whether the uploaded object is verified. Fill those in; a backup
  nobody can describe is hard to restore from under pressure.
- **⚠ Whole-LXC backups are still NOT off-site.** They land on the DS418 and stop there;
  Steve is setting that up with the Ops agent. Lower stakes now that the DB itself is
  replicated — what's missing is fast container rebuild, not the archive's meaning.
- **Detection latency, not retention depth, is the binding constraint.** With 5 Proxmox
  dailies, a corruption unnoticed for more than ~5 days falls back to a single weekly
  and then to monthlies. This archive is browsed occasionally, so two weeks of silence
  is entirely plausible. **Replication makes this sharper, not softer:** an off-site
  copy faithfully reproduces a corrupt snapshot. That is why `db-snapshot.sh` now
  verifies the finished artifact and refuses to publish a bad one (STATUS #2, fixed
  2026-07-27) — the off-site sync can only ever pick up a snapshot that was opened,
  integrity-checked and found non-empty.
- **⚠ Never drilled.** No restore has been performed or timed — neither from a
  `SynDS418` vzdump nor from the new B2 copy of a snapshot. The vzdump steps are
  deliberately not written down here: an untested runbook written from memory is the
  failure mode this section exists to prevent. Do one real restore, record what you
  actually did, then replace this bullet. **Restoring the B2 snapshot is the cheaper
  drill and covers the digital prerequisite — do that one first.**

## Notes
- `data/photos.db` is the live DB (bind-mounted to `/data/photos.db` in the
  container). Always stop `api` before replacing it.
- Migrations run automatically on container start (`alembic upgrade head`), so a
  restored older DB is brought to the current schema on the next `start`.
- The image library (`/mnt/photos/library`) is not in these snapshots — it is
  mounted read-only and backed up separately with the photo drive.
