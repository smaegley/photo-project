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

# 3. Back up the current DB first (in case you picked the wrong snapshot)
cp data/photos.db data/photos.db.before-restore

# 4. Restore
gunzip -c snapshots/photos-YYYYMMDD-HHMMSS.db.gz > data/photos.db

# 5. Sanity check, then bring the API back
python3 -c "import sqlite3; print(sqlite3.connect('data/photos.db').execute('SELECT count(*) FROM photo').fetchone()[0])"  # expect ~1140
docker compose start api
curl -s http://127.0.0.1:8077/health   # via caddy: https://photos.maegley.org/health
```

## Notes
- `data/photos.db` is the live DB (bind-mounted to `/data/photos.db` in the
  container). Always stop `api` before replacing it.
- Migrations run automatically on container start (`alembic upgrade head`), so a
  restored older DB is brought to the current schema on the next `start`.
- The image library (`/mnt/photos/library`) is not in these snapshots — it is
  mounted read-only and backed up separately with the photo drive.
