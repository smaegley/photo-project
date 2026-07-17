# STATUS — read this first

**Purpose:** fast orientation for a drop-in session. Steve comes into this project
occasionally to triage a **prod issue**, a **bug**, or an **enhancement** — this file
should tell you where to look and what's already known, so you don't re-derive it.

**This file is a pointer, not a copy.** Design → `SPEC.md`. Environments/run →
`README.md`. Deploy/runbook → `infra/DEPLOY.md`. Restore → `infra/RESTORE.md`.
What lives *only* here: current deployment state, known-and-accepted issues, and the
gotchas that burn time.

**Last reviewed:** 2026-07-14 (full project + prod-setup review; image pool fix §10.16).

---

## 30-second orientation

Private family archive: 1,140 digitized 35mm slides (1962–1976), shot and
hand-captioned by Steve's father Wendel. Browse by people/place/date/event + map +
timeline, with an admin curation layer. **Phase 1 is complete, frozen, and live.**
This is *not* a greenfield project — it's a maintenance-mode app.

- **Prod:** https://photos.maegley.org — LXC 209 (`10.0.1.178`), Docker Compose
  (`api` + `caddy`) behind a Cloudflare Tunnel + Cloudflare Access.
- **Dev:** VM 201 (`10.0.1.121`), this repo, no Cloudflare, dev-bypass auth.
- **Source of truth for data:** prod's `/opt/photo-project/data/photos.db`.
- **Stack:** FastAPI + SQLite + Alembic / React + Vite + MapLibre.

---

## Current state

| | |
|---|---|
| Repo HEAD (dev) | `c9fcf03` — §12 Scanned Photos build + lightbox/rail fixes (2026-07-17) |
| Pushed to origin | yes (`origin/main` @ `c9fcf03`) |
| Deployed to prod | `d0d8ade` — 2026-07-14, §10.15 + §10.16 (**prod is BEHIND main by the §12 work**) |
| Last deploy before that | §10.14 @ `e0875cd` (2026-07-07) |

> **⏸ DEPLOY DELIBERATELY HELD (2026-07-17).** The §12 "Scanned Photos" build is done,
> verified on dev, pushed to origin — but **not deployed to prod, by Steve's choice.**
> Reason: with no real scans yet, a deploy would (a) reshuffle the slide gallery —
> **17 of 32 rolls move** under the new date-interleaved sort (§12.5) — and (b) add an
> empty "Scanned Photos" tab + rename Gallery/Rolls → All Photos/Slide Photos, all with
> no payoff. **Plan: Steve returns with a real scanned-photo set, then we deploy the
> whole thing together.** Deploying is safe whenever (additive migration, non-destructive
> importer) — the hold is purely to avoid a puzzling half-state for the family.
>
> **Deploy-day runbook (when the scans are ready):** on LXC 209
> `git pull && DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build`
> (migration `a7b8c9d0e1f2` auto-runs) → rsync batches to
> `/mnt/photos/library/photos/<batch>/` → `docker compose exec api python -m
> app.import_photos --dry-run` → review `./data/review/*.csv` → real run →
> `docker compose exec api python -m app.prewarm`. Full detail: SPEC §12.10 / §12.12.
> **Never** `import_data.py` on prod. Three of the held commits are also general UX
> fixes (click-outside-to-close, close ✕ top-right, Places rail live counts) — they
> ship with this deploy.
>
> **Dev carries throwaway test data** from verification: ~8 sample scan rows under
> `/mnt/photos/library/photos/` + a `deirdre_carlile` test person. Harmless; wiped by
> the next `load-prod-snapshot.sh` refresh. Pre-migration dev DB backup at
> `/tmp/photos.db.pre-scan-bak`. A dev backend (uvicorn :8077, no --reload) was left
> running for the verification.

Re-confirm prod's actual HEAD any time it matters — it's one command, and this table
is only as good as its last update:

```bash
ssh -i ~/.ssh/proxmox_lxc root@10.0.1.178 'cd /opt/photo-project && git rev-parse --short HEAD'
```

**§10.16 is deployed but not yet behaviourally confirmed.** The fix only shows itself
on a *cold* visit after >15 minutes away — a warm gallery never reproduced the bug in
the first place, so "it looks fine" right after a deploy proves nothing. The real
signal is this going quiet across a cold visit:

```bash
docker compose logs --tail=200 api | grep -i 'QueuePool\|TimeoutError'   # expect nothing
```

Also confirm `last_login` still advances on page loads (it moved from the tiles to the
`/api/photos` call in §10.16). If it has stopped moving, the `touch=False` split is
wrong. Check via **⚙ Admin → Manage users**, or:

```bash
docker compose exec api python -c "
from app.database import SessionLocal; from app import models as m
print([(u.email, str(u.last_login)) for u in SessionLocal().query(m.User).all()])"
```

Unrelated but easy to miss: `scripts/apply-notes-patch-20260707.py` (Ryan's 3 caption
+ 440 notes OCR corrections) is a **separate idempotent data patch** that no deploy
applies; it supports `--dry-run`.

---

## Triage playbook

### "Prod is down / behaving oddly"
Work outside-in; the layers are independent and each has a distinct failure signature.

```bash
ssh -i ~/.ssh/proxmox_lxc root@10.0.1.178
systemctl status cloudflared          # tunnel up? edge 502 = tunnel/ingress
docker compose ps                     # in /opt/photo-project — api + caddy running?
docker compose logs --tail=50 api     # app errors, migration failures
curl -s http://127.0.0.1:8080/        # Caddy serving locally?
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8077/health').read().decode())"
```

Reading the signals:
- **502 at the edge but local `curl` works** → tunnel ingress target is wrong.
- **`/health` says `"auth":"dev-bypass"` in prod** → `.env` is missing/empty. This is
  a real failure mode, not cosmetic: every visitor silently becomes admin-as-Steve.
  See Known issue #1.
- **Whole hostname returns the Access login page** → expected. The entire site is
  behind Access, so external health checks *always* look like this. Verify internally.
- **SQLite "malformed disk image"** → stale `-wal`/`-shm` next to a swapped-in DB.
  See Known issue #3.

### "Bug in the app"
1. Reproduce on **dev** (VM 201), never prod. Prod's DB is the source of truth.
2. Need real data? `scripts/load-prod-snapshot.sh` — one-directional, integrity-checked,
   handles WAL cleanup. It is the *good* path; use it rather than improvising a copy.
3. `SPEC.md` §10 is the build log and is current where it refines §§1–9 — check it
   before assuming the frozen §§1–9 design describes today's behavior.

### "Enhancement"
- Open/deferred items: `SPEC.md` §10.7. Phase 2 (non-slide ingest) is designed in
  §11; **Phase 2a "Scanned Photos" (origin=scan) is BUILT & VERIFIED ON DEV**
  (2026-07-17, migration `a7b8c9d0e1f2`, §12.12) — only the **prod rollout (slice 7)**
  remains. Scan importer: `docker compose exec api python -m app.import_photos
  [--dry-run]` then `python -m app.prewarm`; non-destructive, reviews to
  `./data/review/*.csv`. **Dev carries sample scan rows + a `deirdre_carlile`
  test person** from verification (harmless; cleared on the next prod-snapshot
  refresh). Dev DB backed up to `/tmp/photos.db.pre-scan-bak` before the migration.
- Ship path: dev → commit → push → on LXC 209
  `git pull && DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build`
  (migrations auto-run on `api` start). Full runbook: `infra/DEPLOY.md`.

---

## Known and ACCEPTED issues — do not "helpfully" fix

Found in the 2026-07-14 review. **Steve reviewed these and accepted the risk.** They
are recorded so future sessions don't rediscover them, re-litigate them, or burn a
session fixing what was a deliberate call. Raise them only if they actually cause the
problem being triaged, or if Steve asks.

1. **Auth fails open to admin if `.env` is missing.** `config.py:20-23` defaults
   `cf_access_*` to `""` and `dev_user_role` to `"admin"`; `auth.py:58` derives dev
   mode from *absence* of config; `docker-compose.yml:12-13` interpolates
   `${CF_ACCESS_TEAM_DOMAIN}` with no `:?` guard, so a missing `.env` starts cleanly
   and serves everyone as admin-as-Steve. Cloudflare Access still gates *who* reaches
   the app, so blast radius is family-only. Fix if ever wanted:
   `${CF_ACCESS_TEAM_DOMAIN:?set in .env}`.
   *Note the docstring at `auth.py:8-9` claims the opposite ("production is never
   accidentally open") — the reasoning there is inverted. Don't trust that comment.*

2. **Nightly snapshot verification is a no-op.** `infra/db-snapshot.sh:38` reads the
   photo count from `s` (the **source** DB), not `d` (the backup). The reassuring
   `1140 photos` in the log says nothing about the snapshot's integrity. The snapshot
   *mechanism* is correct (online backup API, not `cp`). Consequence: a corrupt
   snapshot would report success indefinitely. If you are ever restoring, verify the
   snapshot by hand first.

3. **`infra/RESTORE.md:22` doesn't remove `-wal`/`-shm`** before gunzipping over
   `photos.db` — the exact corruption `scripts/load-prod-snapshot.sh:57-58` warns
   about. **The dev refresh path is more careful than the prod restore path.** If you
   are executing a real restore, crib the WAL handling from `load-prod-snapshot.sh`.

4. **Snapshots share a disk with the live DB.** `/opt/photo-project/snapshots/` sits
   on the LXC root disk alongside `data/photos.db`; disk loss takes both. Off-box
   coverage is the Proxmox LXC backup — its location, retention, and restore have
   never been documented or tested in this repo.

5. **Lower severity, all accepted:** frontend builds on the 2 GB prod box during
   `docker compose up --build` (OOM risk mid-deploy); `/api/admin/geocode` can starve
   the ~40-thread pool (sync routes + 1.1s sleep + 30s timeout); `iss` not verified in
   the Access JWT (signature/`aud`/`exp` *are*, and RS256 is pinned); concurrent undos
   can double-apply an inverse (lossy re-encode for `photo_rotate`); doc drift —
   `RESTORE.md:36` and `backend/.env.example:5` say the library is mounted read-only,
   but compose mounts it **read-write** (correct: rotation writes in place).

**Verified sound in the same review** — don't re-audit without reason: no SQL
injection (ORM throughout; only literal PRAGMAs use `execute()`); path traversal
correctly guarded in `routers/images.py:33-38` (resolve-then-contain, catches symlink
escapes); all 27 `/api/admin/*` routes role-gated; `database.py` WAL + `busy_timeout`
+ FK enforcement correct.

---

## Gotchas that waste time

- **"78 modified files" with zero content changes.** Steve's Mac mounts this repo over
  SMB (`//aiuser@10.0.1.121/projects`), which reports the exec bit on everything, so
  git sees `100644 → 100755` on every tracked file. **The tree is clean.** Don't commit
  them — prod deploys via `git pull` and would flip every file to `100755` for no
  benefit. Fix is `git config core.filemode false` (that `.git/config` is the VM's,
  shared over SMB — harmless there, since the VM's native filesystem already sees 644).
- **Connection/streaming bugs are invisible on loopback.** §10.16's pool exhaustion
  could not be reproduced by a 60-tile burst on dev — localhost streams instantly, so
  nothing stays pinned. It only appeared with *slow-reading clients* standing in for
  tunnel latency (9.56s vs 0.03s). If prod misbehaves under load and dev looks fine,
  simulate the slow client before concluding it's environmental.
- **Dev backend must bind `0.0.0.0`**, not `127.0.0.1`, or Steve's Mac can't reach it
  over the LAN. A dev box unreachable from the LAN is *always* a binding problem —
  dev has no Cloudflare in front of it.
- **`pkill -f uvicorn` kills your own shell** (the pattern matches its own argv). Kill
  by port instead — see `REVIEW-FIXES.md` §0 for the exact incantation.
- **Never run `importer/import_data.py` on prod** — it's destructive (SPEC §11.8). Use
  `apply_lr_people.py` / `import_photos.py`.
- **macOS `._*` AppleDouble files** litter the library folders. Safe to delete.
- **BuildKit is disabled on the prod host** (`DOCKER_BUILDKIT=0
  COMPOSE_DOCKER_CLI_BUILD=0`) — a NUC2c kernel/runc crash, not a preference. Keep it.

---

## Maintaining this file

Update the **Current state** table on every deploy, and add to **Known and accepted
issues** whenever a review turns something up that isn't being fixed. Keep it short —
if it grows into a second changelog it stops being read, and `SPEC.md` §10 is already
the build log. Durable facts belong in `CLAUDE.md`; volatile state belongs here.
</content>
</invoke>
