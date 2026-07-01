# Maegley Photo Album

Private family photo archive — browse ~1,140 digitized 35mm slides (1962–1976,
shot and hand-captioned by Steve's father Wendel) by **people, place, date, and
event**, with a map and timeline. Plus an admin layer to curate the metadata.

See **`SPEC.md`** — §§1–9 are the frozen v1.0 design; **§10 is the current build
log** (source of truth where it refines the design).

## What it does
- **Faceted browse:** four AND-combined facets (time / people / events / map) with
  live counts; Google-Photos-style gallery; a "Rolls" view showing Wendel's
  original index cards beside the clickable caption list.
- **Lightbox:** large view with fit/zoom/pan, Dad's notes panel, and a
  show-roll-card reveal.
- **Map:** maplibre; flies to / fits selected places; shades US-state regions.
- **Admin / curation** (admin-only for now, dev runs as admin):
  - Manage the **event** and **place** vocabularies — rename / merge / delete.
  - **Bulk** apply/remove an event, person, or place across a selection.
  - **Per-photo** editing in the lightbox; **rotate** a slide.
  - **Map pin editor** with **geocode lookup** (OSM Nominatim).
  - **Undo** — repeatable, backed by an inverse-logged contribution trail.

## Layout
- `backend/` — FastAPI app (`app/`, incl. `routers/admin.py`, `geocoding.py`),
  Alembic migrations (`alembic/`).
- `frontend/` — React + Vite + maplibre SPA (`src/`).
- `importer/` — data pipeline: `import_data.py`, `dates.py`, `geocode.py`,
  `make_thumbnails.py`.
- **Not in the repo** (gitignored): the SQLite DB (`data/photos.db` — the live
  source of truth, backed up at the VM/LXC level), the family-data curation CSVs
  (`*_firstpass.csv`, `people_decisions.csv`), and the slide images
  (`/mnt/photos/library/{slides,index_cards,thumbnails}`). A fresh clone needs the
  manifest + curation CSVs supplied to rebuild the DB from scratch.

## Run (dev)
**Backend** (http://127.0.0.1:8077/api/docs):
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && alembic upgrade head        # schema
# (first build only — needs manifest + curation CSVs present:)
#   python -m importer.import_data         # manifest + CSVs -> data/photos.db
#   python -m importer.geocode             # fill gazetteer lat/lon
#   python -m importer.make_thumbnails     # -> library/thumbnails/
uvicorn app.main:app --host 0.0.0.0 --port 8077
```
**Frontend** (http://localhost:5173, proxies `/api` -> 127.0.0.1:8077):
```bash
cd frontend && npm install && npm run dev
```
Re-running the importer is idempotent (rebuilds content tables; leaves
users/contributions).

## Auth
Behind **Cloudflare Access** in production (set `CF_ACCESS_*` in `.env`, see
`.env.example`). With those unset, the app runs in local-dev mode as
`DEV_USER_EMAIL` with the `admin` role. SPEC §6.3 / §10.5 (planned two-tier admin:
contributor = tagging, admin = vocabulary + undo).

## Environments (dev vs prod)

**These are two separate boxes with separate databases.** Prod is behind Cloudflare;
dev is **not** — so if the dev app is unreachable from the LAN it's a *binding*
problem (see below), never a Cloudflare one.

### DEV — VM 201 (`10.0.1.121`)
- **Repo/DB/library:** `/home/aiuser/projects/photo-project`; DB `data/photos.db`
  (a dev copy, **not** prod's data); images at `/mnt/photos/library` (local to the VM).
- **How it runs (two processes, from the repo):**
  ```bash
  # backend — MUST bind 0.0.0.0 or the Mac can't reach it over the LAN
  cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8077
  # frontend (proxies /api + /health -> 127.0.0.1:8077; vite.config binds 0.0.0.0:5173)
  cd frontend && npm run dev
  ```
- **How you access it:** browse **http://10.0.1.121:5173/** (the Vite UI) from your
  Mac. The API is at `:8077` but you normally just use the 5173 UI.
- **Auth:** local **dev-bypass** — `CF_ACCESS_*` unset, so it runs as `DEV_USER_EMAIL`
  (Steve) with `admin`. No login, no Cloudflare. `/health` shows `"auth":"dev-bypass"`.
- **Gotcha:** binding uvicorn to `127.0.0.1` makes it loopback-only and breaks LAN
  access. Always use `--host 0.0.0.0`.

### PROD — LXC 209 (`10.0.1.178`), `photos.maegley.org`
- **Runtime:** Docker Compose in `/opt/photo-project` — `api` (FastAPI+SQLite on
  `:8077`, internal) + `caddy` (serves the built SPA, proxies `/api`+`/health`,
  published on `127.0.0.1:8080`). A host-systemd **`cloudflared`** outbound tunnel
  routes `photos.maegley.org` → `127.0.0.1:8080`. No LAN ports, no A record.
- **How you access it:** **https://photos.maegley.org** — Cloudflare **Access**
  challenges you at the edge (email in the Access policy), then the app loads. TLS/cert
  are all at Cloudflare's edge. `/health` shows `"auth":"cloudflare-access"`.
- **DB/library:** `/opt/photo-project/data/photos.db` is the **production source of
  truth** (real tagging happens here); images on a local LVM at `/mnt/photos/library`.
- **Deploy/update:** on LXC 209, `git pull && DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0
  docker compose up -d --build` (BuildKit disabled for this host; migrations auto-run
  on `api` start). SSH: `ssh -i ~/.ssh/proxmox_lxc root@10.0.1.178`. Full runbook:
  `infra/DEPLOY.md`.
- **Do NOT re-run the destructive `import_data.py` on prod** — see the SPEC §11.8
  caveat; use the non-destructive `apply_lr_people.py` / `import_photos.py` instead.
