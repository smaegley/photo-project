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
