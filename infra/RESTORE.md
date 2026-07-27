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

- **Detection latency, not retention depth, is the binding constraint.** With 5 dailies,
  a corruption that goes unnoticed for more than ~5 days falls back to a single weekly
  (up to 7 days old) and then to monthlies. This archive is browsed occasionally, so
  two weeks of silence is entirely plausible. That is the real argument for making the
  nightly snapshot *verify itself* (STATUS known issue #2) — backups you can't trust to
  be good are only as useful as your speed at noticing.
- **Everything is on-premises.** The DS418 is also the NAS in the B2 photo sync chain
  (SPEC §13.1), so it shares fate with the photo library, and a fire or theft takes
  `NUC2c` and the DS418 together. **Open question worth confirming:** whether the
  Proxmox backup folder on the DS418 is itself inside the Backblaze sync set. If it is,
  that is genuine off-site coverage; if not, there is none.
- **⚠ Never drilled.** No restore from a `SynDS418` vzdump has been performed or timed,
  and the exact steps are not written down here on purpose — an untested runbook written
  from memory is the failure mode this section exists to prevent. Do one real restore to
  a scratch VMID, record what you actually did, then replace this bullet.

## Notes
- `data/photos.db` is the live DB (bind-mounted to `/data/photos.db` in the
  container). Always stop `api` before replacing it.
- Migrations run automatically on container start (`alembic upgrade head`), so a
  restored older DB is brought to the current schema on the next `start`.
- The image library (`/mnt/photos/library`) is not in these snapshots — it is
  mounted read-only and backed up separately with the photo drive.
