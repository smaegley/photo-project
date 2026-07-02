# Family Slide Archive — "Maegley Photo Album"

A private family archive of **1,140 digitized 35mm slides** (1962–1976) shot and
hand-captioned by Steve's father **Wendel**, across 32 Airequipt magazines.
Browsable by **people / place / date / event** with a map + timeline, plus an
admin curation layer. Owner = **Steve** (steve@maegley.com), who appears in the
photos.

## Status — built, frozen, deployed, hardened
- **App is built and live** at **`photos.maegley.org`** (do not treat this as a
  greenfield project). Phase 1 complete: gallery, four facets, map, lightbox,
  Rolls view, full admin/curation layer, undo, two-tier roles.
- **Deployed** on **LXC 209** (`10.0.1.178`) — two Docker containers (FastAPI +
  Caddy) behind a **Cloudflare Tunnel + Cloudflare Access**. See
  `infra/DEPLOY.md` (all steps complete) and `infra/RESTORE.md` for backups.
- **Pre-1.0 hardening done & deployed (2026-07-02, SPEC §10.11):** SQLite WAL +
  `busy_timeout`, throttled `last_login`, image `Cache-Control`, the fixed
  gallery infinite-scroll bug, facet indexes (migration `a6b7c8d9e0f1`), card
  thumbnails, MapLibre code-split. **Phase 1 is closed out; ready for family
  usability feedback.**

## Environments (dev vs prod) — full details in README "Environments"
Two separate boxes, separate DBs. **Prod is behind Cloudflare; dev is NOT** — a dev
box that's unreachable from the LAN is a *binding* problem, never a Cloudflare one.
- **DEV = VM 201 (`10.0.1.121`)**, this repo. Run backend `uvicorn app.main:app
  --host 0.0.0.0 --port 8077` (**must be `0.0.0.0`** or Steve's Mac can't reach it)
  + frontend `npm run dev` (Vite `0.0.0.0:5173`, proxies `/api`→loopback backend).
  Steve views it at **http://10.0.1.121:5173/**. Auth = dev-bypass (admin, no login).
  Leave the servers running after testing.
- **PROD = LXC 209 (`10.0.1.178`)**, `/opt/photo-project`, Docker Compose (api+caddy)
  behind a `cloudflared` tunnel + Cloudflare Access at **https://photos.maegley.org**.
  DB there is the source of truth. Deploy via `git pull && DOCKER_BUILDKIT=0
  COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build` (see `infra/DEPLOY.md`).
  **Never re-run the destructive `import_data.py` on prod** (SPEC §11.8) — use the
  non-destructive `apply_lr_people.py` / `import_photos.py`.

## Sources of truth (read these, in order)
1. **`SPEC.md`** — §§1–9 are the **frozen v1.0** design; **§10 is the build log**
   and is current where it refines §§1–9. This is the authoritative design doc.
2. **`README.md`** — layout, dev run instructions, auth model.
3. **`infra/DEPLOY.md`** — production topology + deploy/update steps.
4. **Content data:** `/mnt/photos/photo-project/handoff/slide_manifest.csv`
   (1,140 rows, 32 mags, all `status=ok`) is the source of truth for slide
   metadata; the importer loads it + 3 curation CSVs into `data/photos.db`.

> The prior planning agent's handoff prose at `/mnt/photos/photo-project/handoff/`
> is superseded — kept for reference only. Trust the manifest, not that text.

## Layout
- `backend/` — FastAPI + SQLite + Alembic (routers: `facets`, `photos`,
  `images`, `admin`; CF-Access JWT auth w/ dev bypass).
- `frontend/` — React + Vite + MapLibre SPA.
- `importer/` — manifest + curation CSVs → DB; build-time geocoder; thumbnails.
- `infra/` — deploy, backup timer/scripts, restore docs.
- **Not in git** (gitignored): `data/photos.db` (live source of truth, backed up
  at the LXC level), the family-data CSVs (`*_firstpass.csv`,
  `people_decisions.csv`), and the slide images under `/mnt/photos/library/`.

## Locations
- `/mnt/photos/library/{slides,index_cards,thumbnails}` — the image library
  (mounted into the app; masters stay on Steve's Mac).
- `/mnt/photos/photo-project/handoff/` — prior-agent handoff package (reference).
- `/home/aiuser/projects/photo-project/` — this working repo.

## Working notes
- **No originals on this machine.** Archival masters stay on Steve's Mac; the app
  works from the `library/` derivatives (it may rotate slides + regenerate
  thumbnails — masters are safe).
- macOS `._*` AppleDouble junk files may be scattered through the folders (safe to
  delete).
- Open / next items live in **SPEC §10.7** (UX polish on Places/Map, caption
  editing, light/dark toggle, person thumbnail picker).
</content>
</invoke>
