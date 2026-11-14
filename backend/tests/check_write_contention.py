"""The site must stay writable while a face/ingest job runs (SPEC §15.16).

SQLite allows ONE writer. Every authenticated API request may write (`last_login`), so
a batch job that keeps a write transaction open while it does slow work locks the whole
site out — which is exactly what took prod down on 2026-11-13 (detect_faces committed
every 500 faces ≈ 5–7 minutes; 180 "database is locked" errors).

This runs the real job against a throwaway DB copy and, concurrently, a writer that
behaves like the API but with a much shorter patience (1 s instead of 5 s). Any failed
write, or any write that waits more than 1 s, fails the check.

    cd backend && ../.venv/bin/python -m tests.check_write_contention [--photos 40]
"""
import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--photos", type=int, default=40)
args = ap.parse_args()

SRC = Path(os.environ.get("DB_PATH") or Path(__file__).resolve().parents[2] / "data" / "photos.db")
tmp = Path(tempfile.mkdtemp(prefix="contention-")) / "photos.db"
with sqlite3.connect(f"file:{SRC}?mode=ro", uri=True) as s, sqlite3.connect(tmp) as d:
    s.backup(d)
    d.execute("pragma journal_mode=WAL")
    # make the newest N digital photos look unscanned so the job has real work
    d.execute("""update photo set faces_scanned_version=null where id in
                 (select id from photo where origin='digital' and visibility='published'
                  order by id desc limit ?)""", (args.photos,))
    # ...and every ingested photo look ungrouped, so app.outings rewrites them all
    d.execute("update photo set outing_id=null, triage=null where ingest_run_id is not null")
    d.execute("""update photo set junk_score=null where id in
                 (select id from photo where ingest_run_id is not null order by id desc limit ?)""",
              (args.photos,))
    d.execute("delete from outing")
    d.commit()

stop = threading.Event()
phase = ["startup"]
waits: list[float] = []
failures: list[str] = []


def writer():
    """An impatient stand-in for the API's last_login write."""
    con = sqlite3.connect(tmp, timeout=1.0)
    con.execute("pragma busy_timeout=1000")
    uid = con.execute("select id from user order by id limit 1").fetchone()[0]
    while not stop.is_set():
        t = time.time()
        try:
            con.execute("update user set last_login=? where id=?", (time.strftime("%Y-%m-%d %H:%M:%S"), uid))
            con.commit()
            waits.append(time.time() - t)
        except sqlite3.OperationalError as e:
            con.rollback()
            failures.append(f"during {phase[0]}: {time.time() - t:.2f}s: {e}")
        time.sleep(0.15)
    con.close()


th = threading.Thread(target=writer, daemon=True)
th.start()
env = dict(os.environ, DB_PATH=str(tmp), PYTHONPATH=str(Path(__file__).resolve().parents[1]))
t0 = time.time()
ok = True
for mod in (["app.detect_faces", "--limit", str(args.photos)], ["app.match_faces"], ["app.cluster_faces"],
            ["app.junk"], ["app.outings"]):
    phase[0] = mod[0]
    r = subprocess.run([sys.executable, "-m", *mod], env=env, capture_output=True, text=True,
                       cwd=Path(__file__).resolve().parents[1])
    tail = (r.stdout.strip().splitlines() or [""])[-1][:90]
    print(f"  {mod[0]:<22} exit {r.returncode}  {time.time() - t0:5.0f}s  {tail}")
    if r.returncode != 0:
        ok = False
        print(r.stderr[-600:])
stop.set()
th.join(timeout=5)

worst = max(waits) if waits else 0.0
print(f"concurrent writes: {len(waits)} ok, {len(failures)} failed, worst wait {worst:.2f}s")
for f in failures[:5]:
    print("   locked:", f)
passed = ok and not failures and worst < 1.0 and len(waits) > 20
print("PASSED" if passed else "FAILED")
sys.exit(0 if passed else 1)
