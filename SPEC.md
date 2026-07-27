# Family Slide Archive — Build Specification

**Status:** ✅ **FROZEN v1.0** (design) + **§10 build log** + **§11 Phase-2 ingest design**. Phase 1 built, **deployed and live** at `photos.maegley.org` (LXC 209 — see `infra/DEPLOY.md`). Since then: UI polish + dark mode (incl. dark map) + hybrid usage tracking + **face thumbnails** + infra (§10.8–10.10, `3691ac1`), then a **pre-1.0 hardening batch** — SQLite concurrency, the gallery scroll-bug fix, facet indexes, and polish (§10.11, `main` @ `1ae672b`, **deployed**). **Phase 1 is closed out and ready for family usability feedback.** **§10 is the source of truth where it refines §§3–9.** **§11** is the design for ingesting non-slide photos — its slide-side foundations (storage/serving, metadata reader, LR-people overlay) are **built** (§11.8); **§12 (2026-07-17): Phase 2a "Scanned Photos" (origin=scan) — BUILT & VERIFIED ON DEV; prod rollout (slice 7) pending (§12.12).** Remaining v1 (minor): wider roll-card view in the lightbox (§10.7).
**Version:** 1.0 frozen + §10 build log + §11 ingest design + §12 Phase-2a build spec · **Frozen:** 2026-06-21 · **Build log:** 2026-06-23 · **§11:** 2026-06-28 · **§10.8–10.10:** 2026-07-01 · **§10.11:** 2026-07-02 · **§10.12:** 2026-07-04 · **§10.13:** 2026-07-06 · **§10.14:** 2026-07-07 · **§10.15:** 2026-07-07 · **§12:** 2026-07-17
**Supersedes:** the prior planning agent's handoff package at `/mnt/photos/photo-project/handoff/` (kept for reference only; its prose lags the project — trust the manifest, not that text).

> **How to read this doc:** Sections with filled content are decided. `> OPEN:` callouts mark decisions we still need to make together. The data model (§3) anchors everything; we fill it first.

---

## 1. Vision & users

A private, invite-only web app for browsing a digitized family photo archive. It begins as **~1,150 35mm slides (1962–1976)** that Steve's father **Wendel** shot and hand-captioned (people, places, dates, subjects) on index cards, OCR'd into a manifest. The app turns that into a gallery that the family can explore by **people, place, date, and event**, with a **map** and a **timeline**.

- **Audience:** Steve's family — **≤15 users**, each with their own account (no shared login).
- **Access:** **email-invite only**, via **Cloudflare Access** (§6.3). Steve adds an address to the Access policy; Cloudflare handles login (email OTP / Google). No public signup, no passwords, no custom magic-link code. Fully private.
- **Primary mode:** **view + download.** Limited **contribution** (identify people/places, add notes) is allowed for designated users and can be farmed out to family for review later (see §3.5, §6.3).
- **Long-term vision:** fold in Steve's wider personal photo library (scanned + digital), growing to **thousands → 10k+** photos, with automated metadata enrichment (§5).

---

## 2. Scope & phasing

### Phase 1 — Slide archive (ML-free) ← **the build**
The 1,150 slides already carry ground-truth metadata from Wendel's manifest, so **no machine learning is needed**. Phase 1 = import that data + the final photo folder, then deliver the gallery and the four facets.

**Phase 1 done when:**
1. Manifest + final photo folder imported into the DB; thumbnails generated.
2. App reachable; Steve can invite a user by adding their email to the Cloudflare Access policy; that user signs in via Cloudflare Access (§6.3).
3. Gallery grid renders; all four facets (§4) work and AND-combine.
4. Map plots geocoded places; selecting a region filters the grid.
5. Photo-detail view shows the image, Wendel's caption/notes, people, place, date, events; download works.
6. A designated contributor can add/correct a person, place, or note.

### Phase 2 — Library expansion + enrichment (designed-for, deferred)
Fold in the broader digital/scanned library and add the **enrichment pipeline** (§5: face recognition, scene/activity hints, geocoding). Deferred until (a) there's a library to ingest and (b) a bigger ML-capable machine exists. Phase 1's schema and storage seams are built to accept it without a rewrite.

> **OPEN:** any Phase-1 feature beyond the six above? (e.g. shared/curated albums, favorites, slideshow mode, per-photo comments.)

---

## 3. Data model

> The backbone is a **flat, date-ordered stream of photos with rich tags** — this works identically for the slides and for Steve's future digital library. The four facets (§4) are the primary navigation; there are **no user-built albums**. Magazines are an *optional* provenance overlay (§3.7), not the spine.

### 3.1 Source of truth (Phase 1 input)
`/mnt/photos/photo-project/handoff/slide_manifest.csv` — 1,140 rows, 32 magazines, fully captioned. Columns:
`seq, magazine, slide_in_mag, organized_file (Mag<N>_Slide<NN>.JPG), original_file, mag_subject, mag_date_span, card_caption, date_raw, people, place, validation, status, notes`

The app does **not** read the CSV at runtime; a re-runnable **importer** loads it into the DB. The importer also consumes the curated first-pass files we built: `people_canon_firstpass.csv`, `places_gazetteer_firstpass.csv`, `events_firstpass.csv`.

**Asset paths (production layout under `/mnt/photos/library/`, mounted read-only by the app):**
- **Slide images:** `library/slides/Mag<N>/Mag<N>_Slide<NN>.JPG` — 1,140 files, 32 per-magazine subfolders, 1:1 with the manifest `organized_file`. (Moved from the prior agent's working `PreProcess_Slides/` after organizing was complete.)
- **Index-card images:** `library/index_cards/Mag<N>_card_<n>.jpg` — 65 files; every roll has ≥1 card, some have 2 (`_card_1`, `_card_2`). Feed the "Rolls" view (§3.7).
- **Thumbnails:** `library/thumbnails/` — generated by the importer (not masters).

### 3.2 Core entities
- **photo** — one row per image. `id, source_file (Mag<N>_Slide<NN>.JPG for slides; null-able origin for future imports), caption (= card_caption), date_start, date_end, date_precision, date_raw (verbatim display label), place_id (FK, nullable), magazine_id (FK, nullable), slide_in_mag (nullable), original_subject (= mag_subject), validation (match|uncertain), notes`.
- **person** — `id, canonical_name, father_id, mother_id, spouse_id, notes` (see §3.3).
- **person_alias** — `id, person_id, alias` (maps note spellings → canonical person).
- **photo_person** — join; carries **provenance** + `uncertain` flag (§3.5).
- **place** — gazetteer entry; `id, canonical_name, lat, lon, precision (exact|landmark|city|region|unknown), region` (see §3.4).
- **event** — `id, name`; extensible vocabulary (§3.6).
- **photo_event** — join (multi-value); carries provenance.
- **magazine** — roll/album entity; `id, number, title, span_label, date_start, date_end, slide_count, card_image_paths` (§3.7).
- **user** — `id, email, display_name, role (admin|contributor|viewer), invited_at, last_login`.
- **invite** — `id, email, token, expires_at, accepted_at`.
- **contribution** — edit log of human changes (§3.5).

### 3.3 People — a derived family tree
**Individuals only** (no group/family entities); friends and vague collectives ("family", "the gang") are out of scope. Scope = **immediate + extended family**: parents, grandparents, children, and (added 2026-06-21) **aunts, uncles, cousins, siblings**.

Each person stores **`father_id` / `mother_id` / `spouse_id`**; the app **derives** the relationship to Steve (root) — parent, grandparent, sibling, aunt/uncle (blood or by marriage), cousin — so nothing is hand-labeled and a family-tree view is possible later. The **people filter is grouped by derived relationship** and shows only people who actually have photos.

Aliases collapse note spellings (Stephen/Stevie→Steve; Herb/Herbert→Herb Beck; "the Becks"→both Beck grandparents). Group mentions in the notes resolve to **named individual members**; a bare group mention (e.g. "Hollenkamps" with no list) tags **the heads (aunt+uncle) only** — cousins tagged where explicitly named.

Canon = 25 people across 3 branches (maternal Becks, paternal Maegleys, plus the two aunts' families). Seed file: `people_canon_firstpass.csv`.
> Watch-outs encoded as distinct aliases: two **Nicks** (Uncle Nick vs cousin Nickie), three **Karens** (sister / cousin Karen Moebius / Wendel's cousin); manifest "Mary Catherine" tags are a **friend**, not Aunt Mary Catherine Moebius (don't auto-map).

### 3.4 Places — gazetteer + precision
A **`place` gazetteer** (one entry per real location) with `lat/lon`, a **`precision`** flag, and `region`. The importer normalizes/dedups the 180 raw strings (→ ~170 first-pass; more merges pending review) so each location is geocoded once and the map clusters cleanly.
- **Coarse "state-only" places** (e.g. "Minnesota", 86 rows) → plotted at the **state centroid, flagged approximate**, hideable on the map (precision `region`).
- **Un-locatable** (~47 rows: "unknown", "…TBD") → **no map pin**, precision `unknown`; still browsable, label still shown.
- **lat/lon population:** a **geocoder (OSM/Nominatim) run at build time** + hand-review of obscure names; the two home addresses pin exactly. Output is the reviewable in-repo gazetteer. Seed file: `places_gazetteer_firstpass.csv`.
- **In-app location tagging** (the genuine unknowns): an admin drops a pin **at the place level** (fixing all photos that share it) or reassigns a single photo; edits are stored `human-confirmed` and **promote precision**. Permission **admin-only to start**, role-gated to open later.

### 3.5 Provenance, confidence & contributions (the two-tier metadata rule)
Slides = **authoritative** (Wendel's notes). Future digital photos = **ML-derived** (lower confidence). Every people/event/place tag carries a **`source`: `manifest` | `auto-suggested` | `human-confirmed`** (+ an `uncertain` flag on people). The UI distinguishes "confirmed" vs "suggested"; a human confirmation **promotes** a suggestion. Slide manifest tags import as `manifest` (treated as confirmed).

**Contributions** are light, gated edits (identify/correct a person, set/refine a place, add a note, confirm/reject a suggested event), captured in the `contribution` log. **Resolved (§9):** `contributor`-role edits apply **immediately** + are logged (no approval queue); **location-tagging is admin-only** to start, role-gated to open later.

### 3.6 Events — suggested → confirmed
Not a manifest column, so the importer **derives suggestions from captions/subjects** and tags them `auto-suggested` for human confirm/reject (bulk-confirm in the UI). **Multi-value** per photo (414 rows match >1), vocabulary **extensible**.
**Seed vocabulary (locked 2026-06-21):** Milestones — Birth, Baptism, Adoption, First Communion, Graduation, Wedding, Funeral, Birthday, Milestone; Holidays — Christmas, Easter, Thanksgiving, Independence Day; Life — Vacation/Trip, Move/New Home. ("School" dropped — place/time cover it; Wedding/Funeral seeded at 0 for the future library.) Seed file: `events_firstpass.csv` (892/1,140 auto-suggested).

### 3.7 Magazines — optional roll/album overlay
Wendel pre-organized the slides into 32 themed rolls. Each is a **`magazine`** record (`number, title from mag_subject [editable], span_label, dates, slide_count, index-card images`); a photo links via nullable `magazine_id` + `slide_in_mag`. Digital photos leave these null and live in the same stream.
- A **"Browse by roll" view** lights up for slide-origin photos and shows Wendel's **original index-card image**.
- **Card-line → image:** v1 renders the card image beside an **ordered, clickable list of that roll's transcribed captions** (click a line → jump to its slide; hover highlights). Clicking directly on the handwriting (per-line hotspots) = Phase 2.

### 3.8 Dates with precision
The importer parses `date_raw` → **`date_start` + `date_end` + `date_precision`** (`day|month|season|year|approx`) and keeps **`date_raw` verbatim as the display label** (approximate dates read as approximate; no fake-precise dates shown). The timeline (§4.1) filters by **overlap** (`date_end ≥ rangeStart AND date_start ≤ rangeEnd`); sort = `date_start` with **`(magazine, slide_in_mag)` tiebreaker** (preserves card order; supersedes the prior spec's "+N seconds" hack).
**Season → months:** Spring=Mar–May · Summer=Jun–Aug · Fall=Sep–Nov · **Winter=Dec–Feb (crosses year)**. **Holidays:** Christmas=Dec 25 · Thanksgiving=4th Thu Nov · Easter=that year's date · Memorial Day=last Mon May · "Early YYYY"=Jan–Apr (precision `approx`). 1 empty `date_raw` → fall back to magazine span.

### 3.9 Importer mapping rules (input encodings → entities)
The manifest + three curation files carry conventions the importer must apply **deterministically**:
- **Photo row:** 1 manifest row → 1 `photo`. `source_file` = `organized_file`; `caption` = `card_caption`; `original_subject` = `mag_subject`; `validation` = manifest `validation` (`match`→confirmed, `uncertain`→keep flag). All 1,140 import (`status` is always `ok`).
- **People field** (`people` column, 491 non-empty rows): split on `;`; trim. A trailing **`?` ⇒ `uncertain=true`** on that `photo_person`. Strip parenthetical role hints (`(Dad)`, `(Mom)`). Resolve each token to a `person` via `person_alias`; **group forms** (`(the Becks)`, `Hollenkamps`) expand to their member persons (heads-only unless members are named — §3.3). Tokens that resolve to **out-of-scope** people (friends, e.g. "Mary Catherine", "the Bowmans") are **dropped** (photo stays). Import source = `manifest`.
- **people_canon_firstpass.csv** → `person` + `person_alias`. Import `person_id, canonical_name, father_id, mother_id, spouse_id, notes` only where `include=Y` (all 25 currently). **`relationship_to_steve` is review-only — do NOT store it** (relationship is derived, §3.3). Explode `aliases_seen` (`;`-separated, includes group forms) into `person_alias` rows. `approx_photos` is informational.
- **places_gazetteer_firstpass.csv** → `place`. Map each manifest `place` string to a `place_id` by matching it against the gazetteer's **`raw_variants_merged`** (`|`-separated). `lat/lon` are **currently empty for all 170** — import as null; a separate **build-time geocode pass** (§3.4) fills them. 5 manifest rows have no place → `place_id` null. `merge_candidates_note` flags pairs still pending human merge.
- **events_firstpass.csv** → `photo_event`. Join on `organized_file`; split `suggested_events`; each → a `photo_event` with `source=auto-suggested` (892/1,140 rows). `event` vocab rows seeded from §3.6.
- **Magazines:** derive 32 `magazine` rows from the manifest (`number`, `title`=`mag_subject`, `span_label`=`mag_date_span`, `slide_count`, dates from §3.8); attach card images by `Mag<N>_card_*` filename.
- **Re-runnable:** importer is idempotent (upsert by natural keys: `photo.source_file`, `person.id`, `place.id`, `event.name`) so it can be re-run after curation-file edits without duplicating.

---

## 4. The four facets

All facets **AND-combine** (`time ∩ people ∩ events ∩ map-region`), each shows **live counts** for the current filtered set, each has an "All/reset," and the result grid updates immediately.

### 4.1 Timeline range
Dual-handle slider over ~1962–1976 (extends as the library grows) with an "All dates" reset. Filters by **overlap** on `date_start`/`date_end` (§3.8), so vague dates appear whenever the range touches them. Optional snap to year/month.

### 4.2 People (multi-select)
Grid of people; select one or several to filter. Phase 1 filters by **name** (manifest authority); no face detection in Phase 1. Each person's thumbnail = an **admin-chosen `representative_photo_id`** (§9 resolved), falling back to the first photo they appear in until set.

### 4.3 Events
Selectable chips/checkboxes from the events vocabulary (§3.6). Multi-select.

### 4.4 Map with region selection
Map (MapLibre) with a pin/cluster per geocoded place; click a location **or draw a rectangle** (bounding box) to filter the grid to that region. Requires lat/lon, geocoded from `place` via the **in-repo gazetteer** (place-name → lat/lon) so results are deterministic and reviewable. First-pass gazetteer exists (`places_gazetteer_firstpass.csv`, 170 entries); it still needs the variant-merge + build-time geocode pass (§3.4).

---

## 5. Enrichment pipeline (Phase 2 — designed-for, deferred)

Offline/batch, runs on a future ML-capable machine, **separate from the serving app**; its only output is data + thumbnails written to the same DB/storage. For each new (non-manifest) photo: extract EXIF date/GPS → **face detection + recognition** (cluster, propose names) → **scene/activity hints** → **geocode** → write tags as `ml-suggested`. Events stay **suggested → human-confirmed**. Nothing in Phase 1's serving stack carries ML weight.

---

## 6. Backend & auth

**Chosen to match Steve's existing stack** (solar-tracker / migraine-tracker on Proxmox).
- **Stack:** **FastAPI (Python 3.12) + SQLite**, **alembic** migrations, `deploy.sh` — same as solar/migraine.
- **API:** REST/JSON. Core endpoint: a **faceted query** taking time range + people + events + map bbox, returning the photo page + per-facet live counts. Plus: photo detail, image/thumbnail serving + download, people/events/places lists, contribution writes, admin (user/role mgmt, person thumbnail, location tagging).

### 6.3 Auth & roles — Cloudflare Access (no custom login)
Put the app **behind Cloudflare Access** (Zero Trust), exactly like migraine-tracker. This replaces the earlier magic-link/email-provider plan entirely.
- **"Invite by email" = add the address to the Access policy.** Cloudflare handles login (email OTP / Google). No password, magic-link, or transactional-email code to build.
- The app trusts the signed **`Cf-Access-Jwt-Assertion`** header, reads the **verified email**, and maps it to a `user` + role. (Verify the JWT against the Access certs; never trust the header un-verified.)
- Roles (in our DB, keyed by email): `admin` (Steve — manage users/roles, edit anything, location tagging, set person thumbnails), `contributor` (view + immediate metadata edits, logged), `viewer` (view + download).
- *Later option:* an in-app admin page calls the Cloudflare API to manage the Access allowlist, for an in-app "invite" feel.

---

## 7. Frontend & look-and-feel

- **Name:** **Maegley Photo Album.**
- **Stack:** **React SPA**, **MapLibre** for the map; static build **served by Caddy** (the same reverse proxy that fronts the API).
- **Aesthetic:** **clean, modern, minimal — NOT skeuomorphic, NOT an "archive" look.** Reference: **Google Photos** for the gallery; Apple-like in that the chrome stays put and content updates in place. **Desktop-first** (Phase 1 is not a mobile/phone experience).

### 7.1 Interaction model — fixed app shell
The **header, date slider, and left filter rail are fixed**; the **gallery pane is the only region that scrolls/updates** (Google-Photos-style justified grid with a live count). The page itself doesn't scroll the chrome away.

### 7.2 Layout
- **Header (top):** "Maegley Photo Album", settings, user menu.
- **Date filter (top, fixed):** the dual-handle timeline slider (§4.1) + "All dates".
- **Map (collapsible band, docked under the date filter):** **hidden by default** (not emphasized); a `🗺 Map` toggle opens a full-width map band; **`📌 pin`** keeps it open so a user can *browse by map*; draw-box / pin-click filters the gallery live (§4.4). Otherwise it tucks away after a selection.
- **Left rail (collapsible):** filter panels — **People** (grouped by derived relationship), **Events**, **Places** — each a slide-out sub-panel; plus a global reset.
- **Gallery (main, scrolls):** the justified photo grid; live count of the filtered set.
- **View toggle:** **Gallery | Rolls** — "Rolls" is the browse-by-magazine view (§3.7) with the index card + clickable caption lines.

### 7.3 Photo view (lightbox)
Opening a photo gives a large view with prev/next and download. **Dad's notes are a right-hand slide-out panel** — clearly present but **not front-and-center** (Steve's call): caption, `date_raw` label, people, place, events, and a link to its Roll. Slides away to view the photo unobstructed.

- Steve wants to **iterate the design** and not be limited by a template; this is the agreed v1 baseline to build and refine against.

> **OPEN (minor):** light vs dark default (lean: light, clean); exact typography/palette — to tune during build.

---

## 8. Hosting & deployment

Matches Steve's established Proxmox pattern.
- **Runtime:** **Docker Compose on a Proxmox LXC** (per solar/migraine; build on LXC 207 — dev VM 201 lacks the compose plugin). Two services: the **FastAPI app** and **Caddy**.
- **Domain / TLS:** **`photos.maegley.org`**, served by **Caddy** with the **`caddy-dns/cloudflare`** image (automatic TLS via the Cloudflare DNS token), exactly like `solar.maegley.org`.
- **Access:** **Cloudflare Access** (Zero Trust) in front, allowlist = the family's emails (§6.3).
- **Data & photos:** SQLite on a mounted `/data` volume; the image library mounted from **`/mnt/photos`**. Thumbnails pre-generated into the data volume.
- **Backups:** SQLite **snapshot + offsite** scripts on a **systemd timer** — reuse the migraine-tracker `infra/` pattern (`db-snapshot.sh`, `backup-offsite.sh`, `.service`/`.timer`, `RESTORE.md`).
- **Secrets:** `.env` (committed `.env.example`): `CLOUDFLARE_API_TOKEN`, Access JWT audience/team domain, paths.
- **"Move to Cloudflare later"** = the edge already is Cloudflare (Access + DNS, optionally a Tunnel) — no app rewrite needed; FastAPI stays on the LXC. Heavy ML (Phase 2) never runs on the serving box.

---

## 9. Open decisions (rollup)

- §2 Phase-1 feature set beyond the core six (favorites? slideshow? per-photo comments?) — revisit during build
- §7 light vs dark default + typography/palette — tune during build

**Resolved (2026-06-21):** people tree + scope (§3.3); places gazetteer + precision + in-app tagging (§3.4); provenance + **immediate** contributions w/ edit log (§3.5); events vocabulary + suggest→confirm (§3.6); magazine overlay + card-line view (§3.7); date parsing + season conventions (§3.8); **no user-built albums**; look-and-feel (§7); **person thumbnail = admin-chosen `representative_photo_id`**; **stack = FastAPI + SQLite + alembic / React + MapLibre / Caddy+Cloudflare-DNS / Cloudflare Access auth / Docker Compose on Proxmox LXC** (§6, §8); domain **`photos.maegley.org`**.

---

## 10. Build log & post-freeze decisions (2026-06-23)

> The frozen sections above are the *design*. This section records what was **built** and the **decisions made during build** that extend/refine them. Where they differ, this section is current.

### 10.1 Built and working (Phase 1, on dev VM 201 @ 10.0.1.121)
- **Importer** (`importer/`): manifest + 3 curation files → SQLite. 1,140 photos, 32 magazines, 27 persons, 67 aliases, 170 places, 15-event vocab, people/event tags. Idempotent. Date parser covers all 120 `date_raw` forms (§3.8).
- **Build-time geocoder** (`importer/geocode.py`): 162/170 places geocoded (1,080/1,140 photos mappable); state-code + DC-override guards.
- **Backend** (`backend/app/`, FastAPI + SQLite): faceted query w/ live counts (self-facet-excluded), photo detail, people (derived, grouped), events, places, magazines, image/thumbnail/card serving (path-guarded), Cloudflare-Access JWT auth + dev bypass. Alembic at head (7 migrations, head `a6b7c8d9e0f1`).
- **Frontend** (`frontend/`, React + maplibre): fixed shell, dual-handle date slider, collapsible map band, filter rail (people/events/places), justified-ish gallery w/ infinite scroll, lightbox, Gallery|Rolls toggle, RollDetail (card + clickable captions).
- **Admin layer** (built 2026-06-22/23) — see §10.3.
- **Code in version control:** `github.com/smaegley/photo-project` (**private**). Repo is **code-only**: `.gitignore` excludes the DB (`data/`), all family-data CSVs (`/*.csv`), `node_modules`/`.venv`/`dist`, caches, secrets. Images never in repo (live under `/mnt/photos`). **Before any future public flip, scrub PII** (steve@maegley.com in `config.py`/`geocoding.py`, `ROOT="steve"` in `family.py`, family names in `CLAUDE.md`/`SPEC.md`).

### 10.2 People — per-viewer rooting (refines §3.3)
Relationships are no longer rooted only at Steve. **`user.person_id`** (FK→person, nullable) links an account to its tree node; the People filter derives relationships **relative to whoever is logged in** ("Self" = them). An **unlinked** viewer sees the canonical Steve-rooted tree but with **no "Self"**. The deriver (`family.py`) takes a `root` param + `mark_self` flag. Family shapes decided for the invite flow: **current wife** = `spouse_id` ↔ Steve ("Spouse"); **ex-wife** = the (future) kids' `mother_id` only, **not** a spouse edge → reads as extended family, never "Spouse", valid login; **kids** = `father_id`=Steve, `mother_id`=ex-wife → "Child". Every family **user is a person node** and may have **0 photos** (exists in the tree, lights up when photographed). The admin sets the person + relationship **at invite**. Invite *screen* deferred (auth still dev-bypass); the data model supports it now.

### 10.3 Admin / contributions — built (implements §3.4, §3.5)
All admin write endpoints under `/api/admin/*`, audit-logged to `contribution`. Built:
- **Event vocabulary:** create / rename / **merge** (re-points all tagged photos, de-dupes) / delete. UI: "Manage events" modal (search, two-step merge confirm).
- **Place vocabulary:** create / rename / edit (region, precision, lat/lon) / **merge** / delete. UI: "Manage places" modal (search, precision dropdown, "no pin" badge).
- **Bulk tagging:** apply/remove an event or person, set/clear a place across a selection. UI: gallery **selection mode** + action bar, "select all in filter" (`GET /api/photos/ids`).
- **Per-photo editing (lightbox, admin):** add/remove people & events (chips), set/clear place. **Show-roll-card** reveal under Events (refines §3.7/§7.3). Lightbox also got **fit-to-window + zoom + pan**.
- **Photo rotate:** `POST /photos/{id}/rotate` rotates the slide JPEG on disk + regenerates its thumbnail (masters safe on Steve's Mac). Image URLs are **mtime-versioned** (`?v=`) so the browser shows the new orientation immediately.
- **In-app geocode lookup:** `GET /api/admin/geocode?q=` (Nominatim, importer's guards) backs a "🔍 Look up coordinates" button in the **map pin editor** (`PinEditor`, maplibre click/drag) — admin confirms before save. New-place creation does **not** auto-geocode.
- **Undo (repeatable LIFO stack):** every op records an **`inverse`** JSON payload; `POST /api/admin/undo` replays the latest not-yet-undone op (`contribution.inverse`/`undone` columns). Events & places **restore with their original id** so overlapping inverses stay valid. **Only edits made after 2026-06-23 are undoable.** Narrow accepted gap: delete highest event-id → create new event grabbing that id → undo collides.

### 10.4 Map — dynamic (refines §4.4)
Selecting place(s) re-frames the map: **fly-to + zoom** for one (zoom by precision), **fit-bounds** for several. Selected pins render above unselected (z-index). **Region shading:** a `precision='region'` selection matched to a US state is **outlined + lightly shaded** (bundled `frontend/public/us-states.geojson`); unmatched regions (e.g. "Canada") keep a pin.

### 10.5 Two-tier admin — DECIDED, build with auth (refines §3.5, §6.3)
Privilege separation so cascading vocabulary changes need higher rights than per-photo tagging. Maps onto existing roles:
- **contributor** = per-photo + bulk **tagging** (add/remove event/person, set/clear place, rotate, geocode lookup) — safe, hand-reversible.
- **admin** = vocabulary (create/rename/merge/delete events & places), the **pin editor**, and **undo** (can reverse ops a contributor couldn't perform).
- **viewer** = read + download.
Frontend gates by role (hide Manage-events/places, Undo, pin editor for non-admins). Enforced **at the deploy/auth phase** (roles only real once Cloudflare Access maps emails→roles). Tier naming TBD (contributor vs editor/curator).

### 10.6 Assets / corrections
- **Index cards = 64** (32 rolls × 2), not 65: `Mag10_card_extra.jpg` was a duplicate of `Mag10_card_2.jpg` (removed). Steve cleaned all 64 (rotate/crop/white-balance) on his Mac; originals archived at `library/index_cards_original_raw/`.

### 10.7 Open / next (post-freeze)
- **Deploy:** ✅ done — live on LXC 209 behind Cloudflare Tunnel + Access (`infra/DEPLOY.md`).
- **GitHub push:** ✅ done (`origin/main`).
- **UX polish pass** — ✅ mostly done (§10.8): region pin-vs-shading redundancy ✅, un-pinned places in the rail filter ✅, caption editing ✅, person `representative_photo_id` picker ✅, light/dark ✅. **Still open (minor):** the roll-card scan in the lightbox notes panel is narrow (340px) — a wider/lightbox view of Dad's card would read better.

### 10.8 UI polish + dark mode + usage tracking (built 2026-07-01, branch `ui-polish`)
Batch after a screenshot UI/UX review (all P1/P2 + the two features):
- **P1 fixes:** lightbox right-nav arrow now returns to the edge when the notes panel is collapsed (`.lightbox.notes-hidden`); the filter rail is hidden in Rolls view (it only applies to the gallery); the admin vocabulary/users/usage/undo buttons are consolidated into one **"Manage ▾" dropdown** (header was over-crowded).
- **P2:** inline **caption editing** in the lightbox (admin; `POST /photos/{id}/caption`, undoable); **representative-photo** picker (a ★ on each tagged person chip; `POST /people/{id}/representative`, admin, undoable); a top **loading bar** on the gallery; the Places filter now lists **all** places incl. un-pinned (was mappable-only); the map **suppresses a region place's centroid pin when it's selected/shaded**.
- **Dark mode** (§7 open item, resolved): per-user `user.theme` (`light|dark|system`, migration `e4f5a6b7c8d9`) via `GET/PATCH /api/me`; a ☾/☀ header toggle; `styles.css` fully tokenized with a `[data-theme="dark"]` palette + `color-scheme`. Default `system`. **Dark map:** the MapLibre OSM raster canvas is inverted via CSS filter in dark mode (`invert(1) hue-rotate(180deg)`) — keeps OSM's strong line/label contrast (CARTO's dark basemap was too faint); markers/controls are DOM siblings of the canvas so they stay correct, and the control buttons are theme-tokenized.
- **Usage tracking** (hybrid, Steve's choice): a minimal `usage_event` table + fire-and-forget `POST /api/usage` beacon (views on lightbox-open, downloads, debounced searches) + an admin **Usage** panel (`GET /api/admin/usage/stats`: view/download totals, most-viewed photos, active users, recent activity). Login/traffic analytics stay in **Cloudflare** (Zero Trust Access logs + Web Analytics) — the in-app log only covers what CF can't see inside the SPA.
- **Color note:** the visible blue/magenta casts on many slides are **source data** (degraded 1962 film, best-effort corrected in Lightroom) — verified not an app/color-management bug (originals carry a correct sRGB profile). Not app work.

### 10.9 Face thumbnails + People-filter grid + usage detail (2026-07-01, branch `ui-polish`)
- **Face thumbnails from Lightroom regions:** `mwg-rs` face regions carry the box (`stArea` x/y/w/h), not just the name — 644 named regions across all 20 tagged people. `apply_lr_people` now stores the box on `photo_person` (migration `f5a6b7c8d9e0`: `region_x/y/w/h`) and **auto-picks each person's representative** as their largest named region (15 people covered). `GET /api/faces/{person_id}` serves a padded square face crop (`derivatives.face_thumb`, cached in `library/faces/`); `PersonOut.face_url` exposes it. The lightbox ★ overrides the auto-pick (crops to that photo's region for the person). A **manual box-drag editor** (⛶ on the person chip → `FaceCropEditor`) sets the face crop for people ★'d on a photo with no Lightroom region (`POST /people/{id}/face-region`, undoable); the face URL/cache is versioned by rep-id + box so edits bust both caches (`derivatives.face_version`).
- **People filter = face grid + list toggle:** circular face thumbnails (fallback initial) grouped by relationship, ▦/☰ toggle (remembered in `localStorage`), selected = accent ring.
- **Usage panel detail:** most-viewed rows show a **thumbnail** (hover-enlarges); a **By user** table gives per-user view/download subtotals alongside the all-user totals.

### 10.10 Infra / ops (2026-07-01)
- **Derivative pre-warm:** `backend/app/derivatives.py` centralizes thumbnail/display/face generation behind one staleness predicate (missing / zero-byte / older-than-source) — used by the server (lazy self-heal) *and* the batch tool. `backend/app/prewarm.py` (`docker compose exec api python -m app.prewarm`, dev wrapper `importer/make_thumbnails.py`) pre-warms thumbnails + display **as a deploy step after a slide rsync**, so browsing never triggers the lazy regeneration burst. The mtime self-heal is preserved (a re-exported slide under the same name refreshes automatically). Lives in the backend package so it runs inside the prod container (`importer/` isn't in the image). See `infra/DEPLOY.md`.
- **Refresh dev from prod** (`scripts/load-prod-snapshot.sh` + `infra/refresh-dev-from-prod.md`): now that prod is the source of truth, a **load-only** (prod→dev, never the reverse) script backs up the dev DB, integrity-checks a prod snapshot, swaps it in, and `alembic upgrade head`s (so a dev branch's newer migration applies on real data). Reuses prod's existing `infra/db-snapshot.sh` gz snapshots; prod pushes the snapshot to dev (LXC 209 → VM 201 works; the reverse isn't set up).
- **Environments** (dev VM 201 `10.0.1.121` vs prod LXC 209 `10.0.1.178`, Cloudflare only on prod): documented in `README.md` "Environments" + `CLAUDE.md`. Dev backend **must** bind `--host 0.0.0.0`; app viewed at `http://10.0.1.121:5173/`.
- **Deploy caveat:** the destructive `import_data.py` (wipe+rebuild) can't be re-run on a live DB (FK-blocked; photo ids regenerate). On prod, apply Lightroom people/faces with the non-destructive `apply_lr_people.py`, or **promote** the dev DB wholesale (Steve's chosen path — resets the prod `user` table, family re-added after).

### 10.11 Pre-1.0 hardening — review-fixes batch (2026-07-02, merged `main` @ `1ae672b`, deployed)
A full-codebase review before inviting a second (non-Steve) user for usability feedback. **Security fundamentals were found sound** — Cloudflare-Access JWT verified (signature + `aud`), image serving path-traversal-guarded, roles enforced server-side (not just hidden UI), secrets out of git. 16 fixes across four batches, all verified on dev, merged, and deployed to prod:
- **Concurrency (the "invite-safe" gate):** SQLite **WAL mode + 5s `busy_timeout`** (`database.py`) — default journaling plus a per-request write meant two concurrent users risked `database is locked`. The per-request **`last_login` write is now throttled** to ≥15 min (`auth.py`) so reads (incl. ~80 thumbnail loads/page) stop turning into writes. **`Cache-Control`** on image responses — `immutable` for the mtime-versioned thumbnails/display/originals, 1-day for faces/cards (`images.py`). *(Note: WAL adds `photos.db-wal`/`-shm` sidecar files; the `.backup`-API snapshot script handles them.)*
- **Gallery infinite-scroll bug (the deferred KNOWN BUG):** root-caused in `App.jsx` — no error handling (a failed fetch left `loading` stuck `true`, killing all further pages) plus no stale-response guard (an old-filter page-N response could overwrite `total` and freeze pagination). Fixed with a **fetch-sequence token** + `.catch/.finally` on every photo fetch.
- **Indexes:** migration **`a6b7c8d9e0f1`** indexes the facet-query columns — `photo(date_start, place_id, magazine_id)`, `photo_person.person_id`, `photo_event.event_id`, `contribution.photo_id` (only `usage_event.created_at` was indexed before). Invisible at 1,140 photos; matters at the §2 10k+ target.
- **Correctness / robustness:** facet counts computed **page-1-only** (scroll pages reuse them); the **N+1** in bulk event-add replaced with one chunked fetch; the three bulk endpoints **reject unknown `photo_ids` with 400** (was a 500 FK error); JWT rejection now returns a **generic message** (detail logged server-side, not echoed to the client); **`usage_event` retention** (prune > 180 days at startup); the Users invite form **keeps its values on failure**.
- **Polish:** **card thumbnails** (`/api/card-thumbs`, 640px — Rolls grid + lightbox panel load ~78 KB not ~3 MB; RollDetail keeps full-res for reading Wendel's hand); **MapLibre code-split** (lazy `MapBand`/`PinEditor` → main bundle **999 KB → 187 KB**, maplibre an 801 KB on-demand chunk); Caddy **`zstd`/`gzip`** compression.
- **Deliberately not done:** a non-root API-container user — needs UID coordination with the LXC 209 host mounts (`./data`, the library LVM), so deferred to a deploy-time task with the Ops Agent.

### 10.12 UX fixes + dev user switcher (2026-07-04, `main` @ `35abaa1`)
Four changes made in preparation for inviting family members:

- **Unlinked-viewer people list:** viewers with no `user.person_id` previously saw the People filter grouped by Steve's family relationships (Parent, Sibling, etc.) but with Steve himself absent — a confusing view. Fix: `facets.py` now returns `relationship=None` for all people when the viewer is unlinked; `FilterRail.jsx` renders them flat (sorted by photo count then name) with no group headers. Linked viewers are unaffected.
- **Map pins filtered to current result:** the map always showed all 162 geocoded pins regardless of active filters. Fix: `run_query` now computes `place_counts` on page 1 (alongside `people_counts`/`event_counts`, places-selection excluded for facet consistency); `PhotoQueryResult` carries the new field; `App.jsx` derives `filteredPlaces` and passes it to `MapBand` in place of the static full list. Pins now reflect only the places present in the current filtered photo set.
- **DateSlider hidden in Rolls view:** the date slider was always rendered even in Rolls view where it has no effect (the filter rail was already correctly hidden there). Wrapped in `{view === "gallery" && …}`.
- **Dev user switcher:** in dev mode (no Cloudflare Access), the `👤` header menu now lists all registered users and lets you switch to any of them with one click — no config editing or server restart required. Implementation: `auth.py` reads a `dev_override` cookie and uses its email in place of `dev_user_email` (role taken from the DB, not forced to admin); `GET /api/dev/switch?email=xxx` sets the cookie + redirects to `/`; `GET /api/dev/users` returns the user list (both endpoints 404 in prod). `MeOut` gained `is_dev: bool` so the frontend can show the switcher vs. a real "Log out" link (prod only — logout redirects to the Cloudflare Access logout URL via `GET /api/logout`).

### 10.13 Gallery sort: mag/slide# as canonical order (2026-07-06, `main` @ `de6d4f3`)
Slide dates were tagged from Dad's index cards and have varying precision ("1963" stored as `1963-01-01`, "Mar 1963" as `1963-03-01`). Sorting by `date_start` scrambled slides within a magazine — e.g. Mag1 rendered `1,2,3,5,13,4,11…` because dates weren't monotone within the roll.

**Decision:** Dad's magazine/slide numbering is the canonical chronological order (he organized them that way). The date tags are for filtering and display, not sorting.

**New sort:** `magazine_id NULLS LAST, slide_in_mag NULLS LAST, date_start NULLS LAST` — slides sort by physical order; non-slide photos (Phase 2, no `magazine_id`) fall after all slides sorted by date. A proper `sort_key` for interleaving digital/scan photos with slides in the timeline is deferred to Phase 2 design (the likely approach: derive a sort key from each magazine's date range for slides, EXIF date for digital).

The roll-gallery sort (when `magazine_id` filter is active) was already fixed in `ca99d09`; this commit extends the same logic to the general gallery.

### 10.14 Person management, admin fixes, filter rail UX (2026-07-07, `main` @ `e0875cd`)

**Person management (admin-only):**
- `PATCH /api/admin/people/{id}` — rename `canonical_name` (undoable).
- `POST /api/admin/people` — create a new person; caller supplies a slug id (validated: lowercase/digits/underscores, 409 on duplicate), optional `father_id`/`mother_id`/`spouse_id`. Slug auto-generated from name in the UI.
- `PATCH /api/admin/people/{id}/links` — update family tree links (father/mother/spouse); pass `null` to clear a link (undoable). `PersonOut` gained `father_id`/`mother_id`/`spouse_id` so the edit UI pre-populates current values.
- **"Manage people"** modal added to the Manage ▾ header menu (wide variant at 740px): searchable list with rename action; per-row "edit links" toggle that expands inline with pre-filled dropdowns; add-person form at the bottom.

**Admin picker fix:** `people` state (photo-filtered) and `allPeople` (all persons, incl. zero-photo) are now loaded separately. Lightbox and AdminBar bulk-picker use `allPeople` so untagged-yet people like Carol Hollenkamp are selectable. Filter rail still uses `people` (photo-filtered only).

**Rolls view admin fix:** `RollDetail`'s `Lightbox` was missing all admin props — editing people/places/events was silently unavailable in Rolls mode. Props now thread `App → RollsView → RollDetail → Lightbox`; `RollDetail` reloads its photo list after each edit before bubbling `onChanged`.

**Filter rail UX:** Sections reordered to **Places → Events → People** (People expanded, Places/Events collapsed by default) so the People face grid is always visible without scrolling. Section headers show **live counts** that update with active filters. Expand/collapse state persisted per-section in `localStorage`.

### 10.15 Notes editing, UX fixes, snapshot hardening (2026-07-07)

**Card notes editing (contributor+, undoable):**
- `POST /api/admin/photos/{id}/notes` — new endpoint; accepts `{ notes: str | null }`, strips whitespace, stores null for blank, logs a `Contribution` row with `inverse` for undo.
- Undo handler added for `op = "photo_notes"` in the undo switch block.
- `NotesReq` schema added to `schemas.py`.
- Frontend: `api.editNotes(id, notes)` added to `api.js`; `Lightbox` gains `notesDraft` state (set on load + reload); admin mode renders an editable `<textarea class="lb-notes-edit">` with `onBlur` save instead of the read-only `<p class="lb-dadnotes">`.
- `scripts/apply-notes-patch-20260707.py` — standalone idempotent prod patch script with 3 caption + 440 notes changes from Ryan's OCR audit embedded as JSON; supports `--dry-run`.

**Prod → dev snapshot hardening:**
- `scripts/load-prod-snapshot.sh` — added WAL/SHM cleanup (`rm -f photos.db-wal photos.db-shm`) before the DB swap to prevent SQLite malformed-disk-image errors when the running backend's WAL was present on the dev box.

**Ryan's UX fixes (5 items):**
1. **"All rolls" back button** — replaced plain `.link` style with `.back-btn` (bordered, hover accent) for better visibility in the roll detail header.
2. **Roll cards in lightbox from Rolls tab** — `RollDetail` now passes `magazines={[roll]}` to its `Lightbox`; the index card panel now appears when a slide is opened from the Rolls tab (was missing, worked only from Gallery tab).
3. **Roll detail layout** — index cards moved to the right column; photo grid is now on the left. CSS grid template flipped from `minmax(280px,380px) 1fr` to `1fr minmax(280px,380px)`.
4. **Download filename** — `Lightbox` builds a `download="Mag01_Slide09_Rock-City-Tenn.jpg"` filename from `magazine_id`, `slide_in_mag`, and the card note text (regex-extracted from `Card: '<text>'` format, slugified, capped at 40 chars). Falls back to browser default if detail not loaded.
5. **Gallery image load errors + scroll restoration** — `Lightbox` image element gains `onError` handler; on failure shows "Photo failed to load / Try again" overlay with a reload button. Closing the lightbox now scrolls the gallery back to the last-viewed tile (`Gallery` gains `scrollToIdx` prop + `tileRefs` array; `App` tracks `galleryScrollTo` and passes it on close).

---

### 10.16 Image serving: DB connection-pool exhaustion (2026-07-14)

**Symptom (prod):** returning to the gallery after being away — typically a day or
two — a scatter of thumbnails failed to load. `docker compose logs api` showed:

```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached,
connection timed out, timeout 30.00
```

Every broken tile was a 500 on `/api/thumbnails/...`. No OOM (`dmesg` clean), no
container restarts — the process was healthy; individual requests died waiting for
a DB connection.

**Root cause — two compounding bugs, both on the image hot path:**

1. **A pooled connection was held for the whole response transfer.** Image routes
   took `db: Session = Depends(get_db)`. FastAPI registers yield-dependency teardown
   on the *request* exit stack (`fastapi/routing.py`: `scope["fastapi_inner_astack"]
   = request_stack`), and that stack unwinds **after** `await response(scope, receive,
   send)` — i.e. after the body has streamed. So `get_db`'s `db.close()` ran only once
   the JPEG had finished crossing the Cloudflare tunnel. Slow client = pinned
   connection. With the SQLAlchemy default pool (5 + 10 overflow = **15**) sitting
   *below* uvicorn's 40-thread sync pool, a gallery page of ~60 tiles exhausted the
   pool and the remainder died on the 30s checkout wait.
2. **A `last_login` write storm gated on absence.** `current_user` ran on every image
   request and refreshed `last_login` on a 15-minute throttle. The check-then-write is
   not atomic, so when the throttle had lapsed — i.e. **whenever you'd been away >15
   min** — every tile in the burst read the same stale value and committed, serialising
   ~60 writes on SQLite's single writer while each held its pool connection. This is
   why the failure tracked "away for a day or two" and never reproduced in a warm
   session (`dirty=False`, no writes, no herd).

**Fix:**
- `auth.py` — split `_identify()` (no DB) from `_load_user(..., touch: bool)`. New
  `image_user` dependency authenticates image requests using a short-lived
  `SessionLocal()` instead of `Depends(get_db)`, and passes `touch=False`. `last_login`
  is now driven by the `/api/photos` call that renders the page, not by 60 tiles.
- `routers/images.py` — all seven image routes moved to `image_user`; `_resolve()` and
  `face()` scope their own sessions so no connection is held across PIL work or
  streaming.
- `database.py` — pool set explicitly to `pool_size=20, max_overflow=30` (50 > the
  40-thread ceiling, so checkout can never queue) and `pool_timeout=10` to fail fast.

**Verified on dev (VM 201), A/B against stashed original:**

| test | original | patched |
|---|---|---|
| normal tile while 20 slow readers stream | **9.56s** (blocked on pool) | **0.03s** |
| 60-tile burst w/ stale `last_login` | writes `last_login` (storm) | unchanged |
| `/api/photos` still refreshes `last_login` | yes | yes |

The 60-tile burst alone passes on *both* — localhost streaming is instant, so the
connection-hold doesn't bite. Reproducing it requires slow-reading clients to stand in
for tunnel latency. Worth remembering: **this class of bug is invisible on loopback.**

**Note:** client-side retry on a failed tile was considered and rejected — under pool
exhaustion it adds requests to the herd causing the failure.

---

## 11. Phase 2 — Library expansion: ingesting non-slide photos (design, 2026-06-28)

> **Status:** design agreed with Steve **2026-06-28**; **not yet built**. This realizes the §2 / §5 "library expansion" vision for two new photo origins beyond Wendel's slides. The data model (§3) was built for this — most of the work is **storage/serving generalization + a new ingest path**, not a schema redesign.
>
> **Decisions locked (2026-06-28):** (a) **masters stay in Lightroom on the Mac; the app serves exported derivatives** (mirrors the slide model — §3.1); (b) **Lightroom-first authoring** with a controlled keyword hierarchy; (c) a **separate, non-destructive importer** that never touches the manifest-derived slide rows.

### 11.1 Scope & sources
Two new origins join `slide`:
- **`digital`** — born-digital photos carrying EXIF (capture date, often GPS) plus whatever Steve adds in Lightroom.
- **`scan`** — scanned prints with little/no inherent metadata. **EXIF date = the scan date (unreliable)** — it must be set/approximated in Lightroom or left unknown; never let scan timestamps pollute the timeline (§4.1/§3.8).

Slides stay `origin=slide`, untouched. **Magazines / the Rolls view stay slide-only** (§3.7) — new photos leave `magazine_id`/`slide_in_mag` null and live in the same flat stream (§3.2). All four facets, map, and timeline already handle them (`queries.py` is magazine-agnostic).

### 11.2 Authoring in Lightroom — the metadata contract
Lightroom is the **authoring** tool (better at bulk metadata than the per-photo admin UI); the app is the **browser**. Steve populates as much as possible in LR, then exports; the importer reads a **fixed set of fields** via **Pillow** (XMP packet + EXIF — no `exiftool`/system dependency, confirmed working on the slides — §11.8):

| Lightroom / file field | App target |
|---|---|
| Capture date (EXIF `DateTimeOriginal`) | `date_start` + `date_precision` (§3.8) |
| GPS (EXIF lat/lon) | reverse-geocode → `place` (lat/lon) |
| **People / face regions** (XMP `mwg-rs`) | `photo_person` — named, per-photo |
| Caption/Description (IPTC / XMP `dc:description`) | `caption` |
| Title | `original_subject` |
| Keywords (XMP `dc:subject`, **hierarchical**) | events / places / people |
| City / State / Country (IPTC location) | `place` |
| Star rating / pick flag | favorites / `representative_photo_id` signal (later) |

**The discipline that makes this work:** free-form keywords won't auto-resolve. Steve maintains a **controlled keyword hierarchy in Lightroom mapped to the existing vocabularies** — `People > <canon name>` (resolves via `person_alias`), `Events > <vocab term>` (§3.6), `Places > <gazetteer name>` (§3.4). The importer resolves them through the **same alias/gazetteer machinery as the slide importer**, and emits a review report for anything unresolved (as `import_data.py` does today). Lightroom keyword/preset conventions live in a maintained companion doc.

**Provenance (§3.5):** LR human-entered tags import as **`human-confirmed`** (authoritative, like `manifest`); any future ML output is **`auto-suggested`**.

### 11.3 Storage & identity (masters in Lightroom)
- **Masters:** stay in the Lightroom catalog on the Mac — **not** on the serving LXC (consistent with §3.1's no-originals rule for slides).
- **Derivatives:** a Lightroom **Export preset** → sized JPEG (long edge ~2560, sRGB, **metadata embedded**, **rotation baked into pixels** — the app ignores EXIF orientation, see §10.6 note) written into a new tree **`/mnt/photos/library/photos/<YYYY>/`** (alongside `slides/`, `index_cards/`, `thumbnails/`). *(Layout superseded by §12.2: batch/subject folders, `photos/<batch>/`.)*
- **Thumbnails + a display derivative** are generated by the serving/ingest layer (as for slides) — a high-res scan/photo should **not** be served full-res to the lightbox.
- **Identity:** each asset gets a **stable storage key** (content hash or assigned slug) recorded as `Photo.source_file` (still globally unique); the original filename is kept separately. This avoids cross-folder filename collisions and decouples identity from the slide naming scheme.

### 11.4 Schema additions (one new migration; backfill existing rows `origin=slide`)
- `Photo.origin` — `slide | scan | digital` (default `slide`).
- `Photo.storage_path` (or relative path) — **decouples serving from the hardcoded slide regex**; the row, not a filename pattern, tells the server where the file is.
- Optional: `Photo.original_filename`, `imported_at`, `width`, `height`.

### 11.5 Serving generalization (the current blocker — `images.py`)
Today `images.py` and `queries.py:_version` gate every image on `^Mag\d+_Slide\d+\.JPG$` and reconstruct the path as `slides/Mag<N>/file`; any other filename 400s. Change:
- Resolve the file path by **DB lookup** (`source_file` → `Photo.storage_path`), not regex path-reconstruction; path-guard by verifying the resolved path stays **under `library_root`** (containment check) instead of the slide regex.
- Add a **display-derivative** path for large images (lightbox), keeping the existing thumbnail path.
- Generalize `_version` to any file's mtime.

### 11.6 The non-destructive importer (`importer/import_photos.py`)
A **separate** module from the slide `import_data.py`. It **never deletes or rebuilds the manifest-derived slide rows**.
- **Input:** the exported files under `library/photos/` (read with `importer/metadata.py`'s Pillow-based `extract()` — the same reader already used for slides), or a **sidecar CSV exported from Lightroom**.
- **Resolution (reuse existing machinery):** keywords/face-regions → people (`person_alias`), → events (vocab, §3.6), → places (gazetteer, §3.4); GPS → reverse-geocode (`geocode.py` guards) → match/create a `place`; caption/title/date per §3.8.
- **Idempotent + non-destructive:** upsert by storage key; re-running updates changed metadata, never duplicates, never touches slides.
- **Review report** for unresolved keywords/people/places (like the slide importer), so the LR keyword hierarchy can be corrected and the run repeated.

### 11.7 UI/UX for a mixed library
- **Origin badge** in the lightbox; **Rolls view stays slide-only**.
- A **"needs review / untagged"** filter to surface bare scans, curated via the **existing bulk-tag admin UI** (§10.3) — no new tagging UI required to start.
- Facets / map / timeline already work for the new rows.

### 11.8 Build plan (phased)
- **Phase A — Decisions & conventions** ✅. Remaining: the **Lightroom keyword-hierarchy + export-preset companion doc**.
- **Phase B — Storage & serving** ✅ (built 2026-07-01, branch `phase2-ingest`): migration `d3e4f5a6b7c8` adds `origin`/`storage_path`/`original_filename`/`width`/`height`/`imported_at` (existing rows backfilled `slide` + `slides/Mag<N>/<file>`); `images.py` now serves by **DB storage_path** with a containment guard (no slide regex) and generates a **display derivative** (`/api/display`, ≤2560px — originals are 24MP) + thumbnail on demand; `queries.py:_version` generalized; `PhotoOut.origin`/`display_url` added; lightbox uses the display derivative, download stays full-res. Shared metadata reader `importer/metadata.py` (Pillow XMP/EXIF — no exiftool). Still TODO for digital/scan: create `library/photos/<YYYY>/`.
- **Phase C — Importer:**
  - *Slide LR-people overlay* ✅ (built 2026-07-01): `importer/apply_lr_people.py` — **non-destructive, idempotent** overlay that reads each slide's Lightroom face-tags and union-merges them as `human-confirmed` (manifest wins on overlap); unresolved names → `review/people_lr_unresolved.csv`. On dev: 314 LR tags added over 687 manifest tags; the 4 non-family names correctly land in the review report. Four alias fixes added to `people_canon_firstpass.csv` (Mary Catherine/AJ "Mobius"→canon Moebius; Mary Francis→Mary Frances; Nick Hollenkamp Sr).
  - *Digital/scan ingest* ☐: `import_photos.py` reading exported files (`metadata.extract`), reverse-geocode GPS, idempotent non-destructive upsert keyed by storage key. **→ Build-specced in §12.6 (as `backend/app/import_photos.py`).**
- **Phase D — UI** ☐: origin badge; "needs review/untagged" filter. **→ Build-specced in §12.8.**
- **Phase E — ML enrichment** ☐ (deferred, off-box, §5).

> **⚠️ Prod-reimport caveat (learned 2026-07-01):** the destructive `import_data.py` (wipe + rebuild) **cannot be safely re-run on a populated DB** — `contribution.photo_id`, `person.representative_photo_id`, and `user.person_id` FK-block the wipe, and photo ids regenerate (breaking the contribution log's photo links). It is a **from-scratch builder only**. On the live LXC-209 DB, apply Lightroom people with the non-destructive `apply_lr_people.py` (and future photos with `import_photos.py`), **not** by re-running `import_data.py`. (`import_data` got a partial FK fix — it now clears `representative_photo_id` before wiping — but still isn't re-run-safe on a live DB.)

**Out of scope for Phase 2 ingest:** face-recognition/ML at serve time (§5 is offline/deferred), per-line index-card hotspots (§3.7), mobile (§7).

---

## 12. Phase 2a build spec — Scanned Photos (decided 2026-07-17)

> **Status:** decisions locked with Steve 2026-07-17. This section turns §11's design
> into a **developer-ready build spec** for `origin=scan`: prints from the
> **1970s–1990s**, scanned on an **Epson FastFoto FF-680W**, people-tagged in
> Lightroom exactly like the slides. Corpus: **< 1,000 photos likely, ≤ 2,000 upper
> bound**, scanned in batches over time (the importer will run many times).
> `origin=digital` is a **later phase** — but everything here except the FastFoto
> filename parsing is shared with it; don't paint it out.
> **Where this section refines §11, this section is current** (same rule as §10 vs §§1–9).

### 12.1 Decisions locked (Steve, 2026-07-17)
1. **Gallery = everything mixed** (slides + scans + future digital), renamed **"All Photos"**.
2. **Interleave by date** — the §10.13 deferred `sort_key` design comes due now (§12.5).
3. **Batch = the FastFoto subject folder.** Scanner batches carry year/decade +
   month/season + subject; subject becomes the folder, date+subject go into filenames.
4. **"Rolls" tab renamed "Slide Photos"** — label only; the browse-by-magazine
   mechanic and in-page "roll" wording stay as-is.
5. **EXIF date cleanup in the masters is left open** — deliberately decoupled: the app
   derives scan dates from the **filename/batch**, so file-level EXIF hygiene is
   optional archival work, not a blocker (§12.4).
6. **Date precision is expressed at the scan-batch level** (scanner supports decade /
   year / month / season / exact) — not in Lightroom.
7. **No camera/scanner column** — `origin` (`scan|digital`) is the differentiator;
   don't write scanner EXIF into files.
8. **Lightroom location channel = TBD by probe** (§12.9 P2).
9. **Non-family people now allowed** — flagged, grouped separately in the People
   filter (§12.7). Relaxes §3.3's "friends out of scope".
10. **Importer lives in the backend package** (`python -m app.import_photos`, like
    `prewarm`) so it runs in the prod container.
11. **Back-of-photo scans** (FastFoto `_b` files, only when something is written on
    the back) are **linked to their photo** and shown in the lightbox (§12.6, §12.8).

### 12.2 Intake pipeline & file conventions (FastFoto → Lightroom → library)
**Pipeline:** FastFoto scan (batch = subject + date) → Lightroom import (people
face-tags, curation — same workflow as the slides) → **LR export preset** (§11.3:
long edge ~2560, sRGB, metadata embedded, rotation baked, **original filename
preserved**) → copied to **`/mnt/photos/library/photos/<batch folder>/`**.
*(Supersedes §11.3's `photos/<YYYY>/` layout — the batch/subject folder is the unit;
the year lives in the filename.)*

**FastFoto naming (CONFIRMED by probe P1, 2026-07-17):**
`<Year>_<MonthOrSeason>_<Subject>_<NNNN>[_b].jpg`, inside a folder named
`<Year>_<MonthOrSeason>_<Subject>`:
- **Year** = `1985` or a decade `1980s`; **Month** = full English name (`July`);
  **Season** = capitalized (`Summer`). **Omitted tokens vanish entirely** (no empty
  slot): `1992_ProbeYear_0001.jpg`.
- **Spaces in the subject are preserved** in both folder and filename
  (`1980s_Summer_Probe Decade/…`).
- **No subject ⇒ no folder**: files land as `Scanned_NNNN.jpg` at the tree root with
  no date anywhere. **Workflow rule for Steve: always enter a subject when
  scanning.** The importer treats files directly under `photos/` as `batch=null`.
- **`_b` = back-of-photo scan** (confirmed). **`_a` enhanced copies never appeared**
  in probe output (FastFoto configured to keep a single copy) — keep §12.6's `_a`
  handling as a cheap defensive rule anyway.

Backs are scanned only when annotated; they may skip Lightroom (no tags needed) but
**must keep their `_b` filename and land in the same batch folder** as their front.

### 12.3 Schema (one migration)
- `photo.batch` — TEXT NULL. The batch folder name (scans only; slides stay null).
- `photo.back_path` — TEXT NULL. Library-relative path of the paired `_b` scan.
- `photo.sort_date` — DATE NULL, **indexed** (composite `(sort_date, magazine_id, slide_in_mag)`). See §12.5.
- `person.is_family` — BOOLEAN NOT NULL DEFAULT 1. See §12.7.
- **Backfill:** every existing photo gets `sort_date = magazine.date_start` (join via
  `magazine_id`); every existing person gets `is_family = 1`.

### 12.4 Dates & precision for scans — filename-first
**Rule: for `origin=scan`, the batch/filename-encoded date is authoritative.** EXIF is
at most a day-level refiner. (The FF-680W writes Steve's entered date into
`DateTimeDigitized`, not `DateTimeOriginal`; `metadata.capture_date_from_exif` already
falls back Original → Digitized → DateTime, so EXIF works as a refiner today. Fixing
the masters' EXIF is optional hygiene, tracked outside this build.)

Parse the filename date tokens with the **same conventions as §3.8** (`importer/dates.py`):

| Filename tokens | `date_start` / `date_end` | `precision` | `date_raw` label |
|---|---|---|---|
| `1980s` | 1980-01-01 / 1989-12-31 | `approx` | `1980s` |
| `1985` | 1985-01-01 / 1985-12-31 | `year` | `1985` |
| `1985_07` (or month name) | 1985-07-01 / 1985-07-31 | `month` | `Jul 1985` |
| `1985_Summer` | per §3.8 season map (Winter crosses year) | `season` | `Summer 1985` |
| exact date | that day | `day` | `Jul 4, 1985` |

- **EXIF is IGNORED for scan dates — confirmed by probe P1 (2026-07-17).** FastFoto
  *derives* `DateTimeDigitized` from the batch tokens (`1980s`+`Summer` →
  `1980:07:01 12:00:00`; `July 1985` → `1985:07:01`; year-only → `1992:01:01`;
  no date → field absent), so EXIF can never add precision beyond the filename.
  There is no exact-date entry in FastFoto.
- **⚠ Poison fallback:** every LR export stamps EXIF `DateTime` (0x0132) with the
  **export timestamp**. The shared reader's Original→Digitized→DateTime fallback
  chain must NOT be used as-is for scans — a no-date scan would import dated
  export-day. `import_photos` parses dates from the filename only.
- Unparseable/no date → `date_start/end` null, `date_raw` null; the photo still
  imports (browsable, no timeline position; sorts last per §12.5).

### 12.5 Interleaved sort (resolves the §10.13 deferred design)
New materialized **`photo.sort_date`**:
- **Slides:** `magazine.date_start` — all slides in a roll share it, so a roll stays
  contiguous and in card order.
- **Scans/digital:** the photo's own `date_start` (null if unknown).

**New global ORDER BY** (in `queries.py:run_query`, replacing the §10.13 order):
`sort_date IS NULL, sort_date, magazine_id IS NULL, magazine_id, slide_in_mag,
date_start IS NULL, date_start, id`
— i.e. rolls interleave with scans by date; where a roll's span-start equals a scan's
date, the roll's slides come first; unknown-date photos sort last, stable by id.

**Accepted consequence:** magazines now order by **span start date**, not magazine
number (Wendel's numbering is broadly chronological, so drift is minor — and it's the
point of a mixed timeline). Slide order **within** a roll is unchanged. The roll-detail
gallery (magazine filter active) is unaffected. This ordering change is visible even
before any scans import — intended.

The importer sets `sort_date` on every upsert; the migration backfills slides (§12.3).

### 12.6 Importer — `backend/app/import_photos.py`
Non-destructive, idempotent, **never touches `origin=slide` rows** (assert this in
code). Run: `docker compose exec api python -m app.import_photos [--dry-run]`
(dev: `python -m app.import_photos` from `backend/`). Walks `library/photos/**`.

- **Skip:** macOS `._*` AppleDouble files; `_b` files (they are attachments, not photos).
- **`_a`/base duplicates:** if both `X.jpg` and `X_a.jpg` exist, **import `X_a` (the
  enhanced copy), skip the base, and report the pair** so Steve can prune. If only one
  exists, import it.
- **Back pairing:** for each `X_b.jpg`, find the sibling front (`X_a.jpg` else
  `X.jpg`) in the same folder → set that photo's `back_path`. Unpaired `_b` files →
  review report.
- **Identity:** `source_file` = **library-relative path** (`photos/<batch>/<file>`) —
  guaranteed unique across batches (bare filenames are not); `storage_path` = the
  same; `original_filename` = bare filename; `batch` = folder name.
  *Implementation note:* image URLs embed `source_file`, so the image routes'
  path params must be `:path`-typed (slashes) — the existing DB-lookup + containment
  guard (§11.5) already handles resolution safely.
- **Metadata (via `importer/metadata.py`, extended):**
  - **People:** `people_from_xmp` + `face_regions_from_xmp` → `photo_person` with
    region boxes, `source=human-confirmed` — the same resolution as
    `apply_lr_people.py` (canonical names + `person_alias`); unresolved → review CSV.
    The importer **never auto-creates persons** — Steve adds them via Manage People
    (§12.7) and re-runs.
  - **Events:** `dc:subject` keywords matched against the §3.6 vocabulary (exact
    name match, case-insensitive) → `photo_event`, `source=human-confirmed`;
    unmatched keywords → review CSV (never auto-create vocab).
  - **Caption / title:** extend `metadata.py` to read `dc:description` → `caption`
    and `dc:title` → `original_subject` (not currently parsed). Fallback: EXIF
    `ImageDescription` (0x010e) — probe P2 showed the LR caption survives there even
    when XMP is stripped.
  - **Orientation:** LR exports bake rotation into pixels, but `_b` backs that skip
    LR carry a live EXIF `Orientation` flag (values 3/6 seen in probe P1). The
    derivative pipeline must apply `PIL.ImageOps.exif_transpose` when generating
    back-scan derivatives (harmless no-op for orientation=1).
  - **Place (both channels, GPS wins — probe P2, §12.9):** if EXIF GPS is present
    (LR Map module), reverse-geocode with `geocode.py`'s guards → match an existing
    gazetteer place (nearby + name match) or create one (`precision=exact`).
    Otherwise use IPTC text (`Iptc4xmpCore:Location` → `photoshop:City` →
    `photoshop:State`, most-specific first) matched against gazetteer names/
    `raw_variants` with state-name/code normalization; unresolved → review CSV,
    never auto-create from text alone.
  - **Dates + `sort_date`:** per §12.4 / §12.5.
- **Upsert key = `source_file`**; re-runs update changed metadata, never duplicate.
- **Review reports** → `/data/review/` (host-visible at `./data/review/`):
  `unresolved_people.csv`, `unmatched_keywords.csv`, `unpaired_backs.csv`,
  `duplicate_versions.csv`.
- **After import:** `python -m app.prewarm` (thumbnails + display derivatives, §10.10).

### 12.7 People — family and others (refines §3.3, §10.12)
- `person.is_family` (§12.3). **Deriver short-circuit** (`family.py`): if
  `not is_family` → relationship **"Friends & others"** (skip tree derivation; today
  un-derivable people fall into "Extended family", which would be wrong for friends).
  Append "Friends & others" **last** in `REL_ORDER` (`facets.py`). Unlinked-viewer
  flat list (§10.12) unchanged.
- **Manage People modal:** add a "Non-family (friend/other)" checkbox on create and
  edit; `PersonOut` gains `is_family`. Non-family persons take no tree links.
- **Slide-era friends** (e.g. the 4 names in `review/people_lr_unresolved.csv`, incl.
  friend "Mary Catherine") **may now be added** as `is_family=0` + aliases, then
  `apply_lr_people.py` re-run — reversing §3.9's drop-on-import where Steve chooses.

### 12.8 Frontend
- **View toggle: `All Photos | Slide Photos | Scanned Photos`.**
  - *All Photos* = the current gallery (now mixed, new sort). *Slide Photos* = the
    Rolls view, renamed at the toggle only. *Scanned Photos* = the **gallery
    mechanic scoped to `origin=scan`**: full chrome — date slider, filter rail, map,
    selection mode, infinite scroll.
  - Backend: `PhotoFilter` gains `origin: str | None`; `run_query` applies it as a
    **hard scope** (filters results *and* all facet counts; no facet self-exclusion —
    it's a view scope, not a facet).
  - Frontend: `view` state adds `'scans'`; audit every `view === "gallery"`
    conditional ([App.jsx](frontend/src/App.jsx) — DateSlider, rail, body class,
    map band) → chrome shows for `view !== "rolls"`.
- **Dynamic year bounds:** new `GET /api/photos/meta` → `{year_min, year_max}` from
  `min(date_start)` / `max(coalesce(date_end, date_start))`. Frontend fetches once at
  load; `YEAR_MIN/YEAR_MAX` constants ([App.jsx:18](frontend/src/App.jsx#L18)) become
  the fallback until it resolves; "All dates" logic compares against the fetched
  bounds. (Register the route before `/photos/{id}` — the `/photos/ids` precedent.)
- **Lightbox:** origin badge for non-slides ("Scanned print" + batch name in the
  notes panel); the **back-of-photo scan is shown automatically** in the notes
  sidebar (sized to the panel, click-through to full size) when `back_path` is set,
  served through the display-derivative pipeline; download filename for scans =
  `original_filename`.
- **Untagged filter (stretch, admin-only — §11.7 Phase D):** a rail toggle filtering
  to photos with zero `photo_person` rows. Build last; drop if the batch runs long.

### 12.9 Probes — results (run 2026-07-17 on Steve's samples; tool: `python -m importer.probe <file-or-dir>`)

**P1 — FastFoto raw output: ✅ CLOSED.** Findings folded into §12.2 (filename
grammar, no-subject `Scanned_NNNN` root files, no `_a` copies) and §12.4
(`DateTimeDigitized` is token-derived — EXIF ignored for scan dates). Additional
notes: raw scans carry a live EXIF `Orientation` flag (→ §12.6 transpose rule);
FastFoto writes no Make/Model, but LR exports surface `UserComment =
"EpsonFF-680W"` — informational only, `origin` stays the differentiator (§12.1 #7).

**P2 — Lightroom roundtrip: ✅ CLOSED** (after a false alarm: the first probe blamed
the export preset for a missing XMP packet, but the packet was present in the file —
**older Pillow (10.0.1) never populates `info["xmp"]`**. `metadata.read_xmp` now
falls back to scanning the JPEG `applist` for the XMP APP1 segment; fixed
2026-07-17, version-proof either way). Confirmed on Steve's exports:
- **People + face regions:** `PersonInImage` + `mwg-rs` with `stArea` boxes — the
  slide contract holds unchanged on scans. Person names also echo into
  `dc:subject`; the importer must not double-treat them as keywords.
- **Locations — both channels work:** Map-module GPS → **EXIF GPS IFD** (existing
  `gps_from_exif` reads it); typed IPTC → **`photoshop:City` / `photoshop:State` /
  `photoshop:Country`** + **`Iptc4xmpCore:Location`** (sublocation) +
  `Iptc4xmpCore:CountryCode`. Hand-typed values vary in form (`OH`/`USA` vs LR's
  `Ohio`/`United States`) — normalize state names/codes like the geocoder guards do.
- **Keywords:** flat `dc:subject` entries, parents included as separate terms
  (`Events`, `Birthday`, `Places`, `Normandy`); **no `lr:hierarchicalSubject`** in
  this export. Vocabulary-driven matching makes parent terms harmless noise; the
  reader should still prefer `lr:hierarchicalSubject` when present.
- **Caption/title:** `dc:description` / `dc:title` confirmed (plus EXIF
  `ImageDescription` as a caption echo).
- **Capture time:** LR *Edit Capture Time* (Library → Metadata menu) writes
  **`DateTimeOriginal` + `photoshop:DateCreated`** on export while
  **`DateTimeDigitized` survives untouched** — so the archival-hygiene path exists
  and never endangers the scan-entered date. (Steve accidentally changed capture
  time on the probe photos; harmless — app dates are filename-first, and
  *Metadata → Revert Capture Time to Original* can undo it in LR.)

**P3 — resolved.** Exact-date rule: no exact-date entry exists in FastFoto;
filename-only (§12.4). Location channel: support **both** — GPS when present
(reverse-geocode → place, most precise), else IPTC text matched against the
gazetteer (Sublocation → City → State specificity order); unresolved → review CSV.
`metadata.py` extensions needed: `dc:title`, `dc:description`,
`photoshop:City/State/Country`, `Iptc4xmpCore:Location/CountryCode`,
`lr:hierarchicalSubject` (preferred over flat `dc:subject` when present).
Remaining Phase-A task: write the LR conventions companion doc (export preset =
the slide preset's settings; keyword vocabulary = §3.6 event names + gazetteer
place names).

### 12.10 Ops & rollout
- **Files:** Steve rsyncs LR-exported batches from his Mac → prod
  `/mnt/photos/library/photos/<batch>/`. Copy one **sample batch to dev** (VM 201's
  `/mnt/photos`) first — all importer development/testing happens on dev against a
  prod snapshot (`scripts/load-prod-snapshot.sh`).
- **Prod import:** `--dry-run` first, review the `/data/review/` CSVs, then the real
  run, then `python -m app.prewarm`. Safe on the live DB by design (non-destructive,
  upsert-only). **Never** `import_data.py` (§11.8 caveat stands).
- **Backups:** DB snapshot timer unaffected. The scan derivatives are re-exportable
  from the Mac masters (same recovery story as the slides).

### 12.11 Build order (slices for the developer agent)
1. **Probes** (§12.9) — close the P1/P2 contracts; update §12.4/§12.6.
2. **Migration + models** (§12.3).
3. **Interleaved sort** (§12.5) — shippable alone; visible reordering is intended.
4. **`metadata.py` extensions + `app/import_photos.py`** (§12.6) — verify on a dev
   sample batch: dry-run report, import, re-run idempotency, back pairing.
5. **People family/others** (§12.7) — deriver, REL_ORDER, Manage People checkbox.
6. **Frontend** (§12.8) — toggle rename/third view, origin scope, meta bounds,
   lightbox badge + back reveal.
7. **Rollout** (§12.10) — rsync batches, prod import, prewarm, deploy.

**Out of scope for 2a:** digital ingest specifics (shares this code; needs no FastFoto
parsing), ML enrichment (§5), any batch-browse UI (batches are provenance labels for
now — a "browse by batch" view is a possible future analog of Rolls).

### 12.12 Build status — BUILT & VERIFIED ON DEV (2026-07-17), prod rollout pending
Slices 1–6 built and verified on VM 201 against the probe sample batches; slice 7
(prod rollout) is the only remaining step. Migration `a7b8c9d0e1f2` (down_revision
`a6b7c8d9e0f1`) applied on dev — 1,140 slides backfilled `sort_date`, all persons
`is_family=1`.

**Built:**
- Schema/models + migration (§12.3); interleaved `sort_date` sort (§12.5) — verified a
  1963 scan lands between Mag1 (span-start 1962-09) and Mag4.
- Shared readers **moved into the backend package** (`app/metadata.py`, `app/dates.py`)
  so the importer runs in the prod image; `importer/` scripts + `probe.py` updated to
  `from app…`. `read_xmp` also gained an `applist` fallback (older Pillow) — committed
  in `da8c18d`.
- `metadata.extract` extended: `dc:description`/`dc:title`, IPTC location
  (`photoshop:City/State/Country` + `Iptc4xmpCore:Location/CountryCode`),
  `lr:hierarchicalSubject`, EXIF `ImageDescription` caption fallback, face regions.
- `app/import_photos.py` (§12.6): non-destructive, idempotent (verified: 8 new →
  re-run 0 new/0 tags), batch-date parsing, `_b` back pairing (4 linked), `_a` dedup,
  union-add tags, fill-if-empty scalars, review CSVs. **Friend workflow verified**:
  adding an unresolved LR name as `is_family=0` + re-run resolves it and groups it
  under "Friends & others".
- People family/others (§12.7): deriver short-circuit, `REL_ORDER`, `is_family` on
  `PersonOut`/create/edit, Manage People checkbox + per-row toggle.
- Frontend (§12.8): `All Photos | Slide Photos | Scanned Photos` toggle, origin scope,
  `/api/photos/meta` dynamic bounds (dev now shows 1962–**1992**), image routes
  `:path`-typed + URL-encoded (spaced/slashed scan paths serve 200), lightbox origin
  badge + batch + auto-shown back-of-photo (sized to the sidebar) + `original_filename`
  download name.
- `prewarm` extended to cover non-slide photos (path-based cache keys).

**Deliberate deviations / deferrals (for the developer + Steve):**
- **Reverse-geocoding is NOT done in the importer.** `importer/geocode.py` isn't in
  the container image and per-import network calls are undesirable on the prod DB.
  GPS resolves by **proximity to existing gazetteer places** (≤25 km); IPTC text by
  name match; genuine new places go to `review/unresolved_places.csv` for Steve to add
  via the pin editor, then re-run. (Refines §12.6's "reverse-geocode … or create".)
- **Scalar metadata is fill-if-empty on re-import** (caption/title/date/place), and
  tags are **union-add only** — a re-run never clobbers in-app human edits, but also
  won't propagate a later LR caption/tag *change* to an already-imported photo. A
  `--refresh-meta` flag can be added if that's wanted.
- **`_a` dedup / unpaired-back reports:** code paths present but not exercised (the
  probe batches had no `_a` copies and no orphan backs).
- **"Needs review / untagged" filter (§12.8 stretch): NOT built** — drop-if-long item.
- Non-family rename toggle is **not undoable** (low-stakes, one-click reversible).

**Rollout (slice 7, §12.10) — for the Ops step:** rsync LR-exported batches to prod
`/mnt/photos/library/photos/<batch>/`, then in the container:
`docker compose exec api python -m app.import_photos --dry-run` → review
`./data/review/*.csv` → real run → `python -m app.prewarm`. Non-destructive; **never**
`import_data.py`.

### 12.13 Catalog-read people path (added 2026-07-24, refines §12.6)
Real-data rollout surfaced that **file XMP is an unreliable source for people/events**:
Lightroom's per-keyword `includeOnExport` flag suppresses a keyword from *all*
file-metadata writes (not just export), and 18-year-old person keywords in Steve's
catalog (e.g. `Abby`/`Toby`, created 2008) had it off — 113 photos exported with no
people at all, unfixable from the file side. The catalog is authoritative, so people/
events are now read directly from it:
- **`app/read_lrcat.py`** — opens the `.lrcat` (SQLite, read-only/immutable), scopes to
  the `FastFoto/%` folder, and emits a **sidecar** `data/review/lr_people.csv`
  (`base_name,people,events`; people = keywords with `keywordType='person'`, events =
  ordinary keywords minus hierarchy-parent noise) plus a complete refreshed
  `people_seed.csv`. This is the §11.6 "sidecar CSV from Lightroom" input, realized.
- **`app/import_photos.py --people-csv <sidecar>`** — unions catalog people/events onto
  file XMP, keyed by front stem (catalog wins where XMP is empty).
- **`app/seed_people.py`** — idempotent seeder for the new cast; CSV contract
  `is_family=Y|N|Pet` (Pet → `is_family=0` + `person.notes='pet'`, a durable marker for
  a future Pets grouping), `resolved_person_id` on a row = alias to that existing person
  (not a new row). Re-runs sync `is_family`/`notes`.
- **Rollout carries the two CSVs, not the catalog** (the `.lrcat` stays on the Mac).
  Verified on dev: 770 photos, 1222 tags, 0 unresolved, pets recovered. Ops runbook in
  `STATUS.md`.

---

## 13. Phase 2b design — B2-backed digital photos (`origin=digital`) (decided 2026-07-21)

> **Status:** design agreed with Steve **2026-07-21**; **not yet built.** This realizes
> the `origin=digital` half of §11 for Steve's born-digital library. It **refines
> §11.3 and §12** where they assume every master lives on the LXC: for digital photos
> the **masters stay in Backblaze B2**, and the app serves from them by pointer + a
> local derivative cache. Nothing here changes slides or scans. Where §13 refines §11,
> §13 is current (same rule as §12 vs §11).
>
> **What makes this cheaper than it looks:** §11.5 already replaced the slide filename
> regex with a **DB `storage_path` lookup** ([routers/images.py:27-38](backend/app/routers/images.py#L27-L38)),
> so the "where is this file" indirection layer already exists. §13 is mostly (a) a
> storage backend behind that lookup, (b) a manifest-driven importer, and (c) making a
> handful of `.stat()`-on-the-master calls stop assuming a local disk. It is **not** a
> schema redesign or a serving rewrite.

### 13.1 Context — Steve's setup (established 2026-07-21)
- **The whole digital library (~65k photos) lives in a B2 bucket.** Only a **curated
  subset — several thousand** — goes into the app (non-family, duplicate, and
  near-identical shots are culled out). Goal is explicitly to **eliminate the second
  export + the file movements**, not to save LXC disk (the volume has ~32 GB free).
- **B2 holds RAW+JPG pairs** (camera writes both); a few are RAW-only.
- **The JPGs are untouched camera originals** — Steve typically does *not* adjust in
  Lightroom and re-export, so LR develop settings are generally NOT baked into the
  JPGs. Consequence: the camera JPG **is** the intended rendering (serving it direct
  loses no fidelity) — with the accepted exception in §13.10.
- **On-disk layout mirrors B2 exactly.** LR import copies files into a Photo Album tree
  organized `YYYY/mmm-dd/` (sometimes `YYYY/`) on a USB NAS (DS223j); GoodSync mirrors
  that **near-realtime** to the primary NAS (DS418); Backblaze sync copies DS418 → B2
  **nightly**. So a B2 object key = `<prefix>/YYYY/mmm-dd/<file>` with the same relative
  path the LR catalog sees — **no per-photo mapping table needed** (§13.9).

### 13.2 Decisions locked (Steve, 2026-07-21)
1. **B2 is the master tier for `origin=digital`.** The app points at it; it does not
   hold a second copy of the exported JPG.
2. **Pixels from B2, metadata from a manifest, no image export.** The importer reads a
   small **LR-generated manifest** (selection + catalog-only fields); image bytes are
   fetched exactly **once per photo** (prewarm), never on the browse path.
3. **Selection = a Lightroom keyword** (e.g. `Archive > Album`) driving a **Smart
   Collection** — extends the §11.2 controlled-hierarchy contract. Not filenames
   (§11.3 made identity independent of filenames on purpose).
4. **Serve the JPG of a RAW+JPG pair; RAW-only → review CSV** (don't build a RAW
   pipeline for the tail — §13.6).
5. **EXIF leads the date for digital** (`DateTimeOriginal` is reliable and precise);
   the `YYYY/mmm-dd` path is the sanity-check + fallback. **This inverts §12.4's
   filename-first rule for scans** — deliberately, because scan EXIF is scanner-derived
   and digital EXIF is camera-truth.
6. **No full-res download path for `origin=digital`** (display derivative only, §13.7).
   Steve already holds these masters in B2; the app need not be a second door to them,
   and this avoids presigned-URL bearer-token exposure outside Cloudflare Access.
   **Amended 2026-07-27 (Steve): digital *does* get a download button, serving the
   2560px display derivative from the local cache.** The original rationale reasoned
   from Steve's own B2 access; family viewers have none, so hiding it entirely left
   them able to download a 1978 scan but not a 2015 photo of themselves. Full-res
   proxying from B2 stays out for now (§13.10) — and presigned URLs stay rejected.
7. **Importer is manifest-diff aware → gives deletion.** Untagging in LR removes the
   photo from the app (§13.8). This makes the keyword a true bidirectional control
   surface — unlike the scan folder-walk, which can only ever add.
8. **Local derivative cache stays on the LXC** (thumbnails + display), same dirs as
   slides/scans. Browsing never touches B2 in steady state.

### 13.3 Storage abstraction (the one real serving change)
Today `photo_file()` and `_version()` do `library_root / storage_path` and
`.stat()` the result — pure local disk. Generalize to a **backend behind the existing
DB lookup**:

- **New `Photo.storage_backend`** — TEXT NOT NULL DEFAULT `'local'`, values
  `local | b2`. `storage_path` keeps meaning "the key within that backend"
  (`2015/Jul-04/IMG_1234.JPG` — a relative path for both; B2 prefixes with the
  configured bucket root). Existing slide/scan rows are all `local` (backfill default).
- **`app/storage.py`** — thin resolver:
  - `open_master(photo) -> file-like` — `local`: open the file; `b2`: fetch the object
    (B2 S3-compatible API via `boto3`, or the native b2 SDK) into a `BytesIO`/temp.
  - `master_exists(photo) -> bool` — `local`: `path.exists()`; `b2`: `HEAD` the key.
  - `master_version(photo) -> str` — a cheap change-token: `local`: mtime (today's
    behavior); `b2`: the value the **manifest** carried at import (ETag/size/mtime),
    stored on the row (§13.5) — **never a live HEAD per photo** (that would be a B2
    round-trip per gallery tile; the §10.16 pool-exhaustion lesson).
- **`routers/images.py`** switches to `storage.*`; the containment guard stays for
  `local`. **Derivatives already stream from the local cache**, so `/api/thumbnails`
  and `/api/display` are unchanged once the cache is warm — only a *cold miss* pulls
  from B2 (§13.7).
- **B2 config** in `config.py` / `.env`: `B2_BUCKET`, `B2_KEY_PREFIX` (the album-root
  prefix), `B2_KEY_ID`, `B2_APP_KEY`, `B2_ENDPOINT`. Absent ⇒ no `b2`-backed serving
  (dev without credentials still runs; digital rows just 404 their masters, caught).

### 13.4 Kill the per-photo master `.stat()` (do this regardless)
`queries.py:_version()` ([queries.py:19-27](backend/app/queries.py#L19-L27)) stats the
master **once per photo during serialization** — 60 syscalls per gallery page locally,
which would become **60 B2 HEADs per page** for digital rows. This is a latent wart
even today. Fix as part of §13:
- **New `Photo.file_version`** — TEXT NULL, the change-token from
  `storage.master_version` **recorded at import/prewarm time**. `_version()` reads the
  column, never the filesystem. Backfill slides/scans from current mtime in the
  migration; the importer/prewarm refresh it.
- **Derivative staleness for remote masters:** `derivatives.needs_regen` compares cache
  mtime vs *source* mtime — impossible cheaply for B2. Instead **fold `file_version`
  into the cache key** (`safe_key(source_file) + "." + file_version`): when the master
  changes, the key changes, the old derivative orphans (GC'd later). Local slides/scans
  keep the mtime predicate unchanged.

### 13.5 The manifest (LR → importer), the only thing that moves at import
A small CSV/JSON exported from Lightroom for the Smart Collection — **kilobytes, no
pixels**. It carries only what lives *solely* in the LR catalog; date/GPS/caption come
from the JPG's own EXIF/XMP, which `metadata.extract()` already reads and prewarm has
to open the file for anyway (§13.7). Columns:

| Field | Purpose |
|---|---|
| `relative_path` | `YYYY/mmm-dd/<file>` — becomes `storage_path`; B2 key = prefix + this |
| `filename` | bare name → `original_filename`; RAW/JPG pairing key is its stem |
| `version_token` | ETag/size/mtime → `file_version` (§13.4); lets a re-export re-derive |
| `people` | face-tag / `PersonInImage` names (catalog-only) → `photo_person` |
| `keywords` | `Events`/`Places` vocab terms (catalog-only) → events/places |
| *(date/GPS/caption)* | **NOT required** — read from EXIF at prewarm; manifest may override |

- **Resolution reuses the existing machinery** exactly as §12.6: people via
  `person_alias` (unresolved → review CSV, **never auto-create persons**); keywords via
  the §3.6 event vocabulary + §3.4 gazetteer (unmatched → review CSV, never auto-create
  vocab); provenance `source=human-confirmed`.
- **Companion doc:** the §11.2/§12.9 "LR conventions" doc gains the selection-keyword +
  Smart-Collection + manifest-export recipe.

### 13.6 RAW+JPG pairing
- A manifest row names one file. If it's a JPG, use it. If it's a RAW (`.CR2/.NEF/
  .ARW/.DNG/…`), **look for a sibling `.jpg`/`.jpeg` with the same stem** in the same
  `relative_path` folder and serve that instead (record it as `storage_path`; keep the
  RAW name in a note if useful).
- **RAW-only (no JPG sibling) → `review/raw_only.csv`.** Small minority; Steve exports
  those few by hand or lets them sit. Pillow can't decode RAW and rendering it outside
  LR would ignore develop settings — out of scope by decision #4.

### 13.7 Serving & prewarm — one B2 read per photo, ever
- **Prewarm is the fetch point.** Extend `app/prewarm.py` (which already loops
  non-slide rows — [prewarm.py:47-72](backend/app/prewarm.py#L47-L72)): for a `b2` row,
  `storage.open_master` → generate thumb (400) + display (2560) into the local cache →
  **extract EXIF/XMP from the same in-memory bytes** (`metadata.extract` accepts a
  file-like; small change from its current path arg) to fill date/GPS/caption →
  discard the master. Record `file_version`.
- **Steady state:** every browse hits the **local** cache — identical latency to slides
  today. B2 is touched only at prewarm and (if ever re-enabled) full-res download.
- **Cold-miss safety net:** if a display/thumb is requested and absent (cache cleared,
  new import not yet prewarmed), the image route may lazily pull-and-generate — but the
  **intended path is prewarm-at-import**, because a lazy 8 MB B2 pull in the request is
  exactly the slow-client latency §10.16 warned about. Prewarm first; treat lazy as
  fallback only.
- **One-time egress: ~5.4 GB for the 4,680-photo set** (measured 2026-07-27: mean master
  **1.17 MB**, min 0.15 / max 3.04 — the original ~40 GB estimate assumed 8 MB/photo and
  was ~7× too high). At B2's $0.01/GB that is pennies, and free egress is 3× stored bytes.
  **⚠ Cost is not the constraint — the account CAP is.** The first full prewarm died at
  1,900 photos against B2's **default 1 GB/day free-tier download cap**
  (`AccessDenied: download bandwidth or transaction (Class B) cap exceeded`), not a code
  fault. Steve raised the cap to **$25** (~2,500 GB) on 2026-07-27, ~450× headroom.
  Also budget **transactions**: `import_digital` HEADs every sidecar row per run
  (~4,700), so repeated runs are the real Class-B consumer, not the pixels.
- **Local cache footprint:** ~7–10 GB for several thousand photos, inside the 32 GB
  free; pure cache, rebuildable by prewarm.

### 13.8 Sync lag & deletion — the importer's freshness contract
The DS418→B2 copy is **nightly**, so a just-imported LR photo can be **up to ~24 h
ahead of B2**. The importer must be *eventually consistent with what's tagged*, not
assume the object is present:
- **HEAD before create.** A manifest row whose B2 key `master_exists()==False` → 
  `review/not_in_b2_yet.csv` instead of a broken `Photo` row. **Re-running after the
  nightly sync sweeps up the stragglers** — this is the normal workflow, not error
  recovery (the upsert is already idempotent, §12.6).
- **Deletion via manifest diff (decision #7).** Any `origin=digital` row whose
  `storage_path` is **absent from the current manifest** = untagged in LR → soft-remove
  (delete the Photo row + its derivatives; slides/scans untouched). Gate behind
  `--prune` (dry-run lists first) so a truncated/partial manifest can't nuke the set.
- **Topology note (not introduced here, but now load-bearing):** GoodSync *mirrors*, so
  the DS418 is the only always-on copy of a same-day import until the nightly runs, and
  a USB-drive deletion propagates. The app's "in the app" state trails "tagged in LR"
  by up to a day — fine for a family archive nobody browses by the minute.

### 13.9 Schema delta (one migration, down_revision = `a7b8c9d0e1f2`)
- `photo.storage_backend` TEXT NOT NULL DEFAULT `'local'` (`local|b2`).
- `photo.file_version` TEXT NULL — change-token for `?v=` + derivative keys (§13.4).
- **Backfill:** all existing rows `storage_backend='local'`,
  `file_version = str(int(master mtime))` (or leave null → `_version` falls back to a
  one-time stat, self-heals on next prewarm).
- No change to `origin` (the `digital` value already exists, §11.4). Reverse-geocode of
  new GPS follows §12.12's deviation (proximity-match existing gazetteer; genuine new
  places → `review/unresolved_places.csv`, added via pin editor, re-run).

### 13.10 UI/UX
- **Fourth view toggle: `All Photos | Slide Photos | Scanned Photos | Digital Photos`**
  — the §12.8 `origin` hard-scope generalizes with no new mechanic (`view` state adds
  `'digital'`; `PhotoFilter.origin='digital'`). *Optional* — could fold digital into
  *All Photos* only; add the fourth tab if/when the digital set is large enough to
  warrant its own scope. Recommend adding it for symmetry with the scan view.
- **Lightbox origin badge** "Digital photo" + the `YYYY/mmm-dd` (or a friendlier date).
  No back-of-photo, no Rolls membership.
- **Download for digital = the display derivative — DECIDED 2026-07-27 (Steve).** The
  button is present, but its href is the **2560px `/api/display/` derivative** off the
  local cache, not `/api/images/` (which serves a local master and would 404 for a
  `b2` row). Those are the same bytes the lightbox already loaded, so the download is
  free — no B2 fetch, no new failure mode.
  - **Label it honestly.** For slides/scans "Download" means the true original; for
    digital it does not. Use distinct wording (e.g. "⬇ Download (large JPEG)") so the
    difference is visible rather than silent — this is a §13.11-class quiet mismatch
    otherwise.
  - **Do NOT wire download to `storage.open_master`.** That puts an unbounded
    multi-MB B2 GET inside a user request, and `open_master` currently buffers the
    whole object via `BytesIO(...read())`. Full-res proxying would need a streaming
    variant + connection-pool care (the §10.16 lesson) — deferred until the family
    actually asks for originals. It layers on top of this cleanly; nothing here
    forecloses it.
  - `logUsage("download", ...)` still fires, so digital downloads stay visible in the
    §10.9 usage panel alongside slide/scan ones.
- Facets / map / timeline already work for the new rows (`queries.py` is origin-agnostic
  apart from the scope filter).

### 13.11 Accepted trade-offs — record in STATUS.md the day this ships
1. **Unadjusted-JPG divergence.** If Steve *does* adjust a photo in LR and doesn't
   re-export, the app shows the **unedited camera JPG** — silently, permanently. Fine
   given the workflow, but exactly the kind of quiet mismatch that becomes a baffling
   bug report in two years. Document it, don't fix it.
2. **The DB becomes system-of-record for identity.** For B2-backed rows the SQLite DB
   is the *only* thing mapping a bucket key to a photo's meaning — losing it unbacked
   leaves opaque objects in B2. This **raises the stakes on the two accepted backup
   gaps** (STATUS.md known issues #2 snapshot-verify no-op, #4 off-box coverage). Revisit
   those before, not after, digital goes live.
3. **~24 h tag→app lag** (§13.8) — accepted, inherent to the nightly B2 sync.
4. **First-cull is manual in LR** — the app trusts the Smart Collection; it does not
   help you pick the subset.

### 13.12 Open items / probes before build
- **P-B2a — manifest export mechanics:** which LR facility emits the manifest (plugin,
  metadata export preset, `Export as Catalog`, or reading the `.lrcat` SQLite directly)
  and can it include a stable `version_token`? *(Reading `.lrcat` directly is possible —
  it's SQLite — but the schema is reverse-engineered and shifts across LR versions;
  treat as fallback, not default.)*
- **P-B2b — B2 access shape:** S3-compatible endpoint + `boto3` vs native b2 SDK;
  application-key scoping to a single bucket/prefix, read-only.
- **P-B2c — key derivation:** confirm the exact `B2_KEY_PREFIX` such that
  `prefix + relative_path` is the literal object key (account for any bucket-side
  top-level folder the Backblaze sync adds).
- **P-B2d — `mmm-dd` grammar:** exact month token form (`Jul` vs `July` vs `07`) and the
  `YYYY`-only fallback, for the path date sanity-check (`dates.py` already handles year
  precision).

### 13.13 Build order (slices)
1. **Probes** P-B2a–d — close the manifest + key-derivation contracts.
2. **Migration + models** (§13.9) + `storage.py` skeleton (`local` passthrough — no
   behavior change; verify slides/scans unaffected).
3. **Kill per-photo master stat** (§13.4): `file_version` column, `_version` reads it,
   derivative cache key includes it. Shippable alone, benefits slides/scans too.
4. **B2 backend** in `storage.py` (open/exists/version) + config/`.env`.
5. **Manifest importer** — extend `app/import_photos.py` (or a sibling `import_digital`)
   for manifest input, RAW/JPG pairing, HEAD-before-create, `--prune` deletion diff;
   dry-run reports.
6. **Prewarm from B2** (§13.7) — fetch-once, derive, EXIF-from-bytes, record version.
7. **Frontend** — fourth view + digital lightbox badge/download (§13.10).
8. **Rollout** — dev against a small tagged sample + a scratch bucket/prefix first;
   then prod. Non-destructive; **never** `import_data.py`.

**Out of scope for 2b:** RAW rendering (decision #4); ML enrichment (§5); serving the
65k full library (only the tagged subset); presigned-URL public sharing.

### 13.14 Build plan — catalog-read revision (decided 2026-07-25)
The scan rollout (§12.13) proved that **reading the Lightroom catalog directly beats
file/manifest export** (no manual step, no `includeOnExport` surprises). Steve chose
to apply the same to digital: **selection + metadata from the `.lrcat`; pixels from
B2; derivatives cached locally.** This supersedes §13.5's manifest and §13.12/§13.13's
manifest-export slices; the rest of §13 (storage backend §13.3, kill-per-photo-stat
§13.4, prewarm-once §13.7, trade-offs §13.11) stands.

**Selection rule (locked 2026-07-25):** a **people gallery** — include a digital photo
**iff it is tagged with ≥1 person from an include-set** (immediate family to start:
Steve, Cori [catalog name **"Cori Johnson"**, not yet in DB], Kate, Ryan, Marilyn,
Wendel; extensible). People not in the set neither include nor exclude. Untagged
photos (landscape/nature) are excluded by construction.

**Scope + baseline (re-probed on the 2026-07-26 catalog):** digital library = the
**`PhotoAlbum` folder tree**, now rooted under the DS223j USB-NAS mount
(root `Mac_DS223j`, path `PhotoAlbum/…` = `/Volumes/Mac_DS223j/PhotoAlbum/…`), ~67,954
photos — separate from scans (`FastFoto`) and slides. **Scope must NOT hardcode the
root name** — it moved between 7/24 (root `PhotoAlbum`) and 7/26 (root `Mac_DS223j` +
`PhotoAlbum/%` path) when Steve remounted the drive; define it as "the PhotoAlbum tree
wherever rooted" and confirm each sync. **Person-tagged: 5,728** (was 1,618 on 7/24);
**immediate-family rule yield: 4,723** (was 1,137) — Kate 2,350, Ryan 2,192, Steve 726,
Cori 659, Marilyn 135, Wendel 1. **Yield is capped by tagging coverage, not the rule**;
it grows as Steve tags (a single 7/25–26 tagging pass ~4×'d it), re-running the sync
absorbs new tags with zero code change. Note: some photos have `.jpg`+`.psd`/RAW
siblings and some sit loose in `PhotoAlbum/` (not a `YYYY/` subfolder) — "serve the JPG"
must handle both.

**Prerequisites (before go-live):**
1. **Backup hardening — HARD prerequisite. ✅ SUBSTANTIALLY MET (2026-07-27).**
   B2-as-master makes the DB the sole map from bucket-key→meaning; losing it unbacked =
   opaque objects. Status of the two gating issues:
   - **#2 snapshot-verify no-op — fixed in git** (`db-snapshot.sh` now verifies the
     finished artifact and stages via `.partial`). **⚠ Not yet on prod** — ships with
     the deploy below, and until then the B2 round-trip check is prod's only verification.
   - **#4 off-box coverage — closed for the DB.** Snapshots replicate direct from LXC 209
     to B2 (`maegley-apps-offsite/photo-album/db/`, 90-day retention, verified by pulling
     the object back down). **Restore-drilled and passing 2026-07-27**, including
     B2-only recovery of a snapshot local retention had already pruned, and the
     old-schema path (a snapshot one migration behind, upgraded on start, serving 200s).
     Recovery is under a minute — measured, not assumed. See `infra/RESTORE.md`.

   **Residual, and acceptable to ship against:** the whole-container vzdump restore is
   still undrilled, and backup alerting catches "ran and failed" but not "never ran".
   Neither blocks digital — both concern container-rebuild speed, not the DB whose loss
   would orphan the B2 objects.
2. **B2 read-only application key** scoped to the bucket + endpoint → `.env` (Steve
   provisions).
3. **Add Cori Johnson + any new trigger people** to the DB (`seed_people` flow).
   **✅ RUN 2026-07-27** — 28 created from `people_seed_digital.csv` (1 family, 27
   friends), 37 already existed, 0 refused. Import then resolved **0 unresolved people**.
   **Two duplicate identities found and RESOLVED as aliases (Steve confirmed 2026-07-27):**
   - **"Cori Johnson" → `cori_maegley`.** Her maiden name — Steve began tagging her in
     Lightroom pre-marriage and **has not updated the catalog**, so the sidecar will keep
     emitting "Cori Johnson" indefinitely. That makes the alias the correct *permanent*
     resolution, not a stopgap. Left unfixed this would have put **646 digital photos**
     on an identity outside the family tree (`cori_maegley` is family, spouse=`steve`)
     while her slides/scans stayed on the other.
   - **"nath" → `nathan_dee`.** Enter pressed before autocomplete finished.

   Seeding had already created both as new persons (the reviewed CSV left
   `resolved_person_id` blank), so those rows were **deleted before aliasing** — leaving
   them would double-tag every photo, since the name would resolve to both the alias
   target and the stray canonical. Verified unreferenced first (0 photo tags, 0 family
   links, 0 user links). 116 → 114 persons.

   **⚠ The corrected `people_seed_digital.csv` is gitignored** (`data/` — family data
   travels out-of-band, as in the §12.13 scan rollout). **rsync it to prod** with the
   sidecar, or prod's seed run will recreate both strays.

**Probes to close first (next session):**
- **P-D1 B2 key derivation — ✅ CLOSED (2026-07-26).** Bucket `PhotoAlbum1`, S3 endpoint
  `s3.us-west-001.backblazeb2.com`. Catalog on-disk
  `/Volumes/Mac_DS223j/PhotoAlbum/2001/2001_04_08_004.jpg` maps to B2 key
  `Photo Album/2001/2001_04_08_004.jpg` — everything after the top folder is identical;
  only the top folder is renamed **`PhotoAlbum` (catalog) → `Photo Album` (B2, with a
  space)** somewhere in the DS223j→DS418→B2 sync. **Rule: `B2 key = "Photo Album/" +
  (catalog pathFromRoot with leading "PhotoAlbum/" stripped) + baseName.ext`** (handles
  loose-in-PhotoAlbum files too). ⚠ The hand-named `PhotoAlbum`/`Photo Album` mismatch is
  a latent fragility — if the sync ever normalizes it, keys break; worth Steve tidying the
  DS223j naming (also the cause of his LR re-link/path churn).
- **P-D2 B2 auth/fetch — ✅ CLOSED (2026-07-26).** `boto3` S3 client against
  `https://<b2_endpoint>` with `region_name=us-west-001`, `signature_version=s3v4`,
  the read-only app key. HEAD + GET both work; keys with spaces (`Photo Album/…`) pass
  through fine. boto3 not yet in requirements — add at build (slice 2).
- **P-D3 EXIF — ✅ CONFIRMED:** `DateTimeOriginal` read straight from the fetched JPEG
  bytes and matched the folder date (2015-01-10) — EXIF-leads-date is reliable for
  digital. *Still TODO in the importer:* RAW/JPG sibling selection (serve JPG, RAW-only
  → review) and GPS.

**Build slices:**
1. **Schema + storage abstraction — ✅ DONE (2026-07-27).** Migration `b8c9d0e1f2a3`
   (`photo.storage_backend` `local|b2` default local, `photo.file_version`) landed
   2026-07-26; the **caller integration** landed 2026-07-27:
   - `queries._version()` reads `file_version` via `storage.master_version()` instead
     of stat'ing the master during serialization — **the per-photo `.stat()` is gone**
     (was 60 syscalls/gallery page; would have been 60 B2 HEADs/page for digital).
     Verified by forcing a sentinel token into the column and seeing it served.
   - `routers/images.py` resolves through `storage.local_path()` (one containment
     guard, not two copies); `_derivative()` handles both backends — local unchanged
     (mtime predicate), remote serves the versioned cache and 404s on a miss rather
     than pulling from B2 inline (§13.7).
   - `derivatives.cache_key()` folds `file_version` in **for remote only**, so every
     local derivative already on disk stays valid — no mass regeneration on deploy.
     `needs_regen(None, cache)` is the remote predicate (existence + non-empty).
   - `prewarm.stamp_versions()` records the token for local rows; idempotent, runs
     from `python -m app.prewarm` and `importer.make_thumbnails`.
   - **`admin._rotate_file()` restamps `file_version`** after writing the rotated
     master. Easy to miss and load-bearing: image URLs are served `immutable`, so
     once `?v=` came from a column a stale token would have pinned the old
     orientation in every browser that had already loaded the photo.

   *Verified on dev 2026-07-27:* 1,910/1,918 rows stamped (the 8 unstamped are the
   dev probe rows whose files are gone — they degrade to no `?v=`, as before); a
   simulated `b2` row 404s cold, serves its versioned cache in 4–6 ms warm, 404s on
   `/api/images/`, and re-404s when its token changes (old derivative orphans);
   rotate-via-API restamps to the new mtime; slides/scans/faces/cards all unchanged.
2. **B2 backend** in `storage.py` (HEAD/GET/version via the P-D2 choice) + `.env` config.
3. **Catalog digital reader — ✅ DONE (slice 3, `cdd46db`).** `read_lrcat --digital`:
   PhotoAlbum photos tagged with ≥1 `DIGITAL_INCLUDE_PEOPLE`; mount-independent B2-key
   derivation (anchor on last `PhotoAlbum/`); RAW+JPG pairs collapsed by key; capture
   date + GPS from the catalog. Emits `digital_sidecar.csv` + `people_seed_digital.csv`.
   Live catalog: **4,704 selected, 65 people**.
4. **Digital importer — ✅ BUILT & RUN ON DEV (2026-07-27, `app/import_digital.py`).**
   Live import: **4,680/4,704 keys resolved → 4,680 `origin='digital'` rows**, 7,168
   people tags, 397 events, 379 places, 0 unresolved people, 0 awaiting sync, 24
   raw-only. Dev now holds **6,598 photos** (1,140 slides + 778 scans + 4,680 digital),
   spanning 1999–2026. Every digital row carries its B2 ETag as `file_version`, so the
   gallery serves `?v=` with no B2 call. Earlier dry-run: 7,167 people tags, 397 event tags,
   379 places, **0 unresolved people**, 0 awaiting sync, **25 raw-only** — matching the
   ~25 predicted below exactly. Key resolution is a *probe*: the sidecar proposes
   lowercase `.jpg`, the bucket holds the camera's casing, so each row HEADs an ordered
   candidate list (12-way parallel, ~4 min for the full set) and stores the found key +
   its ETag as `file_version`. Misses split by cause — `raw_only.csv` (permanent) vs
   `not_in_b2_yet.csv` (nightly lag, swept up on re-run).

   *Two things the live data forced, worth keeping in mind for future catalogs:*
   the sidecar's `people` column carries full names while `events` carries short forms,
   so every tagged person's first name read as a stray event keyword (638 "Kate", 558
   "Ryan") — now suppressed against per-photo **and** global person-name tokens, for
   reporting only (the event vocabulary is still consulted first). And "Google Upload"
   (449) / "Photo Stream" (92) are Lightroom sync plumbing, not events → `DIGITAL_NOISE`.

   *Keyword review — ✅ DONE (Steve, 2026-07-27).* 32 marked add / 18 ignore →
   `seed_events` created 31 events → re-import attached **525 event tags** (Christmas
   173, Easter 120, Birthday 93, Halloween 80, Vail 65, Zoo 55…). Unmatched fell 50 → 18,
   which is exactly the ignore list and is expected to reappear every run —
   `events_seed_digital.csv` is the durable record, not the report.

   *Places — DEFERRED (Steve, 2026-07-27): "not important yet, I'll look them up later."*
   671 digital photos carry GPS with no gazetteer place within 25 km (largely Colorado,
   which the slide-era gazetteer never covered). The analysis is done and parked in
   **`data/review/places_seed_digital.csv`**: the points cluster into just **26 distinct
   locations** at 15 km, sorted by size, with blank `name`/`region` to fill in — **the
   top 9 cover 91%** of them (the largest, 421 photos at 39.99889/-105.09500, looks like
   home). Per §13.9 places are added via the admin pin editor and picked up on a re-run;
   if that CSV gets filled in instead, a `seed_places` mirroring `seed_people`/
   `seed_events` would be the consistent way to consume it. Nothing else blocks on this —
   the photos are imported and browsable, just not mapped.

   ~~Digital importer~~ — consume the sidecar → `origin='digital'`,
   `storage_backend='b2'`, `storage_path=<resolved B2 key>`; resolve people/events
   (reuse §12 machinery); **HEAD-check B2 and defer misses** (nightly-sync lag, §13.8);
   `--prune` removes rows no longer selected. **B2-verified key resolution (from slice-3
   probes):** try `.jpg / .JPG / .jpeg / .JPEG` for the JPG-of-pair (3,673 jpg-origin +
   **875 `.cr2/.orf/.cr3` resolve via UPPERCASE `.JPG`**); HEIC-origin (131) has no JPG
   twin → use the `.HEIC` key (decode in slice 5); ~25 `.nef/.dng/.tif/.psd` are RAW-only
   → review. Store the resolved `file_version` (ETag) at import so serving never HEADs.
5. **Prewarm from B2** (§13.7): fetch each master once → thumb+display derivatives +
   EXIF from the same bytes → cache locally, record `file_version`, discard master.
   **Add `pillow-heif`** so the 131 iPhone HEICs decode (recent family photos worth
   keeping); JPGs/CR2-JPGs decode with plain Pillow.
6. **Frontend — ✅ BUILT (2026-07-27).** Fourth view **"Digital Photos"** added as a
   hard origin scope; the §12.8 mechanic generalized to a `VIEW_ORIGIN` map rather than
   another `view === "scans"` branch, so a fifth origin would be one line. Lightbox badge
   reads **"Digital photo"**. Download points at the **2560px display derivative** and is
   labeled **"⬇ Download (large JPEG)"** with a tooltip saying the full-res original
   stays in B2 — because "Download" means the true original everywhere else in the app
   and a silent downgrade is exactly the §13.11-class quiet mismatch. Back-of-photo and
   Rolls panels need no guard: `back_url`/`magazine_id` are null for digital.

**Prove-out milestone — ✅ DONE (2026-07-26).** Validated on 3 tagged Kate photos: catalog
→ derived `Photo Album/…` key → boto3 HEAD (all exist) → GET (1.38 MB) → PIL decode
(3264×1836) → 400px thumbnail → EXIF `DateTimeOriginal` matching the folder date. The
whole model is de-risked on live data; config/creds wired (`b2_enabled`). Remaining is
turning this into the real code (slices 1–6): `storage.py`, migration, importer, prewarm,
frontend — plus the backup-hardening prerequisite before go-live.

**The weekly sync (Steve's idea):** the whole chain is **idempotent + additive +
`--prune`**, so re-running reconciles the DB to the current tag state — safe to run on
a cadence. One friction: the `.lrcat` copy needs Lightroom **closed** (file lock), so a
fully-unattended cron is awkward. Simplest = a **Steve-triggered `sync` script** (copy
catalog → `read_lrcat` → import → prewarm) run when he's done a tagging pass; a real
weekly cron is possible only if LR is reliably closed on schedule. Decide the trigger
model when we build slice 4.

**Status:** planned 2026-07-25; **build starts next session.** Steve tagging in the
meantime. Nothing here is blocked by tagging coverage — build against the current
~1,137 and it scales as tags grow.

---

## 14. Phase 3 design — Face matching & bulk confirm (2026-07-27)

> **Status:** designed **2026-07-27**, **not built**. This realizes the face-recognition
> half of **§5**'s enrichment pipeline, which reserved the slot but never specified it.
> It **refines §5** where §5 is vague, and depends on §13 having landed: matching needs
> pixel access, which the B2 storage layer now provides.
>
> **Origin (2026-07-26):** Steve, mid-tagging — *"LR is pretty bad at this. Do you think
> that if I reach a critical mass with IDs that an AI could do a better job?"* The
> answer was yes, decisively, and this section is that answer made concrete.

### 14.1 Why — the point is the workflow flip, not the accuracy
Lightroom's face tool is slow, its suggestions are poor, and the confirm loop is
tedious. Today Steve grinds that UI to **author** tags one at a time.

With a matcher the direction reverses: the system proposes *"this is Kate (94%)"* across
a whole batch and Steve **confirms in bulk**. Tagging stops being authoring and becomes
reviewing. This maps onto machinery that already exists — §3.5's provenance model
(`auto-suggested` vs `human-confirmed`, both constants already in `models.py` and in
use) and the §10.3 bulk-tag admin UI. **That is the deliverable. Accuracy is just the
enabler.**

### 14.2 Why this is the easy case
1. **Closed set** — a known cast (116 persons in the DB, ~111 in the catalog), not
   open-world identification.
2. **Rich labels** — years of Steve's manual work is a serious reference set. LR ignores
   most of that signal; a proper matcher uses all of it.
3. **Mature, local, free** — ArcFace/InsightFace-class embeddings run offline on
   ordinary CPUs. **Never a cloud face API**: family faces don't leave the house. That
   is a privacy decision, and it is also simply unnecessary here.

### 14.3 Enrollment audit — what we actually have (measured 2026-07-27)
The reference set is **not** the same as the tag count, and the gap matters:

| | |
|---|---|
| `photo_person` rows | 9,405 |
| …**with a face-region box** | **1,784** (the actual enrollment set) |
| distinct people with ≥1 box | 82 |
| by origin | **scan 1,151 · slide 633 · digital 0** |
| best-enrolled | steve 339, karen 336, marilyn 268, kate 204, ryan 107, wendel 99 |

**Two consequences, and the second is the important one.**

*First*, 1,784 boxes across 82 people is already ample — a matcher needs ~5–20 examples
per person, so the core family is enrolled 10–30× beyond requirement.

*Second, and easy to miss:* **every one of those boxes is from a slide or a scan.**
`import_digital` takes people from the catalog sidecar, which carries names but no
region geometry, so the 4,680 digital photos contributed **zero** reference faces. Our
entire enrollment is therefore **pre-digital-era** — the era where the aging problem
(§14.8) bites hardest. The catalog itself holds far more (~10,639 person links as of
2026-07-26, including recent-era digital faces with regions). **Extracting digital-era
regions from the `.lrcat` is the single highest-value input to this build**, because
recent adults are exactly where face matching is strongest. See probe **P-F1**.

### 14.4 Decisions to confirm with Steve (nothing locked yet)

**D1 — Where does it run?** §5 said "a future ML-capable machine"; that machine still
doesn't exist. Dev VM 201 is **4 cores (i5-8259U, avx2), 3 GB RAM, no GPU, ~1 GB free
while the dev servers run**. Prod LXC 209 (2 GB) is out of the question.
- *Recommended:* **run it on dev VM 201 as an offline batch, with the dev servers
  stopped.** CPU inference is ~0.3–1 s/photo, so a full 6,598-photo pass is roughly
  1–2 hours, once. RAM is the real constraint, not speed — mitigated by batching and by
  writing work files to `/mnt/photos` (162 GB free) rather than `/home` (7 GB).
- *Alternative:* Steve's Mac is far faster, but then the pipeline lives outside this
  repo's deploy story. *Do not* run it on prod — §5 and §8 both say enrichment never
  touches the serving box, and 2 GB makes it moot anyway.

**D2 — Enrollment source.** (a) DB regions only (1,784, pre-digital), (b) **+ extract
digital-era regions from the `.lrcat`** (recommended — see §14.3), (c) bootstrap by
matching against whatever is confirmed and iterating.

**D3 — Detection scope.** All 6,598 photos, or digital-only first? *Recommended:*
**digital first** — it is the largest set, the most recent, the best-matching era, and
the one Steve is actively tagging. Slides/scans are already well tagged from the
manifest and can follow.

**D4 — Threshold policy.** A single global cosine threshold, or per-person? *Recommended:*
one conservative global threshold to start, tuned against held-out confirmed faces, with
the review queue sorted by confidence so the easy bulk clears first.

**D5 — Pets.** Abby, Toby, Floyd and Muffy are `notes='pet'` persons whose LR regions
were hand-drawn (LR doesn't detect animal faces). Human face detectors will not find
them. *Recommended:* explicitly **out of scope** — they stay manual.

### 14.5 Model & runtime
- **InsightFace `buffalo_l`** (SCRFD detector + ArcFace R100 recogniser) on **ONNX
  Runtime CPU**. ~350 MB of models, avx2 is present, no GPU needed.
- Embeddings are 512-float vectors — ~2 KB per face, so even 20k faces is ~40 MB.
  Matching is cosine similarity against per-person **centroids** (plus nearest-neighbour
  against raw references for the hard cases); at this scale a brute-force NumPy dot
  product is instant and **no vector database is warranted**.
- **Offline and batch, never in a request path.** Nothing here is imported by
  `app.main`; the serving stack carries no ML weight (§5, §8).

### 14.6 Schema delta (one migration, additive)
Deliberately **does not** write speculative rows into `photo_person` — suggestions live
apart until confirmed, so an un-reviewed guess can never leak into the gallery, and
discarding the whole ML layer stays a `DROP TABLE`.

- **`face`** — one row per *detected* face: `photo_id`, bbox (normalized, matching
  `photo_person`'s convention), `det_score`, `embedding` BLOB, `detector_version`.
- **`face_suggestion`** — `face_id`, `person_id`, `score`, `status`
  (`pending|accepted|rejected`), `decided_by`, `decided_at`.
- **Confirming a suggestion writes a normal `photo_person` row** with
  `source='human-confirmed'` and the face box copied in — indistinguishable from a
  hand-made tag, which is the point. Rejections are retained so the same wrong guess is
  never re-offered.
- `photo_person.source='auto-suggested'` (constant already exists, currently unused)
  is reserved for a future *auto-apply-above-threshold* mode. **Not enabled in v1** —
  D4 says suggest, never auto-apply.

### 14.7 UI — the bulk confirm queue
A new admin view, reusing the §10.3 bulk-tag patterns:
- Grid of **face crops** (not whole photos) for one proposed person, sorted by
  confidence descending — the visual judgement is fast and near-binary.
- **Accept all / accept above threshold / reject selected**, one click for a screenful.
- Every action is a `contribution` row, so it is **undoable** like every other edit.
- Entry point: a "Suggested people" count badge in the admin bar.

### 14.8 Honest limits — record these before anyone is surprised
1. **Aging is the hard part.** A face embeds very differently at 5 and at 50, so
   cross-*decade* matching (a slide-era child → their digital-era adult self) is
   genuinely unreliable. Within an era it is strong. Expect **excellent on recent
   adults, shaky on big age gaps and young children** — which is precisely why §14.3's
   digital-era enrollment matters.
2. **Suggest, never auto-apply** (D4). A threshold plus human confirmation keeps
   mistakes out of a family archive that is meant to be trustworthy.
3. **Pets are out** (D5).
4. **A 29-person long tail** had only 1–2 catalog examples as of 2026-07-26 and will
   match poorly. That is fine — they are exactly the faces worth tagging by hand, and
   the matcher handles the volume instead.
5. **This is the largest single feature discussed for this project.** It is well-trodden
   and low-risk technically, but it is not an afternoon.

### 14.9 Probes to close first
- **P-F1 — catalog face regions.** Can `read_lrcat` extract per-face bounding boxes for
  *digital* photos (LR's `AgLibraryFace` tables) the way `import_photos` reads them from
  scan XMP? This is the highest-value input (§14.3) and decides D2.
- **P-F2 — throughput and RAM on VM 201.** Time detection+embedding over ~200 photos and
  watch peak RSS, with the dev servers stopped. Decides D1 and whether batching needs
  tuning.
- **P-F3 — accuracy against held-out truth.** Hold out ~20% of confirmed faces, match the
  rest, and measure precision/recall per era (slide / scan / digital) — this turns §14.8's
  aging caveat from a claim into a number, and sets D4's threshold.
- **P-F4 — HEIC/RAW coverage.** Detection runs on the *display derivative*, not the
  master (already local, already sized, no second B2 fetch) — confirm 2560px is enough
  resolution for reliable small-face detection in group shots.

### 14.10 Build order (slices)
1. **P-F1–F4 probes** — close the enrollment source, the box it runs on, and the threshold.
2. **Migration + `face`/`face_suggestion` models** (§14.6); no behaviour change.
3. **Detection + embedding batch** over display derivatives → `face` rows. Resumable and
   idempotent, like every other importer here.
4. **Enrollment + matching** — build per-person centroids from confirmed regions, score
   every unassigned face, write `face_suggestion` rows above threshold.
5. **Bulk-confirm UI** (§14.7) + undo integration.
6. **Rerun cadence** — folds into the weekly `sync` script (§13.14) so newly imported
   photos get suggestions automatically.

**Out of scope for Phase 3:** open-world identification (strangers stay unnamed), scene
and activity hints (§5's other half), auto-apply without confirmation, pets, and any
cloud inference whatsoever.
