"""Outings, triage, the inbox tabs, event naming and reconcile (SPEC §15.5/§15.8/§15.4-8).

Runs the real app on a throwaway COPY of the database with an empty temp library and a
fake bucket listing — no network, nothing written to the real DB, library or B2.

    cd backend && ../.venv/bin/python -m tests.check_inbox

Needs ingested photos in the source DB (dev has the test batch; on prod, the inbox).
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path(os.environ.get("DB_PATH") or Path(__file__).resolve().parents[2] / "data" / "photos.db")
tmpdir = Path(tempfile.mkdtemp(prefix="inbox-check-"))
tmp = tmpdir / "photos.db"
with sqlite3.connect(f"file:{SRC}?mode=ro", uri=True) as s, sqlite3.connect(tmp) as d:
    s.backup(d)
os.environ["DB_PATH"] = str(tmp)
os.environ["LIBRARY_ROOT"] = str(tmpdir / "library")
os.environ["CF_ACCESS_TEAM_DOMAIN"] = os.environ["CF_ACCESS_AUD"] = ""
os.environ["INGEST_MAX_AUTO_CULL"] = "3"

from alembic import command                      # noqa: E402
from alembic.config import Config                # noqa: E402
cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
command.upgrade(cfg, "head")

from fastapi.testclient import TestClient        # noqa: E402
from app.main import app                         # noqa: E402
from app.config import settings                  # noqa: E402
from app import derivatives, models as m, outings, reconcile   # noqa: E402
from app.database import SessionLocal            # noqa: E402
assert settings.db_path == str(tmp), "refusing to run against the real database"
assert str(settings.thumbnails_dir).startswith(str(tmpdir)), "refusing to touch the real library"

admin = TestClient(app)
viewer = TestClient(app, cookies={"dev_override": "inbox-check-viewer@example.invalid"})
fails: list[str] = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


db = sqlite3.connect(tmp)
db.row_factory = sqlite3.Row
q = lambda sql, *a: db.execute(sql, a).fetchall()          # noqa: E731
n_ing = q("select count(*) from photo where ingest_run_id is not null")[0][0]
if not n_ing:
    sys.exit("no ingested photos in the source DB — nothing to check")

# Start from a known state: every ingested photo pending and unclassified.
db.execute("update photo set visibility='pending', triage=null, outing_id=null, culled_from=null "
           "where ingest_run_id is not null")
db.execute("delete from outing")
db.commit()
gallery_before = viewer.get("/api/photos/meta").json()

print("== outings + triage ==")
st = outings.run()
print("  ", st)
tri = dict(q("select triage, count(*) from photo where ingest_run_id is not null group by 1"))
check(None not in tri, "every ingested photo has a triage class")
check(q("select count(*) from photo where ingest_run_id is not null and outing_id is null")[0][0] == 0,
      "every ingested photo has an outing")
check(q("""select count(*) from photo p where triage='related' and not exists
           (select 1 from photo s where s.outing_id=p.outing_id and s.triage='selected')""")[0][0] == 0,
      "every related photo shares an outing with a selected one")
check(q("""select count(*) from photo p where triage='unselected' and exists
           (select 1 from photo s where s.outing_id=p.outing_id and s.triage='selected')""")[0][0] == 0,
      "no unselected photo sits in an outing that has a selected one")
check(q("""select count(*) from photo p where triage='selected' and not exists
           (select 1 from face f join face_suggestion fs on fs.face_id=f.id
            where f.photo_id=p.id and fs.status in ('pending','accepted'))
           and not exists (select 1 from photo_person pp where pp.photo_id=p.id)""")[0][0] == 0,
      "every selected photo shows someone the app knows")
gaps = q("""select o.id, (julianday(o.end_at)-julianday(o.start_at))*24 h, count(*) n
            from outing o join photo p on p.outing_id=o.id where o.start_at is not null group by o.id""")
bad = 0
for o in gaps:
    ts = [datetime.fromisoformat(r[0]) for r in q(
        "select taken_at from photo where outing_id=? order by taken_at", o["id"])]
    bad += any(b - a >= outings.GAP for a, b in zip(ts, ts[1:]))
check(bad == 0, f"no outing contains a gap of 2 h or more ({len(gaps)} timed outings)")
before = q("select id, outing_id, triage from photo where ingest_run_id is not null order by id")
outings.run()
check(before == q("select id, outing_id, triage from photo where ingest_run_id is not null order by id"),
      "a second run changes nothing (ids stable)")
biggest = q("select outing_id, count(*) n from photo where outing_id is not null group by 1 order by n desc limit 1")[0]
print(f"   biggest outing: {biggest['n']} photos")

print("== inbox API ==")
check(viewer.get("/api/admin/inbox").status_code in (401, 403), "viewer cannot open the inbox")
ib, un = admin.get("/api/admin/inbox").json(), admin.get("/api/admin/inbox?tab=unselected").json()
n = lambda d: sum(len(g["photos"]) for g in d["groups"])     # noqa: E731
check(n(ib) == tri.get("selected", 0) + tri.get("related", 0), f"Inbox tab = selected + related ({n(ib)})")
check(n(un) == tri.get("unselected", 0), f"Not selected tab = unselected ({n(un)})")
check(n(ib) + n(un) + tri.get("junk", 0) == n_ing, "the tabs cover every pending photo exactly once")
check(admin.get("/api/admin/inbox/summary").json()["pending"] == n(ib), "badge counts the Inbox tab only")
check(viewer.get("/api/photos/meta").json() == gallery_before, "family gallery unchanged by triage")

print("== likely junk ==")
jk = admin.get("/api/admin/inbox?tab=junk").json()
check(n(jk) == tri.get("junk", 0), f"Likely junk tab = junk ({n(jk)})")
check(q("""select count(*) from photo p where triage='junk' and
           (exists (select 1 from face f where f.photo_id=p.id) or junk_score is null or junk_score < 0.5)""")[0][0] == 0,
      "no photo with a face, or under the threshold, is classed as junk")
check(q("select count(*) from photo where triage='junk' and visibility!='pending'")[0][0] == 0,
      "junk is never culled or published by the pipeline")
if n(jk):
    jid = jk["groups"][0]["photos"][0]["id"]
    admin.post("/api/admin/photos/rescue", json={"photo_ids": [jid]})
    outings.run()
    check(q("select triage from photo where id=?", jid)[0][0] == "rescued", "a rescued junk photo stays in the Inbox")
    admin.post("/api/admin/undo")
    outings.run()
    check(q("select triage from photo where id=?", jid)[0][0] == "junk", "undo returns it to Likely junk")

print("== stacks ==")
from app import stacks                                           # noqa: E402
before_vis = q("select id, visibility from photo order by id")
st = stacks.run()
check(q("select id, visibility from photo order by id") == before_vis, "stacking culls and publishes nothing")
solo = q("select stack_id, count(*) n from photo where stack_id is not null and visibility='pending' group by 1 having n < 2")
check(not solo, f"every stack has at least two photos ({st['stacks']} stacks, {st['stacked_photos']} photos)")
check(q("""select count(*) from (select stack_id from photo where stack_id is not null and visibility='pending'
           group by 1 having count(distinct outing_id) > 1)""")[0][0] == 0, "a stack never spans outings")
bad = 0
for (sid,) in q("select distinct stack_id from photo where stack_id is not null and visibility='pending'"):
    ms = q("select taken_at, phash from photo where stack_id=? and visibility='pending' order by taken_at, id", sid)
    for a, b in zip(ms, ms[1:]):
        dt = (datetime.fromisoformat(b[0]) - datetime.fromisoformat(a[0])).total_seconds()
        bad += dt > stacks.MAX_SECONDS or stacks.bits_apart(a[1], b[1]) > stacks.MAX_BITS
check(bad == 0, "neighbours in a stack are within 30 s and 12 bits")
s2 = stacks.run()
check(s2["updated"] == 0 and s2["measured"] == 0, "a second run changes nothing")
big = q("select stack_id, count(*) n from photo where stack_id is not null and visibility='pending' group by 1 order by n desc limit 1")
if big:
    sid = big[0][0]
    mem = [r[0] for r in q("select id from photo where stack_id=? and visibility='pending' order by id", sid)]
    admin.post("/api/admin/photos/cull", json={"photo_ids": mem[1:]})
    stacks.run()
    check(q("select stack_id from photo where id=?", mem[0])[0][0] is None,
          "after culling the rest, the kept photo is no longer shown as a stack")
    admin.post("/api/admin/undo")
    stacks.run()
    check(q("select count(*) from photo where stack_id=? and visibility='pending'", sid)[0][0] == len(mem),
          "undo + rerun rebuilds the stack")

print("== auto-tag ==")
import contextlib, io, re                                         # noqa: E402,E401
from app import autotag, match_faces                              # noqa: E402


def identified() -> int:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        match_faces.run(dry_run=True)
    return int(re.search(r"already identified:\s+(\d+)", buf.getvalue()).group(1))


# Fixture: undo whatever auto-tagging the source DB already has, so there is work to do.
db.execute("""update face_suggestion set status='pending', decided_by=null, decided_at=null
              where decided_by='auto-face'""")
db.execute("delete from photo_person where source='auto-face'")
db.commit()
strong = q("""select fs.id, f.photo_id, fs.person_id, pe.notes from face_suggestion fs
              join face f on f.id=fs.face_id join photo p on p.id=f.photo_id
              join person pe on pe.id=fs.person_id
              where fs.status='pending' and fs.score>=0.65 and p.ingest_run_id is not null
              and p.visibility='pending' order by fs.score desc""")
if len(strong) < 5:
    print("  (fewer than 5 strong pending suggestions in this DB — auto-tag checks skipped)")
else:
    refs_before = identified()
    tags_before = q("select count(*) from photo_person")[0][0]
    human = [r for r in strong if (r["notes"] or "") != "pet"]
    pub_sid, pub_photo, pub_person = human[0]["id"], human[0]["photo_id"], human[0]["person_id"]
    pet_person = human[1]["person_id"]
    db.execute("update photo set visibility='published' where id=?", (pub_photo,))
    db.execute("update person set notes='pet' where id=?", (pet_person,))
    db.commit()
    weak_before = q("select count(*) from face_suggestion where status='pending' and score<0.65")[0][0]
    st = autotag.run()
    check(st["tagged"] > 0 and q("select count(*) from photo_person where source='auto-face'")[0][0] == st["tagged"],
          f"strong matches are tagged with source auto-face ({st['tagged']})")
    check(q("""select count(*) from photo_person pp join photo p on p.id=pp.photo_id
               where pp.source='auto-face' and (p.visibility!='pending' or p.ingest_run_id is null)""")[0][0] == 0,
          "only pending, ingested photos are auto-tagged")
    check(q("select status from face_suggestion where id=?", pub_sid)[0][0] == "pending"
          and q("select count(*) from photo_person where photo_id=? and person_id=? and source='auto-face'",
                pub_photo, pub_person)[0][0] == 0,
          "a photo the family can already see is left as a suggestion")
    check(q("select count(*) from photo_person where person_id=? and source='auto-face'", pet_person)[0][0] == 0
          and st["pets_skipped"] > 0, "a person marked as a pet is never auto-tagged")
    check(q("select count(*) from face_suggestion where status='pending' and score<0.65")[0][0] == weak_before,
          "matches under 0.65 stay suggestions")
    check(q("""select count(*) from face_suggestion fs join face f on f.id=fs.face_id
               join photo_person pp on pp.photo_id=f.photo_id and pp.person_id=fs.person_id
               where fs.decided_by='auto-face' and fs.status='accepted' and pp.source='auto-face'""")[0][0] == st["tagged"],
          "each auto tag keeps its suggestion and score on record")
    check(identified() == refs_before, "auto tags are not used as references by the matcher")
    check(autotag.run()["tagged"] == 0, "a second run tags nothing more")
    at = admin.get("/api/admin/face-tags?auto_only=true&limit=500").json()
    check(len(at) == st["tagged"] and all(a["origin"] == "auto" for a in at),
          "the audit lists exactly the auto tags")
    a, b = at[0], at[1]
    admin.post("/api/admin/face-tags/confirm-auto", json={"pairs": [[a["photo_id"], a["person_id"]]]})
    check(q("select source from photo_person where photo_id=? and person_id=?", a["photo_id"], a["person_id"])[0][0]
          == "human-confirmed", "confirming makes it an ordinary tag")
    check(identified() == refs_before + 1, "…and a reference")
    admin.post("/api/admin/undo")
    check(q("select source from photo_person where photo_id=? and person_id=?", a["photo_id"], a["person_id"])[0][0]
          == "auto-face", "undo makes it an auto tag again")
    admin.post("/api/admin/face-queue/tiny-tags/remove", json={"pairs": [[b["photo_id"], b["person_id"]]]})
    check(q("""select count(*) from face_suggestion fs join face f on f.id=fs.face_id
               where f.photo_id=? and fs.person_id=? and fs.status='rejected'""", b["photo_id"], b["person_id"])[0][0] >= 1
          and autotag.run()["tagged"] == 0, "removing an auto tag rejects the guess: it is not re-applied")
    admin.post("/api/admin/undo")
    check(q("select source from photo_person where photo_id=? and person_id=?", b["photo_id"], b["person_id"])[0][0]
          == "auto-face", "undo of the removal restores the auto tag")
    check(q("select count(*) from photo_person where source!='auto-face'")[0][0] == tags_before,
          "no existing tag was changed")
    db.execute("update photo set visibility='pending' where id=?", (pub_photo,))
    db.execute("update person set notes=null where id=?", (pet_person,))
    db.commit()

print("== rescue ==")
g = max(un["groups"], key=lambda g: len(g["photos"]))
one, rest = g["photos"][0]["id"], [p["id"] for p in g["photos"][1:]]
r = admin.post("/api/admin/photos/rescue", json={"photo_ids": [one]}).json()
check(r["changed"] == 1, "rescue moves a Not selected photo")
outings.run()
check(q("select triage from photo where id=?", one)[0][0] == "rescued", "nightly triage leaves a rescued photo alone")
if rest:
    check({r[0] for r in q(f"select triage from photo where id in ({','.join(map(str, rest))})")} == {"related"},
          "its outing-mates follow it into the Inbox as related")
admin.post("/api/admin/undo")
outings.run()
check(q("select triage from photo where id=?", one)[0][0] == "unselected", "undo + rerun puts the outing back")

print("== event naming ==")
ib = admin.get("/api/admin/inbox").json()
g = max(ib["groups"], key=lambda g: len(g["photos"]))
oid, pids = g["outing_id"], [p["id"] for p in g["photos"]]
NAME = "Inbox Check Outing 9471"
ev_before = q("select count(*) from event")[0][0]
admin.put(f"/api/admin/outings/{oid}/event", json={"event_name": "Typo Nmae 9471"})
admin.put(f"/api/admin/outings/{oid}/event", json={"event_name": NAME})
check(q("select count(*) from event")[0][0] == ev_before, "naming an unpublished outing creates no event yet")
first, later = pids[:2], pids[2:]
r = admin.post("/api/admin/photos/publish", json={"photo_ids": first}).json()
eid = q("select id from event where name=?", NAME)
check(len(eid) == 1 and r["event_tagged"] == len(first), "publishing creates the event and tags the photos")
check(q("select count(*) from event where name like 'Typo%9471'")[0][0] == 0, "the corrected typo left nothing behind")
eid = eid[0][0]
admin.patch(f"/api/admin/events/{eid}", json={"name": NAME + " renamed"})
if later:
    admin.post("/api/admin/photos/publish", json={"photo_ids": later[:1]})
    check(q("select count(*) from photo_event where event_id=? and photo_id=?", eid, later[0])[0][0] == 1,
          "a later photo follows the event through a rename (no duplicate event)")
    check(q("select count(*) from event where name=?", NAME)[0][0] == 0, "…and the old name was not recreated")
    admin.post("/api/admin/undo")
    check(q("select visibility from photo where id=?", later[0])[0][0] == "pending"
          and q("select count(*) from photo_event where photo_id=? and event_id=?", later[0], eid)[0][0] == 0,
          "undo of that publish removes its event tag")
# The name applies at publish time only: clearing or changing it never touches photos
# already published, so part of an outing can go out under the event and part without.
admin.put(f"/api/admin/outings/{oid}/event", json={"event_name": None})
check(q("select count(*) from photo_event where event_id=?", eid)[0][0] == len(first),
      "clearing the name leaves the photos already published with their event")
if later:
    r = admin.post("/api/admin/photos/publish", json={"photo_ids": later[:1]}).json()
    check(r["event_tagged"] == 0
          and q("select count(*) from photo_event where photo_id=?", later[0])[0][0] == 0,
          "a photo published after the name is cleared gets no event")
    admin.post("/api/admin/undo")
    OTHER = "Inbox Check Other 9471"
    admin.put(f"/api/admin/outings/{oid}/event", json={"event_name": OTHER})
    admin.post("/api/admin/photos/publish", json={"photo_ids": later[:1]})
    oth = q("select id from event where name=?", OTHER)
    check(len(oth) == 1
          and q("select count(*) from photo_event where event_id=?", oth[0][0])[0][0] == 1
          and q("select count(*) from photo_event where event_id=?", eid)[0][0] == len(first),
          "a second name tags only the photos published under it")
    admin.post("/api/admin/undo")
    check(q("select count(*) from event where name=?", OTHER)[0][0] == 0, "undo removes the event it created")
    admin.post("/api/admin/undo")
admin.post("/api/admin/undo")
check(q("select event_name from outing where id=?", oid)[0][0] is not None, "undo restores the cleared name")

print("== reconcile ==")
sdb = SessionLocal()
now = datetime.now(timezone.utc)
# The fixture owns file_size. Whatever the source DB holds (NULL before the first real
# reconcile, real byte counts after it — OPS-2026-11-14-04), every B2 row gets a known
# size here, and a block of rows is set back to NULL so the backfill has work to do.
SIZE = lambda pid: 1000 + pid                                    # noqa: E731
db.execute("update photo set file_size = 1000 + id, master_missing = 0 where storage_backend='b2'")
unsized = [r[0] for r in q("select id from photo where storage_backend='b2' order by id desc limit 25")]
db.execute(f"update photo set file_size = null where id in ({','.join(map(str, unsized))})")
db.commit()
listing = {r["storage_path"]: (r["file_version"], SIZE(r["id"]), now - timedelta(days=900))
           for r in q("select id, storage_path, file_version from photo where storage_backend='b2'")}
rows = q("""select id, storage_path, file_version from photo where storage_backend='b2'
            and visibility='published' and ingest_run_id is null and file_version is not null
            order by id""")
keys = set(listing)
pl = reconcile.plan(sdb, listing, keys)
check(not pl["moved"] and not pl["deleted"] and not pl["replaced"], "an unchanged bucket plans nothing")
check(set(pl["sizes"]) == set(unsized), f"exactly the unsized rows are queued for backfill ({len(pl['sizes'])})")
reconcile.backfill_sizes(sdb, pl)
check(q("select count(*) from photo where storage_backend='b2' and file_size is null")[0][0] == 0
      and q("select file_size from photo where id=?", unsized[0])[0][0] == SIZE(unsized[0]),
      "backfill fills them from the listing")

mv, mv2, gone, repl, amb1, amb2 = rows[0], rows[1], rows[2], rows[3], rows[4], rows[5]
new_key = "Photo Album/2099/2099-01/2099-01-01/" + mv["storage_path"].rsplit("/", 1)[-1]
ren_key = mv2["storage_path"].rsplit("/", 1)[0] + "/renamed-9471.jpg"
L = dict(listing)
L[new_key] = (L.pop(mv["storage_path"])[0], SIZE(mv["id"]), now)             # moved: same ETag
L[ren_key] = ("multipart-etag-2", L.pop(mv2["storage_path"])[1], now)         # renamed: size + folder
del L[gone["storage_path"]]                                                    # deleted
L[repl["storage_path"]] = ("newetag9471", 5, now)                              # replaced
# two vanished rows whose only candidate is one ambiguous key -> neither may claim it
dup = "Photo Album/2099/dup-9471.jpg"
db.execute("update photo set file_version='same-etag-9471' where id in (?,?)", (amb1["id"], amb2["id"]))
db.commit()
del L[amb1["storage_path"]], L[amb2["storage_path"]]
L[dup] = ("same-etag-9471", 77, now)
K = set(L)
for d_ in (settings.thumbnails_dir, settings.display_dir):       # a cached derivative to carry over
    f = d_ / derivatives.cache_key(mv["storage_path"], mv["file_version"], versioned=True)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
sdb.expire_all()
pl = reconcile.plan(sdb, L, K)
check({x["id"]: x["new"] for x in pl["moved"]} == {mv["id"]: new_key, mv2["id"]: ren_key},
      "a move (same ETag) and a rename (same size + folder) are both recognised")
check({x["id"] for x in pl["deleted"]} == {gone["id"], amb1["id"], amb2["id"]},
      "a vanished master with no match — or an ambiguous one — is a delete, not a move")
check([x["id"] for x in pl["replaced"]] == [repl["id"]], "a changed ETag is a replace")
tags_before = q("select count(*) from photo_person where photo_id=?", mv["id"])[0][0]
reconcile.apply_moves(sdb, pl)
got = q("select storage_path, source_file, file_version, visibility from photo where id=?", mv["id"])[0]
check(got["storage_path"] == new_key and got["source_file"] == new_key and got["visibility"] == "published"
      and q("select count(*) from photo_person where photo_id=?", mv["id"])[0][0] == tags_before,
      "a moved photo is re-pointed and keeps its tags and visibility")
check((settings.thumbnails_dir / derivatives.cache_key(new_key, mv["file_version"], versioned=True)).exists(),
      "its cached derivatives move with it")
r = reconcile.apply_missing(sdb, pl)
check(r["culled"] == 3 and q("select visibility, culled_from, culled_by, master_missing from photo where id=?",
                             gone["id"])[0][:] == ("culled", "published", reconcile.AUTO_CULL_BY, 1),
      "a deleted master is culled, restorable, and says why")
admin.post("/api/admin/photos/restore", json={"photo_ids": [gone["id"]]})
sdb.expire_all()
pl2 = reconcile.plan(sdb, L, K)
check(not pl2["deleted"], "a photo restored by an admin is not culled again the next night")
check(pl2["missing"] and gone["id"] in pl2["missing"], "…but it is still listed as missing")
db.execute("update photo set visibility='culled', culled_from='published', culled_by=? where id=?",
           (reconcile.AUTO_CULL_BY, amb1["id"]))
db.commit()
Lback = dict(L)
Lback[amb1["storage_path"]] = ("same-etag-9471", 77, now)        # the file is put back
Lback[amb2["storage_path"]] = ("same-etag-9471", 77, now)
admin.post("/api/admin/photos/cull", json={"photo_ids": [amb2["id"]]})   # amb2: restore, then an ADMIN culls it
admin.post("/api/admin/photos/restore", json={"photo_ids": [amb2["id"]]})
admin.post("/api/admin/photos/cull", json={"photo_ids": [amb2["id"]]})
sdb.expire_all()
r = reconcile.apply_missing(sdb, reconcile.plan(sdb, Lback, set(Lback)))
check(q("select visibility, master_missing from photo where id=?", amb1["id"])[0][:] == ("published", 0),
      "when the master comes back, a photo the ingest culled returns by itself")
check(q("select visibility from photo where id=?", amb2["id"])[0][0] == "culled",
      "…but one an admin culled stays culled")
import json                                                       # noqa: E402
db.execute("insert into ingest_run (started_at, state, listed, candidates, new, errors, detail) "
           "values (datetime('now'),'ok',0,0,0,0,?)",
           (json.dumps({"reconcile": reconcile.summary(pl2)}),))
db.commit()
mt = admin.get("/api/admin/inbox?tab=missing").json()
check(mt["counts"]["missing"] == len(pl2["missing"])
      and gone["id"] in {p["id"] for g in mt["groups"] for p in g["photos"]},
      "the Missing from B2 tab lists what the last run found")
L2 = {k: v for k, v in L.items() if k not in {r["storage_path"] for r in rows[10:20]}}
pl3 = reconcile.plan(sdb, L2, set(L2))
r = reconcile.apply_missing(sdb, pl3)
check(r["guard"] and r["culled"] == 0
      and q(f"select count(*) from photo where visibility='culled' and id in ({','.join(str(r['id']) for r in rows[10:20])})")[0][0] == 0,
      "10 masters vanishing at once trips the guard: nothing culled")
sdb.close()

print("\nALL CHECKS PASSED" if not fails else f"\n{len(fails)} CHECK(S) FAILED:\n  " + "\n  ".join(fails))
sys.exit(1 if fails else 0)
