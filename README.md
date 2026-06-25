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
