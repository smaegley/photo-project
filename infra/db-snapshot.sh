#!/bin/bash
# Snapshot the Maegley Photo Album SQLite DB (online-safe via sqlite3 .backup),
# gzip it, and rotate. Run by photo-backup.timer; also safe to run by hand.
#
#   DB     — path to the live DB        (default: /opt/photo-project/data/photos.db)
#   OUTDIR — where snapshots are kept   (default: /opt/photo-project/snapshots)
#   KEEP   — how many to retain         (default: 14)
#
# The LXC/Proxmox backups are the off-site layer; this is the fast local restore
# point. NOTE: never cp/mv the live DB while the app is running — the online
# backup API is the correct snapshot mechanism. Uses python3 (stdlib sqlite3),
# so no sqlite3 CLI package is required on the host.
set -euo pipefail

DB="${DB:-/opt/photo-project/data/photos.db}"
OUTDIR="${OUTDIR:-/opt/photo-project/snapshots}"
KEEP="${KEEP:-14}"
TS=$(date +%Y%m%d-%H%M%S)
OUT="$OUTDIR/photos-${TS}.db.gz"

mkdir -p "$OUTDIR"

if [ ! -f "$DB" ]; then
  echo "ERROR: DB not found at $DB" >&2
  exit 1
fi

TMP=$(mktemp /tmp/photos-snap.XXXXXX.db)
trap 'rm -f "$TMP"' EXIT

# Consistent online snapshot (won't tear concurrent writes), via stdlib sqlite3.
PHOTOS=$(python3 - "$DB" "$TMP" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src); d = sqlite3.connect(dst)
with d:
    s.backup(d)
print(s.execute("SELECT count(*) FROM photo").fetchone()[0])
s.close(); d.close()
PY
)
gzip -c "$TMP" > "$OUT"
echo "snapshot: $OUT ($(du -h "$OUT" | cut -f1), ${PHOTOS} photos)"

# Rotate — keep the newest $KEEP (timestamped names sort chronologically).
mapfile -t FILES < <(ls -1 "$OUTDIR"/photos-*.db.gz 2>/dev/null | sort)
COUNT=${#FILES[@]}
if (( COUNT > KEEP )); then
  for f in "${FILES[@]:0:$((COUNT - KEEP))}"; do
    rm -f "$f"
    echo "pruned old snapshot: $(basename "$f")"
  done
fi
