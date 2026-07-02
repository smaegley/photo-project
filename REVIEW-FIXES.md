# Pre-1.0 Review Fixes — Implementation Handoff

Implementation spec for the fixes from the 2026-07-02 full-codebase review
(reviewed at `main` @ `3f62eff`). Everything here has already been diagnosed;
**do not re-review, redesign, or refactor beyond what is written**. If a change
doesn't match the code you find, stop and report rather than improvising.

---

## 0. Environment & ground rules (read first)

- **Work here:** `/home/aiuser/projects/photo-project` on dev VM 201. Python venv
  at `.venv` (activate: `. .venv/bin/activate`). This is DEV; prod is LXC 209
  and is **out of scope** — you have no SSH access to it and must not try.
- **Dev servers** (leave running when done):
  - Backend: uvicorn on `0.0.0.0:8077` (must be 0.0.0.0, not loopback).
  - Frontend: `cd frontend && npm run dev` (Vite, 0.0.0.0:5173, proxies `/api`).
  - Steve views the app at `http://10.0.1.121:5173/`.
- **Restarting the backend — known gotcha.** `pkill -f uvicorn` kills your own
  shell (pattern matches its own argv). Kill by port instead:
  ```bash
  PID=$(ss -tlnp | grep ':8077' | grep -oP 'pid=\K[0-9]+' | head -1); kill $PID
  # poll until free:
  ss -tln | grep ':8077'   # repeat until no output
  cd backend && nohup ../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8077 > /tmp/photoapp.log 2>&1 &
  ```
  (Or run it as a proper background task if your harness supports that.)
- **Database safety:** the dev DB is `data/photos.db`. Before the migration
  (Fix 6), make a copy: `cp data/photos.db data/photos.db.bak-review-fixes`.
  Never copy a backup *over* the live DB. **Never run
  `importer/import_data.py`** — it is a destructive from-scratch rebuilder.
- **Git:** create a branch `review-fixes` off `main`. One commit per numbered
  fix (or per batch), short imperative messages matching the repo's existing
  style (`git log --oneline`). Push the branch (SSH auth already works:
  plain `git push -u origin review-fixes`). **Do not merge to `main`** — Steve
  reviews first.
- **Image checks:** ImageMagick `identify` is broken in this environment; use
  Pillow if you need to inspect an image.
- There is **no automated test suite**. Verification is curl + browser + the
  specific checks listed per fix. Playwright + Chromium are installed in the
  venv if you need a screenshot (`~/.cache/ms-playwright/chromium-1223`;
  launch with `executable_path`).

---

## Batch 1 — Concurrency & the gallery scroll bug (highest priority)

### Fix 1: SQLite WAL mode + busy timeout

**File:** `backend/app/database.py`
**Problem:** No WAL journal mode and no busy timeout. Combined with Fix 2's
per-request writes, two concurrent users will produce `database is locked`
errors.

In `_set_sqlite_pragma`, add two pragmas next to the existing foreign-keys one:

```python
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # Readers don't block the writer (and vice versa); required for multi-user use.
    cursor.execute("PRAGMA journal_mode=WAL")
    # Wait up to 5s on a locked DB instead of failing immediately.
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()
```

Notes: WAL is persistent per-database-file but re-issuing the pragma on every
connect is harmless. WAL creates `photos.db-wal` / `photos.db-shm` sidecar
files — expected. The backup script (`infra/db-snapshot.sh`) uses the online
backup API, which is WAL-compatible; nothing to change there.

**Verify:** restart the backend, hit `curl -s localhost:8077/health`, then:
```bash
.venv/bin/python -c "import sqlite3; print(sqlite3.connect('data/photos.db').execute('pragma journal_mode').fetchone())"
```
→ `('wal',)`.

### Fix 2: Stop writing `last_login` on every request

**File:** `backend/app/auth.py`, function `current_user` (lines ~64–79)
**Problem:** Every authenticated request — including each of the ~80 thumbnail
requests per gallery page — runs `user.last_login = now; db.commit()`. That's
a SQLite write per request.

Replace the body after the email/role resolution with a dirty-flag version
that only commits when something actually changed, and refreshes `last_login`
at most every 15 minutes:

```python
from datetime import datetime, timedelta, timezone   # timedelta is new

    now = datetime.now(timezone.utc)
    user = db.query(m.User).filter(m.User.email == email).first()
    if user is None:
        user = m.User(email=email, role=role or "viewer",
                      person_id=settings.dev_user_person_id if dev else None,
                      invited_at=now, last_login=now)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    dirty = False
    if role and user.role != role:  # dev override keeps the dev user as admin
        user.role = role
        dirty = True
    if dev and not user.person_id:  # backfill the dev user's tree link
        user.person_id = settings.dev_user_person_id
        dirty = True
    # last_login is a coarse "seen" timestamp — refreshing it at most every
    # 15 min keeps reads from turning into writes on every request.
    last = user.last_login
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)   # SQLite returns naive datetimes
    if last is None or now - last > timedelta(minutes=15):
        user.last_login = now
        dirty = True
    if dirty:
        db.commit()
    return user
```

⚠️ The naive-vs-aware handling is load-bearing: the `DateTime` column stores
naive values, so `now - user.last_login` without the `replace(tzinfo=...)`
raises `TypeError` on every request. Do not drop it.

**Verify:** restart backend; browse the dev app; confirm requests succeed. Then
check that repeated requests don't bump the timestamp:
```bash
.venv/bin/python -c "import sqlite3; print(sqlite3.connect('data/photos.db').execute('select email,last_login from user').fetchall())"
curl -s localhost:8077/api/me > /dev/null   # repeat a few times
# re-run the sqlite check → last_login unchanged (within the 15-min window)
```

### Fix 3: Cache-Control headers on image responses

**File:** `backend/app/routers/images.py`
**Problem:** Image URLs are mtime-versioned (`?v=<mtime>` from
`queries._version`), so their content is immutable per-URL — but responses
carry no `Cache-Control`, so browsers revalidate ~80 thumbnails per page.

Add at module level:

```python
# Image URLs are mtime-versioned (?v=), so a given URL's bytes never change.
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}
DAY = {"Cache-Control": "public, max-age=86400"}
```

Then:
- `full_image`, `display_image`, and **both** `FileResponse` returns in
  `thumbnail`: add `headers=IMMUTABLE`.
- `face` and `index_card`: add `headers=DAY`.

Rationale for the split (don't "improve" it): face URLs are versioned by
rep-photo + crop box but **not** by file mtime, and card URLs aren't versioned
at all — `immutable` would pin stale copies; one day is safe.

**Verify:**
```bash
curl -sI "localhost:8077/api/thumbnails/Mag1_Slide01.JPG" | grep -i cache-control
# → public, max-age=31536000, immutable
curl -sI "localhost:8077/api/cards/Mag1_card_1.jpg" | grep -i cache-control
# → public, max-age=86400
```

### Fix 4: Gallery fetch error handling + stale-response guard

**File:** `frontend/src/App.jsx`
**Problem (this is the known "infinite scroll stops" bug):**
1. None of the photo fetches have error handling — one failed request leaves
   `loading` stuck `true` forever, and `loadMore`'s guard then blocks all
   future page loads until a full page reload.
2. `loadMore` and `refresh` have no stale-response protection (only the
   filter-change effect has an `alive` flag) — a slow page-N response from an
   *old* filter can overwrite `result` (including `total`) after the new
   filter's page 1 arrived, which can freeze pagination
   (`photos.length >= total` with a stale small `total`).

Changes, all in `App.jsx`:

**(a)** Move the `anchorRef` declaration up so it sits with the other refs/state
(it's currently declared at ~line 169 but used in the effect at ~line 133 —
works due to effect timing, but confusing). Next to it add:

```jsx
const seqRef = useRef(0); // fetch-sequence token: bumped per filter change; stale responses are dropped
```

**(b)** Replace the filter-change effect (currently the `let alive = true` one):

```jsx
// refetch page 1 whenever filters change
useEffect(() => {
  const seq = ++seqRef.current;
  setLoading(true);
  setPage(1);
  setSelectedIds(new Set()); // selection is tied to the current filter view
  anchorRef.current = null;
  api.photos(filters, 1)
    .then((r) => { if (seq === seqRef.current) setResult(r); })
    .catch(() => {}) // keep the previous view; loading reset below re-enables retry-by-scroll
    .finally(() => { if (seq === seqRef.current) setLoading(false); });
}, [filters]);
```

**(c)** Replace `loadMore`:

```jsx
const loadMore = useCallback(() => {
  if (loading || result.photos.length >= result.total) return;
  const seq = seqRef.current;
  const next = page + 1;
  setLoading(true);
  api.photos(filters, next)
    .then((r) => {
      if (seq !== seqRef.current) return; // filters changed mid-flight; drop it
      setResult((prev) => ({ ...r, photos: [...prev.photos, ...r.photos] }));
      setPage(next);
    })
    .catch(() => {})
    .finally(() => { if (seq === seqRef.current) setLoading(false); });
}, [filters, page, loading, result.photos.length, result.total]);
```

**(d)** Wrap `refresh`'s body in try/catch and guard both awaits:

```jsx
const refresh = useCallback(async () => {
  const seq = seqRef.current;
  try {
    const [ev, aev, pl, apl, pe] = await Promise.all([
      api.events(), api.events(false), api.places(true), api.places(false),
      api.people()]);
    if (seq !== seqRef.current) return;
    setEvents(ev); setAllEvents(aev); setPlaces(pl); setAllPlaces(apl);
    setPeople(pe);
    if (isAdmin) api.undoPeek().then(setUndoInfo).catch(() => {});
    const pages = await Promise.all(
      Array.from({ length: page }, (_, i) => api.photos(filters, i + 1))
    );
    if (seq !== seqRef.current) return;
    setResult({ ...pages[0], photos: pages.flatMap((r) => r.photos) });
    setSelectedIds(new Set());
  } catch { /* an admin edit already landed server-side; keep the current view */ }
}, [filters, page, isAdmin]);
```

**(e)** Minor: add `.catch(() => {})` to the six reference-data loads in the
initial `useEffect` (`api.people().then(setPeople).catch(() => {})`, etc.) so a
transient failure doesn't surface as an unhandled rejection.

**Verify:** `cd frontend && npm run build` must pass. In the browser at
`http://10.0.1.121:5173/`: scroll the unfiltered gallery well past 160 photos;
toggle ⚙ Admin on/off and keep scrolling; apply a filter mid-scroll and
confirm the grid resets and keeps loading; do a lightbox edit (admin), close
it, and confirm scroll position/extent is kept and further scrolling loads
more. Note for Fix 7: it also touches `loadMore` — apply that variant if doing
both.

---

## Batch 2 — Database indexes

### Fix 5 + 6: Index the facet-query columns (models + migration)

**Problem:** The only index in the DB is `ix_usage_event_created_at`. Every
facet query filters/sorts on unindexed columns. Fine at 1,140 photos; the
design goal is 10k+.

**Fix 5 — `backend/app/models.py`:** add `index=True` to exactly these six
columns (keeps `Base.metadata.create_all` fresh-DB layouts in sync with the
migration; the default index names `ix_<table>_<column>` then match):

| Model | Column |
|---|---|
| Photo | `date_start` |
| Photo | `place_id` |
| Photo | `magazine_id` |
| PhotoPerson | `person_id` |
| PhotoEvent | `event_id` |
| Contribution | `photo_id` |

e.g. `mapped_column(Date, nullable=True, index=True)` /
`mapped_column(ForeignKey("place.id"), nullable=True, index=True)`.

**Fix 6 — new alembic revision.** Current head is `f5a6b7c8d9e0`
(`f5a6b7c8d9e0_face_regions.py`). Follow the repo's hand-written style: create
`backend/alembic/versions/a6b7c8d9e0f1_facet_indexes.py`:

```python
"""Indexes for the facet-query columns (photo date/place/magazine,
photo_person.person_id, photo_event.event_id, contribution.photo_id).

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
"""
from alembic import op

revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_photo_date_start", "photo", ["date_start"]),
    ("ix_photo_place_id", "photo", ["place_id"]),
    ("ix_photo_magazine_id", "photo", ["magazine_id"]),
    ("ix_photo_person_person_id", "photo_person", ["person_id"]),
    ("ix_photo_event_event_id", "photo_event", ["event_id"]),
    ("ix_contribution_photo_id", "contribution", ["photo_id"]),
]


def upgrade() -> None:
    for name, table, cols in INDEXES:
        op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, cols in INDEXES:
        op.drop_index(name, table_name=table)
```

**Run it (dev):**
```bash
cp data/photos.db data/photos.db.bak-review-fixes   # once, before upgrading
cd backend && ../.venv/bin/alembic upgrade head
```

**Verify:** exactly one head (`../.venv/bin/alembic heads`), and:
```bash
.venv/bin/python -c "import sqlite3; print([r[0] for r in sqlite3.connect('data/photos.db').execute(\"select name from sqlite_master where type='index' and name like 'ix_%'\")])"
```
→ the six new names plus `ix_usage_event_created_at`. Then browse the app —
gallery, filters, rolls all still work. (Prod applies this automatically at
deploy: the container entrypoint runs `alembic upgrade head`.)

---

## Batch 3 — Small fixes

### Fix 7: Skip facet-count queries on page > 1

**Files:** `backend/app/queries.py` + `frontend/src/App.jsx`
**Problem:** `run_query` computes the two facet-count aggregations on every
infinite-scroll page; only page 1's counts are ever used.

Backend — in `run_query`, wrap the two count blocks:

```python
    people_counts, event_counts = [], []
    if page == 1:  # infinite-scroll pages reuse page 1's facet counts
        # ... existing pq block ...
        # ... existing eq block ...
        people_counts.sort(key=lambda x: -x.count)
        event_counts.sort(key=lambda x: -x.count)
    return total, photos, people_counts, event_counts
```

Frontend — **required companion change** or the rail's counts will blank out
when page 2 loads: in `loadMore` (the Fix-4 version), preserve the previous
counts:

```jsx
setResult((prev) => ({ ...r, photos: [...prev.photos, ...r.photos],
  people_counts: prev.people_counts, event_counts: prev.event_counts }));
```

(`refresh` is already safe — it spreads `pages[0]`, which is page 1.)

**Verify:** scroll to page 2+; the People/Events counts in the rail stay
populated and match page 1's values.

### Fix 8: N+1 query in bulk event-add

**File:** `backend/app/routers/admin.py`, `bulk_event`, the `op == "add"` branch
**Problem:** For every already-tagged photo it runs a separate
`db.query(...).first()` inside the loop (select-all 1,140 photos → up to
1,140 extra queries).

Replace the branch's lookup with a single chunked fetch of the rows:

```python
    if body.op == "add":
        existing: dict[int, m.PhotoEvent] = {}
        for ch in _chunks(ids):
            for pe in db.query(m.PhotoEvent).filter(
                    m.PhotoEvent.event_id == e.id,
                    m.PhotoEvent.photo_id.in_(ch)).all():
                existing[pe.photo_id] = pe
        added, confirmed = [], []
        for pid in ids:
            pe = existing.get(pid)
            if pe is not None:
                if pe.source != SOURCE_HUMAN:
                    pe.source = SOURCE_HUMAN
                    confirmed.append(pid)
            else:
                db.add(m.PhotoEvent(photo_id=pid, event_id=e.id, source=SOURCE_HUMAN))
                added.append(pid)
        # _log + commit + return unchanged
```

Behavior must be identical (same `added`/`confirmed` lists, same inverse log).

**Verify (UI):** in admin mode select a few photos, `+ Event` one that some
already have → response counts look right; `↶ Undo` restores; re-check.

### Fix 9: Validate photo_ids on the bulk endpoints

**File:** `backend/app/routers/admin.py`
**Problem:** `bulk_event` / `bulk_person` / `bulk_place` never check the ids
exist; an unknown id becomes an FK IntegrityError → 500.

Add a helper near `_chunks`:

```python
def _check_photo_ids(db: Session, ids: list[int]) -> None:
    found: set[int] = set()
    for ch in _chunks(ids):
        found |= {r[0] for r in db.query(m.Photo.id).filter(m.Photo.id.in_(ch)).all()}
    missing = set(ids) - found
    if missing:
        raise HTTPException(400, f"unknown photo ids: {sorted(missing)[:5]}")
```

Call it in all three endpoints right after the existing
`if not ids: raise HTTPException(400, "no photos selected")`.

**Verify:** `curl -s -X POST localhost:8077/api/admin/photos/event -H 'Content-Type: application/json' -d '{"photo_ids":[999999],"event_id":1,"op":"add"}'` → 400 with
"unknown photo ids", not 500.

### Fix 10: Generic JWT rejection message

**File:** `backend/app/auth.py`, `_verify_access_jwt`
**Problem:** the 401 detail interpolates the raw exception
(`f"Invalid Access token: {exc}"`) — internal library detail goes to the
client. Log it, return a generic message:

```python
import logging
logger = logging.getLogger("app.auth")   # module level

    except Exception as exc:  # noqa: BLE001
        logger.warning("Access JWT rejected: %s", exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid Access token")
```

**Verify:** code review only (dev bypass means this path doesn't run locally).
Confirm the module imports cleanly: restart backend, `/health` still 200.

### Fix 11: Usage-event retention

**Files:** `backend/app/main.py`
**Problem:** `usage_event` grows unbounded.

Prune rows older than 180 days at startup, via a lifespan handler:

```python
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

USAGE_RETENTION_DAYS = 180


@asynccontextmanager
async def lifespan(app: FastAPI):
    # created_at is stored naive-UTC, so compare against a naive cutoff
    cutoff = (datetime.now(timezone.utc) - timedelta(days=USAGE_RETENTION_DAYS)).replace(tzinfo=None)
    from app.database import SessionLocal
    from app import models as m
    db = SessionLocal()
    try:
        n = (db.query(m.UsageEvent)
             .filter(m.UsageEvent.created_at < cutoff)
             .delete(synchronize_session=False))
        db.commit()
        if n:
            print(f"[usage] pruned {n} events older than {USAGE_RETENTION_DAYS}d")
    finally:
        db.close()
    yield


app = FastAPI(title="Maegley Photo Album", version="1.0.0",
              docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
```

⚠️ Same naive-datetime trap as Fix 2 — the `.replace(tzinfo=None)` matters.

**Verify:** restart backend; startup log shows either nothing or a prune line;
`/health` 200; Usage panel still loads.

### Fix 12: UsersAdmin invite form clears even on failure

**File:** `frontend/src/components/UsersAdmin.jsx`
**Problem:** `run()` swallows errors, so `invite()`'s `.then(...)` clears the
email/role/person fields even when the create failed (e.g. duplicate email) —
the admin loses what they typed.

Make `run` report success and gate the reset on it:

```jsx
  async function run(label, fn) {
    setBusy(true); setMsg(null);
    try { await fn(); await load(); setMsg(`✓ ${label}`); return true; }
    catch (e) { setMsg(`⚠ ${e.message}`); return false; }
    finally { setBusy(false); }
  }

  const invite = async () => {
    const e = email.trim().toLowerCase();
    if (!e) return;
    const ok = await run(`invited ${e}`, () =>
      api.createUser({ email: e, role, person_id: personId || null }));
    if (ok) { setEmail(""); setRole("viewer"); setPersonId(""); }
  };
```

**Verify (dev UI, admin):** invite a fake email → form clears. Invite the same
email again → ⚠ message shows and the form keeps its values. Remove the fake
user afterwards.

### Fix 13: Trivial hygiene (one commit)

- `backend/app/routers/facets.py`: `update_me` imports `HTTPException` inline
  (~line 38). Add `HTTPException` to the existing top-level
  `from fastapi import APIRouter, Depends` and delete the inline import.
- `.env.example`: the `LIBRARY_ROOT_HOST` comment says "Mounted read-only into
  the API container" — stale (it's been read-write since commit `4d6e3b9`,
  the app rotates slides and writes derivatives). Reword to: "Mounted
  read-write into the API container at /mnt/photos/library (the app rotates
  slides in place and writes derivatives)."

**Verify:** backend restarts clean; `PATCH /api/me` with a bad theme still 400s:
`curl -s -X PATCH localhost:8077/api/me -H 'Content-Type: application/json' -d '{"theme":"purple"}'`.

---

## Batch 4 — Optional polish (do only if Steve says go)

### Fix 14: Card thumbnails for the Rolls grid

**Problem:** `RollsView` grid tiles and the Lightbox roll-card panel load
original card scans (~3000×4000, multi-MB each) via `/api/cards/`.

- `backend/app/config.py`: add
  ```python
  @property
  def card_thumbs_dir(self) -> Path:
      return Path(self.library_root) / "card_thumbs"
  ```
- `backend/app/routers/images.py`: add alongside `index_card` (reuse `CARD_RE`
  and the Fix-3 `DAY` headers):
  ```python
  @router.get("/card-thumbs/{filename}")
  def index_card_thumb(filename: str, _user=Depends(current_user)):
      """Sized card derivative for grid/panel views; /api/cards stays full-res."""
      if not CARD_RE.match(filename):
          raise HTTPException(400, "bad card filename")
      src = settings.cards_dir / filename
      if not src.exists():
          raise HTTPException(404, "card not found")
      cache = settings.card_thumbs_dir / filename
      derivatives.ensure(src, cache, 640)
      return FileResponse(cache, media_type="image/jpeg", headers=DAY)
  ```
- Frontend: switch `RollsView.jsx` (grid tile) and `Lightbox.jsx` (the
  `lb-rollcard` `<img src>`, NOT its full-size `<a href>`) from `/api/cards/`
  to `/api/card-thumbs/`. `RollDetail.jsx` keeps full-res (it's the reading
  view for Wendel's handwriting).

**Verify:** Rolls tab loads visibly faster; card thumbs appear in
`/mnt/photos/library/card_thumbs/`; clicking a card in the lightbox panel
still opens the full-res original.

### Fix 15: Code-split MapLibre (~1MB out of the main bundle)

`maplibre-gl` is imported by exactly two components: `MapBand` and
`PinEditor`. Both must become dynamic imports or Vite keeps maplibre in the
main chunk.

- `App.jsx`:
  ```jsx
  import { lazy, Suspense } from "react";
  const MapBand = lazy(() => import("./components/MapBand"));
  // at the render site:
  {mapOpen && (
    <Suspense fallback={<div className="mapband" />}>
      <MapBand ... />
    </Suspense>
  )}
  ```
- `PlacesAdmin.jsx`: same pattern for `PinEditor`
  (`<Suspense fallback={null}>` around the `{pinning && ...}` render).

**Verify:** `npm run build` — output now shows a separate maplibre chunk and a
much smaller index chunk (was ~1MB). In the browser, toggling 🗺 Map loads and
renders the map; the 📍 location editor in Manage places still works.

### Fix 16: Caddy compression

`Caddyfile`: first line inside the `:80 { ... }` block, add `encode zstd gzip`.
Cloudflare compresses at the edge in prod, so this mostly helps direct/dev
access — harmless, one line. No way to verify on this VM (containers run on
prod); syntax-check only: `docker run --rm -v $PWD/Caddyfile:/etc/caddy/Caddyfile caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile` — skip if Docker unavailable here.

### Deliberately NOT included (do not do)

- **Non-root user in the API Dockerfile** — needs UID coordination with the
  host mounts on LXC 209 (`./data`, the library LVM mount). Getting it wrong
  breaks prod writes (rotate, derivatives, the DB itself). Deferred to a
  deploy-time task with the Ops agent.
- Places live-count consistency in the filter rail, justified gallery, Slice C
  ingest — out of scope per Steve.

---

## Final checklist (after all batches)

1. `cd frontend && npm run build` — clean.
2. Backend restarts clean; `curl -s localhost:8077/health` → ok.
3. Browser pass at `http://10.0.1.121:5173/`: gallery scrolls past 160+,
   filters combine, map opens, rolls view works, lightbox edit + undo works,
   Manage users invite failure keeps the form.
4. `git log --oneline` on `review-fixes` reads as one commit per fix/batch;
   branch pushed; **not merged**.
5. Leave both dev servers running.
6. Report back: what was done, what was verified, anything that deviated from
   this spec. Deploy to prod is Steve's step (on LXC 209:
   `git pull && DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose up -d --build`;
   the entrypoint auto-runs the Fix-6 migration).
