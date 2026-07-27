# Restore the Maegley Photo Album database

Snapshots are gzipped SQLite files in `/opt/photo-project/snapshots/`
(`photos-YYYYMMDD-HHMMSS.db.gz`), written daily by `photo-backup.timer`. That local
copy is the **fast restore**, on the same disk as the live DB. There are two layers
behind it, both documented below: the **Proxmox LXC backup** (whole container → DS418,
on-prem) and **B2 replication of these snapshots** (off-site, the one that protects
the database itself).

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
#    NOTE: this failure is INTERMITTENT, which is what makes it dangerous. It depends
#    on whether a WAL happens to be pending. At the 2026-07-27 drill prod's
#    photos.db-wal was 0 bytes (freshly checkpointed) and a naive `cp` would have
#    worked by luck. Don't "test" cp on a quiet box and conclude it's safe.
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
#    ⚠ Use the HOST python3 (below), NOT the api image. The image's ENTRYPOINT is
#    entrypoint.sh, which runs `alembic upgrade head` and then execs uvicorn — and it
#    ignores "$@" entirely, so `docker run <image> python -c ...` does NOT run your
#    command: it silently migrates the DB and starts the server. An inspection
#    container will therefore mutate the very artifact you were trying to measure
#    (hit for real during the 2026-07-27 drill). If you must use the image, override
#    it: `docker run --entrypoint python3 ...`.
python3 -c "
import sqlite3
c = sqlite3.connect('data/photos.db')
print('integrity   :', c.execute('PRAGMA integrity_check').fetchone()[0])   # expect: ok
print('photos      :', c.execute('SELECT count(*) FROM photo').fetchone()[0])
print('people      :', c.execute('SELECT count(*) FROM person').fetchone()[0])
print('people tags :', c.execute('SELECT count(*) FROM photo_person').fetchone()[0])"
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

## Off-site layer — Backblaze B2 (DB snapshots) (built by the Ops agent, 2026-07-27)

The layer that matters most for SPEC §13: it puts the **database** off-site, not just
the pixels. Everything here lives outside the git tree on LXC 209, so a redeploy of
`/opt/photo-project` neither installs nor removes it.

| | |
|---|---|
| Provider | Backblaze B2, **direct from LXC 209** — not via the DS418 |
| Bucket | `maegley-apps-offsite` (private, SSE-B2 on, no Object Lock) |
| Prefix | `photo-album/db/` |
| Object names | `photos-YYYYMMDD-HHMMSS.db.gz` — unchanged from local |
| What pushes it | `/usr/local/sbin/photo-db-offsite.sh` |
| When | Second `ExecStart=` in `photo-backup.service`, so immediately after `db-snapshot.sh`, ~03:30 |
| Off-site retention | **90 daily**, pruned by age against the remote each run |
| Local / Proxmox retention | 14 daily / (5 daily, 1 weekly, 6 monthly) — both unchanged |
| Size | ~276 KB per snapshot; 90 days ≈ 25 MB |
| Credentials | rclone remote `b2`, `/root/.config/rclone/rclone.conf` (0600); bucket-scoped app key, read+write, cannot see other buckets |
| Client-side encryption | None — deliberate: private bucket + SSE-B2, and no passphrase to lose at restore time |

**Direct to B2, not relayed via the DS418.** Steve's call (2026-07-27): *"I don't want
the NAS to be another failure point."* A relay would make the DB's off-site copy depend
on NAS health and Synology Cloud Sync's schedule; the direct push either succeeds or
fails loudly the same night.

**`rclone copy`, never `sync`.** Local keeps 14 days, B2 keeps 90. A `sync` would mirror
local deletions and destroy the 76 days of extra history that are the entire point of
the off-site tier. Pruning is explicit and age-based against the remote, with
`hard_delete = true` so a prune really removes the object instead of stacking a B2
hide-marker over it.

**It verifies the object in the bucket, not the upload.** Each night, after the push,
the script pulls the newest object **back down from B2**, gunzips it, runs
`PRAGMA integrity_check`, counts `photo` rows on that round-tripped copy, and fails if
the count is 0 or below **95% of live** — a floor computed each run rather than a
hardcoded expectation, so it doesn't rot as the catalog grows. Any failure exits
non-zero and fails the systemd unit.

This is deliberately independent of `db-snapshot.sh`'s own check, and the two are
complementary rather than redundant: **`db-snapshot.sh` verifies the local artifact at
creation; `photo-db-offsite.sh` verifies what is actually sitting in B2.** They also
compose safely — the off-site job globs `photos-*.db.gz`, which cannot match the
`.partial` staging name, so it can never ship an unverified snapshot.

**Alerting:** `OnFailure=photo-backup-alert@%n.service` → `/usr/local/sbin/photo-backup-alert.sh`
→ Home Assistant `telegram_bot.send_message`, including the last 8 journal lines so the
message says *why*. Tested end-to-end 2026-07-27 by forcing the verify floor to fail.

### Residual risks (known, not yet addressed)

- **⚠ Alerting catches "ran and failed", not "never ran".** If CT 209 is down or the
  timer is disabled, nothing fires and the silence looks identical to success. The fix
  is a dead-man's switch on the Home Assistant side (alert if no success heartbeat in
  ~25 h); **not yet built.**
- **⚠ Whole-LXC backups are still NOT off-site.** They land on the DS418 and stop there;
  Steve is setting that up with the Ops agent. Lower stakes now that the DB itself is
  replicated — what's missing is fast container rebuild, not the archive's meaning.
- **Detection latency, not retention depth, is the binding constraint.** With 5 Proxmox
  dailies, a corruption unnoticed for more than ~5 days falls back to a single weekly
  and then to monthlies. This archive is browsed occasionally, so two weeks of silence
  is entirely plausible. **Replication makes this sharper, not softer:** an off-site
  copy faithfully reproduces a corrupt snapshot. Both verification layers exist for
  exactly this reason. Note the 90-day B2 tier is what actually buys back the margin
  here — it is the only tier deep enough to survive a slow-noticed corruption.
- **⚠ `db-snapshot.sh`'s own verification is fixed in git but NOT YET ON PROD.** Prod
  runs the old script (source-connection count, STATUS #2) until it pulls. Until then
  the nightly B2 round-trip in `photo-db-offsite.sh` is **the only verification running
  in production** — which is why that layer being independent matters right now.
- **⚠ The `SynDS418` vzdump restore is still undrilled.** Whole-container recovery needs
  a spare VMID and storage on `NUC2c` — a separate exercise from the DB drill below, and
  still outstanding. Its steps are deliberately not written here until someone does one:
  an untested runbook written from memory is the failure mode this section exists to
  prevent.

## Restore drill — B2 snapshot path: PASSED (2026-07-27)

Run by the Ops agent against a throwaway container on a copy; **prod was never touched**
(verified after: `photo-api` up 8h and never restarted, live `data/photos.db` mtime
unchanged, `/health` still 200). Re-runnable via `/usr/local/sbin/restore-drill.sh` on
LXC 209 — idempotent, fresh timestamped workspace each run.

| | newest | pre-scan (Jul 15) |
|---|---|---|
| Object | `photos-20260727-151448.db.gz` (272K) | `photos-20260715-033001.db.gz` (156K) |
| Retrieve from B2 | <1s | ~1s |
| `integrity_check` / photos | ok / **1,910** | ok / **1,140** |
| alembic at rest | `a7b8c9d0e1f2` (head) | `a6b7c8d9e0f1` (one behind) |
| alembic on start | none needed | **upgraded → `a7b8c9d0e1f2`** |
| API healthy | 3s | 2s |
| Endpoints | all 200 | all 200 |

**End-to-end recovery is well under a minute** — retrieval was never the constraint, and
now that's measured rather than assumed. Three things this proved that a file-downloads
check would not have:

1. **The old-schema path works.** The pre-scan snapshot came up one migration behind and
   `alembic upgrade head` applied the §12.3 scan-ingest migration on container start,
   then served real queries. Reaching for a snapshot older than your last migration is
   *the* realistic disaster case, and it had never been tested.
2. **B2-only recovery works.** `photos-20260715` no longer exists on the LXC — 14-day
   local retention had pruned it. It came back from B2 alone, which is the 90-day
   off-site tier earning its keep rather than being assumed.
3. **The WAL step was genuinely exercised.** The drill *plants* stale `-wal`/`-shm` files
   before restoring, so step 4 has real work to do. A drill on a clean directory passes
   for the wrong reason and tells you nothing about the hazard.

**For any future drill:** mount `/mnt/photos/library` `:ro` so a throwaway container
can't mutate the real library. The production mount is read-write by necessity (slide
rotation, derivative generation), so a drill container is the one place you can and
should deny that.

## Notes
- `data/photos.db` is the live DB (bind-mounted to `/data/photos.db` in the
  container). Always stop `api` before replacing it.
- Migrations run automatically on container start (`alembic upgrade head`), so a
  restored older DB is brought to the current schema on the next `start`.
- The image library (`/mnt/photos/library`) is not in these snapshots — it is backed up
  separately with the photo drive. It is mounted **read-write**, not read-only as this
  file previously claimed: the app rotates slides and writes thumbnail/display
  derivatives in place. Verified on prod 2026-07-27 (`findmnt` reports
  `rw,relatime,stripe=16`). Don't treat the mount as a safety guarantee it doesn't give.
