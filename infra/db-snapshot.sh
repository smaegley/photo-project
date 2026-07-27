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
VTMP=$(mktemp /tmp/photos-verify.XXXXXX.db)
# Staged filename: the snapshot is built and verified as .partial and only moved to
# its real name once it passes. Two reasons. (1) A reader — notably the off-site sync
# — can never pick up a half-written or unverified .gz, since the mv is atomic within
# the directory and .partial doesn't match the rotation glob. (2) A failing run can
# never delete a pre-existing good snapshot that happens to share its timestamp.
PART="$OUT.partial"
trap 'rm -f "$TMP" "$VTMP" "$PART"' EXIT

# Consistent online snapshot (won't tear concurrent writes), via stdlib sqlite3.
python3 - "$DB" "$TMP" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src); d = sqlite3.connect(dst)
with d:
    s.backup(d)
s.close(); d.close()
PY
gzip -c "$TMP" > "$PART"

# Verify the FINISHED ARTIFACT, not the source.
#
# This block used to read the photo count from the *source* connection, so the
# reassuring "1140 photos" in the log said nothing whatsoever about the file just
# written — a truncated or corrupt snapshot logged success indefinitely. It matters
# more now that these snapshots are replicated off-site: replication faithfully
# copies a bad snapshot, and without this check nothing would ever notice.
#
# So: gunzip the real artifact back, open it, integrity-check it, and refuse to keep
# it if it's bad. A non-zero exit here makes photo-backup.service fail visibly rather
# than leaving a broken .gz to be discovered during a restore.
gunzip -c "$PART" > "$VTMP"
if ! PHOTOS=$(python3 - "$VTMP" <<'PY'
import sqlite3, sys
try:
    c = sqlite3.connect(sys.argv[1])
    ok = c.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        sys.exit(f"integrity_check: {ok}")
    n = c.execute("SELECT count(*) FROM photo").fetchone()[0]
    c.close()
except sqlite3.DatabaseError as e:
    # A badly truncated file raises here rather than failing integrity_check,
    # so catch it for a readable message instead of a traceback.
    sys.exit(f"unreadable: {e}")
if n == 0:
    sys.exit("snapshot contains 0 photos")
print(n)
PY
); then
  echo "ERROR: snapshot verification FAILED — discarding $PART" >&2
  rm -f "$PART"
  exit 1
fi

mv "$PART" "$OUT"   # atomic: only a verified snapshot ever appears under the real name
echo "snapshot: $OUT ($(du -h "$OUT" | cut -f1), ${PHOTOS} photos, integrity ok)"

# Rotate — keep the newest $KEEP (timestamped names sort chronologically).
mapfile -t FILES < <(ls -1 "$OUTDIR"/photos-*.db.gz 2>/dev/null | sort)
COUNT=${#FILES[@]}
if (( COUNT > KEEP )); then
  for f in "${FILES[@]:0:$((COUNT - KEEP))}"; do
    rm -f "$f"
    echo "pruned old snapshot: $(basename "$f")"
  done
fi
