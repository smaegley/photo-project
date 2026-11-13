# Deploy — Maegley Photo Album

Production runs as two containers via Docker Compose behind Cloudflare Access at
**`photos.maegley.org`**:

- **`api`** — FastAPI + SQLite (`Dockerfile`). Runs `alembic upgrade head` then
  uvicorn on `:8077`. DB on a bind-mounted volume; image library mounted
  read-write (the app rotates slides + writes thumbnails; masters live on the Mac).
- **`caddy`** — serves the built SPA and reverse-proxies `/api` + `/health` to
  `api`, over plain HTTP on `127.0.0.1:8080` (`frontend/Dockerfile`, `Caddyfile`).
- **`cloudflared`** (host systemd) — outbound-only Cloudflare Tunnel that routes
  `photos.maegley.org` → `http://127.0.0.1:8080`. TLS, the public cert, and
  **Cloudflare Access** enforcement all happen at Cloudflare's edge; nothing is
  exposed on the LAN or router.

| Placeholder            | Value |
|------------------------|-------|
| LXC ID                 | `209` |
| LXC IP                 | `10.0.1.178` |
| Deploy dir             | `/opt/photo-project` |
| Domain                 | `photos.maegley.org` |

**Specs:** 2 vCPU, 4 GB RAM (verified by ops 2026-11-13; was documented as 2 GB), 16 GB root disk + 50 GB mp disk at
`/mnt/photos/library`. The app itself is light (SQLite + a small FastAPI), but the
**image build is the heavy part** — the Node/Vite frontend build plus Docker layers
(python-slim, node, caddy) want headroom. The image library is on its own disk and
does **not** count against root.
LXC is Debian 12, unprivileged, with `features: nesting=1,keyctl=1` and
`lxc.apparmor.profile: unconfined` for Docker.

---

## 0. Provision the LXC ✅

- ✅ LXC 209 `photo-album` created on NUC2c at `10.0.1.178`. Debian 12 unprivileged,
  `features: nesting=1,keyctl=1`, `lxc.apparmor.profile: unconfined`,
  `lxc.mount.auto: proc:rw sys:rw`. Root pw: in the password manager (do NOT
  commit credentials to this repo — it may go public).
  `ssh -i ~/.ssh/proxmox_lxc root@10.0.1.178`
- ✅ Docker CE + compose plugin installed. BuildKit disabled via
  `/etc/docker/daemon.json` (`{"features":{"buildkit":false}}`) — same NUC2c
  kernel/runc crash as LXC 207. Verified: `docker run hello-world` passes.
- ✅ **Repo access (private repo):** the LXC needs to authenticate to GitHub.
  Generate a deploy key and add it as read-only to the repo:
  ```bash
  ssh -i ~/.ssh/proxmox_lxc root@10.0.1.178
  ssh-keygen -t ed25519 -f /root/.ssh/github_photos -N ""
  cat /root/.ssh/github_photos.pub
  # → paste into github.com/smaegley/photo-project → Settings → Deploy keys (read-only)
  ```
  Then add to `/root/.ssh/config` on the LXC:
  ```
  Host github.com
      IdentityFile /root/.ssh/github_photos
  ```
- ✅ **Clone the repo:**
  ```bash
  git clone git@github.com:smaegley/photo-project.git /opt/photo-project
  ```

## 1. Library access ✅

The image library lives on a dedicated **50 GB LVM volume** (`local-lvm:vm-209-disk-1`)
mounted at `/mnt/photos/library` on LXC 209. Data was rsynced from VM 201
(`/mnt/photos/library`) on 2026-06-26. Counts verified: 1,140 slides, 1,140
thumbnails, 64 index cards. ~6.5 GB used, ~40 GB headroom.

VM 201's original 200 GB `DataZFS:vm-201-disk-0` is intentionally left in place
(to reclaim later).

Set `LIBRARY_ROOT_HOST=/mnt/photos/library` in `.env` (library is local to the LXC
— no NFS or bind mount needed). The compose file defaults to this path if the var
is unset, so in practice you can omit it.

Sanity check:
```bash
ls /mnt/photos/library/thumbnails | wc -l    # expect 1140
```

## 2. Cloudflare Tunnel + Access ✅

> **⚠️ APPROACH CHANGED (2026-06-26) — was a non-proxied A record, now a Cloudflare
> Tunnel.** A direct LAN A record bypasses Cloudflare Access, so the API never
> receives the `Cf-Access-Jwt-Assertion` header and rejects every request. This
> app must sit behind Access (like migraine), which requires the tunnel.
> **If you already created the `photos.maegley.org` A record from the earlier
> version of this doc, delete it** — it would resolve straight to the LXC and
> bypass Access. The tunnel creates a CNAME instead (step 4).
>
> Related artifact changes pushed with this: Caddy is now HTTP-only on
> `127.0.0.1:8080` (no TLS, no `caddy-dns/cloudflare` image), and **`.env` no
> longer has `CLOUDFLARE_API_TOKEN`** — TLS/cert is handled at Cloudflare's edge.
> `git pull` before deploying.

Mirrors migraine-tracker (outbound-only tunnel; no inbound ports, no public IP,
no A record). Reference: `migraine-tracker/infra/proxmox-agent-brief.md` §6.

1. **Install cloudflared** on LXC 209 — download the binary from Cloudflare's
   releases (NOT the apt repo): https://github.com/cloudflare/cloudflared/releases

2. **Create the tunnel:**
   ```bash
   cloudflared tunnel login            # opens a browser on your workstation
   cloudflared tunnel create photo-album
   # note the tunnel UUID from the output
   ```

3. **Config** `/etc/cloudflared/config.yml` — route the hostname to Caddy's
   localhost-published HTTP port (step 4 publishes 127.0.0.1:8080):
   ```yaml
   tunnel: <tunnel-UUID>
   credentials-file: /etc/cloudflared/<tunnel-UUID>.json
   ingress:
     - hostname: photos.maegley.org
       service: http://127.0.0.1:8080
     - service: http_status:404
   ```

4. **DNS (CNAME via the tunnel)** + run as a service:
   ```bash
   cloudflared tunnel route dns photo-album photos.maegley.org   # creates the CNAME
   cloudflared service install
   systemctl enable --now cloudflared
   ```

5. **Access application** — in the Cloudflare Zero Trust dashboard, create a
   self-hosted app for `photos.maegley.org` and add an Access **policy** allowing
   the family emails. Record two values for `.env`:
   - **team domain**, e.g. `maegley.cloudflareaccess.com` → `CF_ACCESS_TEAM_DOMAIN`
   - **Application Audience (AUD) tag** → `CF_ACCESS_AUD`

   Access enforcement happens at Cloudflare's edge (in front of the tunnel), so it
   injects the `Cf-Access-Jwt-Assertion` header the API verifies. This is why the
   tunnel is required — a direct LAN A record would bypass Access and the API would
   reject every request.

## 3. Seed data onto the LXC ✅

The DB is **not** in git (gitignored); `data/` doesn't exist after a clone.
Copy the live DB from VM 201:
```bash
mkdir -p /opt/photo-project/data
scp aiuser@10.0.1.121:/home/aiuser/projects/photo-project/data/photos.db \
    /opt/photo-project/data/photos.db
```
**Thumbnails** are already present in the library (step 1 ✅) — no regeneration
needed.

## 4. Configure + launch ✅

```bash
cd /opt/photo-project
cp .env.example .env
# Fill in: CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD  (no CLOUDFLARE_API_TOKEN — the
# tunnel handles TLS/cert). LIBRARY_ROOT_HOST defaults to /mnt/photos/library.
$EDITOR .env

export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose up -d --build
docker compose logs -f api     # watch "alembic upgrade head" then uvicorn start
```

Verify the API is up **internally** (the entire hostname is behind Cloudflare
Access, so external health checks return the Access login page):
```bash
docker compose exec api python -c "
import urllib.request, sys
print(urllib.request.urlopen('http://localhost:8077/health').read().decode())"
# expect {"auth":"cloudflare-access",...}
```
`"auth":"cloudflare-access"` (not `"dev-bypass"`) confirms both `CF_ACCESS_*`
vars are set and JWT enforcement is live. Then open `https://photos.maegley.org`
in a browser — Cloudflare Access challenges you, then the gallery loads.

If the site doesn't load: `systemctl status cloudflared` and
`cloudflared tunnel info photo-album` (tunnel up?), `docker compose ps` (caddy +
api up?), and `curl -s http://127.0.0.1:8080/` on the LXC (Caddy serving locally?).
A 502 at the edge but a working local curl means the tunnel ingress target is wrong.

## 5. Seed the admin + invite family ✅

If you copied the existing `photos.db` (step 3), Steve's `user` row is already
`admin` linked to person `steve` — skip the seed below and go straight to inviting
family. Otherwise seed once:
```bash
docker compose exec api python - <<'PY'
from app.database import SessionLocal
from app import models as m
db = SessionLocal()
u = db.query(m.User).filter(m.User.email=="steve@maegley.com").first() \
    or m.User(email="steve@maegley.com")
u.role="admin"; u.person_id="steve"; db.add(u); db.commit()
print("admin:", u.email, u.role, u.person_id)
PY
```
To invite family: add each email to the Cloudflare Access policy (step 2) **and**
register it in **⚙ Admin → Manage users** with a role and an optional linked
person (roots their People filter on themselves).
Roles: **contributor** = per-photo + bulk tagging, rotate, geocode lookup;
**admin** = the above plus event/place vocabulary, undo, and user management.

## 6. Backups ✅

```bash
cp /opt/photo-project/infra/photo-backup.service /etc/systemd/system/
cp /opt/photo-project/infra/photo-backup.timer   /etc/systemd/system/
systemctl enable --now photo-backup.timer
systemctl list-timers photo-backup.timer
/opt/photo-project/infra/db-snapshot.sh    # one manual run to confirm
```
Daily snapshot at 03:30 **UTC** (the container runs UTC; that is 20:30 Mountain the evening before) → `/opt/photo-project/snapshots/` (14-day retention). That
directory is on the **same disk as the live DB**, so it is a fast restore point, not
disaster coverage. Off-box is the Proxmox job: **daily 02:00, mode Stop, → SynDS418,
5 daily / 1 weekly / 6 monthly**, LXC 209 included — details, why `Stop` matters for
SQLite, and the residual gaps are in `RESTORE.md`. Restore: see `RESTORE.md`.

## Updating later

```bash
cd /opt/photo-project && git pull
export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
docker compose up -d --build      # migrations run on api start
```

### After replacing/re-exporting slides (pre-warm derivatives)

Whenever you rsync new/changed slide JPEGs into the library, **pre-warm the
derivatives** so the gallery isn't regenerating thumbnails + display images lazily
on first view (a burst on this 2-vCPU box). It's staleness-aware, so it only touches
what changed:

```bash
# after the slides rsync + `docker compose up -d --build`
docker compose exec api python -m app.prewarm          # regenerate stale only
# docker compose exec api python -m app.prewarm --force  # if you need a full rebuild
```

If you cleared the caches (`rm -rf /mnt/photos/library/{thumbnails,display}/*`), the
first `app.prewarm` run regenerates everything up front — do it before announcing the
update so browsing stays fast. The server still self-heals anything missed (a slide
re-exported under the same name refreshes automatically), so this is an optimization,
not a correctness requirement.

---

## Ingest image and face matching on prod (SPEC §15.12, §15.13 — added 2026-11-13)

Face detection and matching run **on prod** in a separate one-shot image, never in the
`api` container. `docker compose up -d` does not build or start it (it sits behind the
`ingest` profile), so ordinary deploys are unchanged.

**One-time setup**

```bash
# 1. model weights — installed on the host, mounted read-only, never downloaded at run time
mkdir -p /opt/photo-project/models
rsync -a aiuser@10.0.1.121:/home/aiuser/.insightface/models/buffalo_l /opt/photo-project/models/
ls /opt/photo-project/models/buffalo_l        # expect det_10g.onnx and w600k_r50.onnx (+3 unused), ~326 MB

# 2. build the image (BuildKit off, as always on this host) — about 790 MB
cd /opt/photo-project
DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose --profile ingest build ingest
```

**Running a job** — the image's entrypoint is `python -m`, so the argument is a module:

```bash
docker compose --profile ingest run --rm ingest app.detect_faces --limit 20   # smoke test
docker compose --profile ingest run --rm ingest app.detect_faces              # resumable; skips photos already scanned
docker compose --profile ingest run --rm ingest app.match_faces               # writes suggestions for Review faces
docker compose --profile ingest run --rm ingest app.cluster_faces             # groups unknown faces
```

- Limits are in `docker-compose.yml`: 2.6 GB memory (= swap limit, so it is killed
  cleanly instead of swapping), 2 CPUs at low priority. Measured on dev under those
  limits: **about 2 s per photo**, so a full pass over ~7k photos is 3–4 hours. For a
  one-off daytime pass add `--cpus 4` after `run` if the box has 4 cores.
- `detect_faces` reads the local display derivatives, not B2, and commits per photo:
  if it is interrupted (including by the 02:00 Mountain backup) just run it again.
- Take a DB snapshot first (`bash infra/db-snapshot.sh`). Nothing here deletes data,
  but `match_faces` replaces all *pending* suggestions each run.
**Nightly ingest by hand** (until the timer exists — SPEC §15.15 slice 8):

```bash
docker compose --profile ingest run --rm ingest app.ingest --dry-run   # listing + reconcile report, no writes
docker compose --profile ingest run --rm ingest app.ingest             # stage, faces, match, outings
docker compose exec -T api python -m app.outings                       # regroup/re-triage only; no ML, seconds
```

- **Reconcile** (photos deleted, moved or replaced in the bucket) starts in **report**
  mode: each run prints `reconcile (report): N B2 photos checked — x moved, y deleted,
  z replaced` and stores the lists in `ingest_run.detail`, but changes nothing. After
  a few clean nights set `INGEST_RECONCILE=apply` in `/opt/photo-project/.env`. In
  apply mode, more than 25 vanished masters in one run culls **none** and the run ends
  `failed` on purpose — look at the listing before overriding.
- A photo culled this way shows in Manage ▾ → Culled photos as culled by
  `ingest:deleted-from-b2` and restores like any other.
- If the file is put back in B2, the next apply-mode run restores the photo by itself.
  In both modes the Inbox screen shows a **Missing from B2** tab while any are missing.

- **Never import face or tag state from dev** (`tags_io --import`, `faces_io --import`).
  Prod's own tags are the reference set (Steve, 2026-11-13).
