"""Does the WEBSITE image have everything the website's code needs? (2026-11-13)

Run this INSIDE the `api` image — never trust dev's virtualenv, which also carries the
ML stack. Two checks:

1. Every import statement anywhere in the request-serving modules (including imports
   tucked inside functions) resolves.
2. Every GET route, admin ones included, answers without a 5xx for an admin, against a
   throwaway copy of the database.

    docker compose exec api python -m tests.check_api_image
    (dev: docker build --target api -t x . && docker run --rm --entrypoint python \
          -v $PWD/data:/data -v /mnt/photos/library:/mnt/photos/library:ro x -m tests.check_api_image)
"""
import ast
import importlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
fails: list[str] = []

# Point the app at a throwaway DB copy BEFORE anything imports app.config — the import
# check below loads it, and settings are read once.
SRC = Path(os.environ.get("DB_PATH") or ROOT.parent / "data" / "photos.db")
tmp = Path(tempfile.mkdtemp(prefix="api-image-")) / "photos.db"
with sqlite3.connect(f"file:{SRC}?mode=ro", uri=True) as s, sqlite3.connect(tmp) as d:
    s.backup(d)
os.environ["DB_PATH"] = str(tmp)
os.environ["CF_ACCESS_TEAM_DOMAIN"] = os.environ["CF_ACCESS_AUD"] = ""

# ---- 1. imports -----------------------------------------------------------------
SERVING = ["app/main.py", "app/auth.py", "app/queries.py", "app/schemas.py", "app/storage.py",
           "app/derivatives.py", "app/family.py", "app/geocoding.py", *sorted(
               str(p.relative_to(ROOT)) for p in (ROOT / "app" / "routers").glob("*.py"))]
seen: set[str] = set()
for rel in SERVING:
    for node in ast.walk(ast.parse((ROOT / rel).read_text())):
        mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module] if isinstance(node, ast.ImportFrom) and node.module and not node.level
                else [])
        for mod in mods:
            if mod in seen:
                continue
            seen.add(mod)
            try:
                importlib.import_module(mod)
            except Exception as e:  # noqa: BLE001
                fails.append(f"import {mod} (used in {rel}): {type(e).__name__}: {e}")
print(f"imports: {len(seen)} modules checked, {len(fails)} missing")

# ---- 2. routes ------------------------------------------------------------------

from fastapi.testclient import TestClient      # noqa: E402
from app.main import app                       # noqa: E402
from app.config import settings                # noqa: E402
assert settings.db_path == str(tmp), "refusing to run against the real database"
c = TestClient(app, raise_server_exceptions=False)   # report every 500, don't stop at the first
assert c.get("/api/me").json()["role"] == "admin"

db = sqlite3.connect(tmp)
one = lambda q: (db.execute(q).fetchone() or [None])[0]   # noqa: E731
fill = {"person_id": one("select person_id from photo_person where region_w is not null limit 1"),
        "cluster_id": one("select id from face_cluster limit 1"),
        "photo_id": one("select id from photo where visibility='published' limit 1"),
        "face_id": one("select id from face limit 1"),
        "event_id": one("select id from event limit 1"),
        "place_id": one("select id from place limit 1")}
SKIP = {"/api/logout", "/api/dev/switch", "/api/admin/geocode"}     # redirects / external call
n = 0
for route in app.routes:
    path = getattr(route, "path", "")
    if "GET" not in getattr(route, "methods", ()) or not path.startswith("/api") or path in SKIP:
        continue
    if ":path}" in path or "{filename}" in path or "{user_id}" in path:
        continue                                # file-serving routes: covered by check_visibility
    url = path
    for k, v in fill.items():
        url = url.replace("{" + k + "}", str(v))
    if "{" in url:
        continue
    r = c.get(url)
    n += 1
    if r.status_code >= 500:
        fails.append(f"GET {url} -> {r.status_code}")
# the two POST routes that also do in-process scoring
for url, body in (("/api/admin/faces/assign", {"face_ids": [], "action": "ignore"}),
                  ("/api/admin/face-clusters/decide", {"cluster_ids": [], "action": "ignore"})):
    r = c.post(url, json=body)
    n += 1
    if r.status_code >= 500:
        fails.append(f"POST {url} -> {r.status_code}")
print(f"routes: {n} requested, {sum(1 for f in fails if f.startswith(('GET', 'POST')))} server errors")

for f in fails:
    print("  FAIL", f)
print("PASSED" if not fails else f"FAILED: {len(fails)}")
sys.exit(1 if fails else 0)
