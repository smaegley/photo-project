#!/usr/bin/env bash
# Refresh the DEV database from a PROD snapshot.  ***prod -> dev, ONE DIRECTION.***
#
# This script is LOAD-ONLY by design: it never connects to or writes prod, so a
# stray run can't clobber the production DB. (Promoting dev -> prod is a separate,
# deliberate, rare event — see infra/DEPLOY.md, not this.)
#
# Prereq: a prod snapshot has been delivered to this box, e.g. by the Ops Agent:
#   scp /opt/photo-project/snapshots/photos-<TS>.db.gz \
#       aiuser@10.0.1.121:/home/aiuser/projects/photo-project/data/incoming/
#
# What it does: backs up the current dev DB, materializes the snapshot into
# data/photos.db (integrity-checked), then runs `alembic upgrade head` so any
# dev-only newer migrations apply on top of prod's schema.
#
# Usage:
#   scripts/load-prod-snapshot.sh [snapshot.db.gz | snapshot.db]
#   (no arg = newest data/incoming/photos-*.db.gz)
set -euo pipefail

REPO="/home/aiuser/projects/photo-project"
DATA="$REPO/data"
INCOMING="$DATA/incoming"
mkdir -p "$INCOMING"
cd "$REPO"

SNAP="${1:-}"
if [ -z "$SNAP" ]; then
  SNAP=$(ls -1t "$INCOMING"/photos-*.db.gz 2>/dev/null | head -1 || true)
fi
if [ -z "$SNAP" ] || [ ! -f "$SNAP" ]; then
  echo "ERROR: no snapshot found. Pass a path, or drop one into $INCOMING" >&2
  exit 1
fi
echo "Loading prod snapshot: $SNAP"

# 1) Materialize into a temp DB (handle .gz or plain .db) and integrity-check it
#    BEFORE touching the live dev DB.
TMP=$(mktemp "$DATA/.prodsnap.XXXXXX.db")
trap 'rm -f "$TMP"' EXIT
case "$SNAP" in
  *.gz) gunzip -c "$SNAP" > "$TMP" ;;
  *)    cp "$SNAP" "$TMP" ;;
esac
. "$REPO/.venv/bin/activate"
python - "$TMP" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
ok = c.execute("PRAGMA integrity_check").fetchone()[0]
assert ok == "ok", f"integrity_check: {ok}"
print(f"  integrity ok; photos={c.execute('SELECT count(*) FROM photo').fetchone()[0]}, "
      f"people_tags={c.execute('SELECT count(*) FROM photo_person').fetchone()[0]}")
c.close()
PY

# 2) Back up the current dev DB, then swap the snapshot in.
if [ -f "$DATA/photos.db" ]; then
  BK="$DATA/photos.db.devbak-$(date +%Y%m%d-%H%M%S)"
  cp "$DATA/photos.db" "$BK"
  echo "  dev DB backed up -> $BK"
fi
mv "$TMP" "$DATA/photos.db"
trap - EXIT

# 3) Apply any dev-only newer migrations on top of prod's schema.
( cd "$REPO/backend" && alembic upgrade head )

echo
echo "Dev DB refreshed from prod. If the dev servers are running, restart the backend"
echo "so it reopens the DB:  (cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8077)"
echo "Slides rarely change; if prod re-exported any, rsync them and run:"
echo "  python -m importer.make_thumbnails   # pre-warm derivatives"
