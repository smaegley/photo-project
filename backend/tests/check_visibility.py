"""Leak check for the visibility rule (SPEC §15.7).

Runs the real app against a throwaway COPY of the database, culls a spread of photos,
then walks the family-facing API as a viewer and fails if any hidden photo is reachable
— by listing, by count, by id or by image URL. Also checks cull/restore/undo round-trip.

    cd backend && ../.venv/bin/python -m tests.check_visibility

Run it after touching any route that returns, counts or serves photos. It never writes
to the real database or the library (derivatives it triggers are the normal cache).
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

SRC = Path(os.environ.get("DB_PATH") or Path(__file__).resolve().parents[2] / "data" / "photos.db")
tmp = Path(tempfile.mkdtemp(prefix="vis-check-")) / "photos.db"
with sqlite3.connect(f"file:{SRC}?mode=ro", uri=True) as s, sqlite3.connect(tmp) as d:
    s.backup(d)                       # online backup API — WAL-safe (see STATUS gotchas)
os.environ["DB_PATH"] = str(tmp)
os.environ["CF_ACCESS_TEAM_DOMAIN"] = os.environ["CF_ACCESS_AUD"] = ""   # dev-bypass auth

from alembic import command                      # noqa: E402
from alembic.config import Config                # noqa: E402
cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
command.upgrade(cfg, "head")

from fastapi.testclient import TestClient        # noqa: E402
from urllib.parse import quote                   # noqa: E402
from app.main import app                         # noqa: E402
from app.config import settings                  # noqa: E402
assert settings.db_path == str(tmp), "refusing to run against the real database"

admin = TestClient(app)
viewer = TestClient(app, cookies={"dev_override": "vis-check-viewer@example.invalid"})
fails: list[str] = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


db = sqlite3.connect(tmp)
db.row_factory = sqlite3.Row
assert viewer.get("/api/me").json()["role"] == "viewer"
assert admin.get("/api/me").json()["role"] == "admin"

# ---- pick targets: one per origin, each as richly connected as possible ----
targets = []
for origin in ("slide", "scan", "digital"):
    r = db.execute("""
        select p.* from photo p
        where p.origin=? and p.place_id is not null
          and exists(select 1 from photo_person pp where pp.photo_id=p.id and pp.region_w is not null)
          and exists(select 1 from photo_event pe where pe.photo_id=p.id)
        order by p.id limit 1""", (origin,)).fetchone() or db.execute(
        "select * from photo where origin=? order by id limit 1", (origin,)).fetchone()
    if r:
        targets.append(r)
rep = db.execute("""select p.* from photo p join person pe on pe.representative_photo_id=p.id
                    order by p.id limit 1""").fetchone()
back = db.execute("select * from photo where back_path is not null order by id limit 1").fetchone()
targets += [t for t in (rep, back) if t is not None]
targets = list({t["id"]: t for t in targets}.values())
ids = [t["id"] for t in targets]
print(f"targets: {[(t['id'], t['origin']) for t in targets]}")


def snapshot(c):
    return {
        "total": c.get("/api/photos?page_size=1").json()["total"],
        "ids": set(c.get("/api/photos/ids").json()),
        "people": {p["id"]: p["photo_count"] for p in c.get("/api/people?with_photos_only=false").json()},
        "events": {e["id"]: e["photo_count"] for e in c.get("/api/events?with_photos_only=false").json()},
        "places": {p["id"]: p["photo_count"] for p in c.get("/api/places").json()},
    }


before = snapshot(viewer)
# photos already hidden in the source DB (e.g. a real inbox) are not this test's business
hidden_at_start = db.execute("select count(*) from photo where visibility!='published' or culled_from is not null or culled_at is not null").fetchone()[0]
n_people = {r[0]: r[1] for r in db.execute(
    f"select person_id,count(distinct photo_id) from photo_person where photo_id in ({','.join('?'*len(ids))}) group by 1", ids)}
n_events = {r[0]: r[1] for r in db.execute(
    f"select event_id,count(distinct photo_id) from photo_event where photo_id in ({','.join('?'*len(ids))}) group by 1", ids)}
n_places = {}
for t in targets:
    if t["place_id"]:
        n_places[t["place_id"]] = n_places.get(t["place_id"], 0) + 1

print("permissions")
check(viewer.post("/api/admin/photos/cull", json={"photo_ids": ids}).status_code == 403, "viewer cannot cull")
check(viewer.get("/api/admin/culled").status_code == 403, "viewer cannot list culled")

# warm image caches while published, so 404s below are the gate and not a cache miss
img_urls = {}
for t in targets:
    sf = quote(t["source_file"])
    urls = [f"/api/thumbnails/{sf}", f"/api/display/{sf}"]
    if t["storage_backend"] == "local":
        urls.append(f"/api/images/{sf}")
    if t["back_path"]:
        urls.append(f"/api/photo-back/{t['id']}")
    for (fid,) in db.execute("select id from face where photo_id=? limit 1", (t["id"],)):
        urls.append(f"/api/face-crop/{fid}")
    for (pid,) in db.execute("select person_id from photo_person where photo_id=? and region_w is not null limit 1", (t["id"],)):
        urls.append(f"/api/tag-crop/{t['id']}/{quote(pid)}")
    for (pid,) in db.execute("select id from person where representative_photo_id=? limit 1", (t["id"],)):
        urls.append(f"/api/faces/{quote(pid)}")
    img_urls[t["id"]] = [u for u in urls if viewer.get(u).status_code == 200]
print(f"image urls under test: {sum(len(v) for v in img_urls.values())}")

print("cull")
r = admin.post("/api/admin/photos/cull", json={"photo_ids": ids})
check(r.status_code == 200 and r.json()["changed"] == len(ids), f"admin culls {len(ids)}")
after = snapshot(viewer)

print("viewer: listings and counts")
check(after["total"] == before["total"] - len(ids), "gallery total dropped by exactly the culled count")
check(not (after["ids"] & set(ids)), "no culled id in /api/photos/ids")
for label, key, exp in (("people", "people", n_people), ("events", "events", n_events), ("places", "places", n_places)):
    bad = [k for k, n in exp.items() if (before[key].get(k) or 0) - (after[key].get(k) or 0) != n]
    check(not bad, f"{label} counts dropped by exactly the culled tags ({len(exp)} checked){' — off: ' + str(bad[:3]) if bad else ''}")
for t in targets:
    for q in (f"origin={t['origin']}", *( [f"magazine_id={t['magazine_id']}"] if t["magazine_id"] else [] ),
              *( [f"places={quote(t['place_id'])}"] if t["place_id"] else [] )):
        got = viewer.get(f"/api/photos/ids?{q}").json()
        check(t["id"] not in got, f"photo {t['id']} absent from filtered ids ({q})")
    facet = viewer.get(f"/api/photos?page_size=1&origin={t['origin']}").json()
    check(sum(1 for _ in facet["people_counts"]) >= 0, f"facet query runs ({t['origin']})")

print("viewer: direct access")
for t in targets:
    check(viewer.get(f"/api/photos/{t['id']}").status_code == 404, f"detail 404 for photo {t['id']}")
    for u in img_urls[t["id"]]:
        check(viewer.get(u).status_code == 404, f"404 {u[:70]}")
for p in viewer.get("/api/people?with_photos_only=false").json():
    if rep is not None and p.get("representative_photo_id") == rep["id"]:
        check(p["face_url"] is None, f"no face_url for person whose representative is culled ({p['id']})")

print("viewer: generic sweep of every parameterless GET outside /api/admin")
needles = [t["source_file"] for t in targets] + [quote(t["source_file"]) for t in targets]
swept = 0
for route in app.routes:
    path = getattr(route, "path", "")
    if "GET" not in getattr(route, "methods", ()) or "{" in path or path.startswith("/api/admin") \
            or not path.startswith("/api") or path in ("/api/logout", "/api/dev/switch"):
        continue
    body = viewer.get(path).text
    swept += 1
    hit = [n for n in needles if n in body]
    check(not hit, f"{path} does not mention a culled file")
print(f"  ({swept} routes swept)")

print("admin: still reachable where intended")
culled = admin.get("/api/admin/culled").json()
check({p["id"] for p in culled["photos"]} >= set(ids) and culled["total"] >= len(ids), "culled view lists them")
check(not (set(admin.get("/api/photos/ids").json()) & set(ids)), "admin's normal gallery hides them too")
for t in targets:
    check(admin.get(f"/api/photos/{t['id']}").json().get("visibility") == "culled", f"admin detail shows culled ({t['id']})")
    for u in img_urls[t["id"]][:1]:
        r = admin.get(u)
        check(r.status_code == 200 and "private" in r.headers.get("cache-control", ""), f"admin image 200 + private cache ({u[:50]})")

print("round trip")
admin.post("/api/admin/undo")
check(snapshot(viewer) == before, "undo of cull restores every listing and count")
admin.post("/api/admin/photos/cull", json={"photo_ids": ids})
r = admin.post("/api/admin/photos/restore", json={"photo_ids": ids})
check(r.json()["changed"] == len(ids) and snapshot(viewer) == before, "restore returns everything")
admin.post("/api/admin/undo")
check(not (snapshot(viewer)["ids"] & set(ids)), "undo of restore re-culls")
admin.post("/api/admin/photos/restore", json={"photo_ids": ids})
for t in targets:
    for u in img_urls[t["id"]][:1]:
        check(viewer.get(u).status_code == 200, f"viewer image back after restore ({u[:50]})")
left = db.execute("select count(*) from photo where visibility!='published' or culled_from is not null or culled_at is not null").fetchone()[0]
check(left == hidden_at_start, "no leftover cull state after restore")

print("pending (inbox) photos — same rule, different state")
db.execute(f"update photo set visibility='pending' where id in ({','.join('?'*len(ids))})", ids)
db.commit()
pend = snapshot(viewer)
check(not (pend["ids"] & set(ids)) and pend["total"] == before["total"] - len(ids), "pending photos absent from viewer listings")
for t in targets:
    check(viewer.get(f"/api/photos/{t['id']}").status_code == 404, f"viewer detail 404 for pending {t['id']}")
    for u in img_urls[t["id"]][:2]:
        check(viewer.get(u).status_code == 404, f"viewer 404 pending {u[:60]}")
check(viewer.get("/api/admin/inbox").status_code == 403 and viewer.get("/api/admin/inbox/summary").status_code == 403, "viewer cannot open the inbox")
check(viewer.post("/api/admin/photos/publish", json={"photo_ids": ids}).status_code == 403, "viewer cannot publish")
box = admin.get("/api/admin/inbox").json()
check({p["id"] for g in box["groups"] for p in g["photos"]} >= set(ids), "admin inbox lists them")
check(admin.get("/api/admin/inbox/summary").json()["pending"] >= len(ids), "badge count includes them")
r = admin.post("/api/admin/photos/cull", json={"photo_ids": ids[:1]})
r2 = admin.post("/api/admin/photos/restore", json={"photo_ids": ids[:1]})
back = db.execute("select visibility from photo where id=?", (ids[0],)).fetchone()[0]
check(r.json()["changed"] == 1 and r2.json()["changed"] == 1 and back == "pending", "culling a pending photo and restoring returns it to the inbox, not to published")
r = admin.post("/api/admin/photos/publish", json={"photo_ids": ids})
check(r.json()["changed"] == len(ids) and snapshot(viewer) == before, "publish makes them visible, counts back to baseline")
admin.post("/api/admin/undo")
check(not (snapshot(viewer)["ids"] & set(ids)), "undo of publish hides them again")
admin.post("/api/admin/photos/publish", json={"photo_ids": ids})

print(f"\n{'FAILED: ' + str(len(fails)) if fails else 'ALL CHECKS PASSED'}")
sys.exit(1 if fails else 0)
