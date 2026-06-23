# Maegley Photo Album

Private family photo archive — browse ~1,140 digitized 35mm slides by people,
place, date, and event, with a map and timeline. See `SPEC.md` (frozen v1.0).

## Layout
- `backend/` — FastAPI app (`app/`), Alembic migrations (`alembic/`).
- `importer/` — data pipeline: `import_data.py`, `dates.py`, `geocode.py`,
  `make_thumbnails.py`; review reports in `importer/review/`.
- `*_firstpass.csv` — curation files the importer reads. `people_decisions.csv`
  is the record of Steve's people in/out calls.
- Images live outside the repo at `/mnt/photos/library/{slides,index_cards,thumbnails}`.

## Build / run (dev)
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt

# 1. schema (Alembic owns it)
cd backend && alembic upgrade head && cd ..

# 2. load data: manifest + curation files -> SQLite (data/photos.db)
python -m importer.import_data

# 3. geocode places (fills gazetteer lat/lon) — once, or after gazetteer edits
python -m importer.geocode

# 4. pre-generate thumbnails -> library/thumbnails/
python -m importer.make_thumbnails

# 5. run the API (http://127.0.0.1:8077/api/docs)
cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8077
```

Re-running the importer is idempotent (rebuilds content tables; leaves
users/invites/contributions). It regenerates `importer/review/*.csv` — these are
reports, not inputs.

## Auth
Behind Cloudflare Access in production (set `CF_ACCESS_*` in `.env`). With those
unset, the app runs in local-dev mode as `DEV_USER_EMAIL` (admin). SPEC §6.3.
