#!/bin/bash
# Publish the result of a systemd job as a retained MQTT message (SPEC §15.12).
# Called as ExecStopPost=, so it runs after every attempt, including ones the job never
# got to report on (OOM kill, timeout, failed pre-run snapshot).
#
#   job-status.sh ingest     -> photo-album/ingest/status   (photo-ingest.service)
#   job-status.sh backup     -> photo-album/backup/status   (photo-backup.service)
#
#   payload {"state":"ok|failed","finished_at":"<ISO8601 UTC, with Z>","new":N,
#            "errors":N,"result":"<systemd $SERVICE_RESULT>"}      retained, QoS 1
#
# ingest: state is "ok" only when systemd says the unit succeeded AND the app's last
#         INGEST_RESULT line says ok; new/errors come from that line (0 if the app
#         never printed one). errors > 0 with state ok = individual files failed.
# backup: state follows systemd alone; new/errors are 0.
# Home Assistant (ops: packages/photo_album_alerts.yaml) alerts on state=failed, and at
# 09:00 Mountain when finished_at is over 26 h old. finished_at MUST carry a zone.
#
# Environment (EnvironmentFile=/etc/photo-album/mqtt.env — ops-owned, never in git):
#   MQTT_HOST (required)  MQTT_PORT (1883)  MQTT_USER / MQTT_PASS
# Set by systemd: SERVICE_RESULT, INVOCATION_ID.
# JOB_STATUS_DRY_RUN=1 prints the payload instead of publishing.
# This script always exits 0: a status that cannot be published must never turn a good
# run into a failed unit — the dead-man alert is the backstop.
set -uo pipefail

JOB="${1:-}"
case "$JOB" in
  ingest|backup) ;;
  *) echo "job-status: usage: job-status.sh ingest|backup" >&2; exit 0 ;;
esac
TOPIC="photo-album/${JOB}/status"
RESULT="${SERVICE_RESULT:-unknown}"

LINE=""
if [ -n "${JOB_STATUS_LINE:-}" ]; then                 # for testing without a journal
  LINE="$JOB_STATUS_LINE"
elif [ "$JOB" = "ingest" ] && [ -n "${INVOCATION_ID:-}" ]; then
  LINE=$(journalctl "_SYSTEMD_INVOCATION_ID=${INVOCATION_ID}" -o cat --no-pager 2>/dev/null \
         | grep '^INGEST_RESULT ' | tail -1)
fi

PAYLOAD=$(JOB="$JOB" RESULT="$RESULT" LINE="$LINE" python3 - <<'PY'
import json, os
from datetime import datetime, timezone
app = {}
line = os.environ.get("LINE", "")
if line.startswith("INGEST_RESULT "):
    try:
        app = json.loads(line[len("INGEST_RESULT "):])
    except ValueError:
        app = {}
result = os.environ.get("RESULT", "unknown")
ok = result == "success" and (os.environ["JOB"] != "ingest" or app.get("state") == "ok")
print(json.dumps({
    "state": "ok" if ok else "failed",
    "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "new": int(app.get("new", 0) or 0),
    "errors": int(app.get("errors", 0) or 0),
    "result": result,
}))
PY
)

if [ "${JOB_STATUS_DRY_RUN:-}" = "1" ]; then
  echo "$TOPIC $PAYLOAD"
  exit 0
fi
if [ -z "${MQTT_HOST:-}" ]; then
  echo "job-status: MQTT_HOST not set — status not published: $PAYLOAD" >&2
  exit 0                      # never turn a good ingest into a failed unit
fi
ARGS=(-h "$MQTT_HOST" -p "${MQTT_PORT:-1883}" -t "$TOPIC" -r -q 1 -m "$PAYLOAD")
[ -n "${MQTT_USER:-}" ] && ARGS+=(-u "$MQTT_USER" -P "${MQTT_PASS:-}")
if ! timeout 30 mosquitto_pub "${ARGS[@]}"; then
  echo "job-status: publish failed: $PAYLOAD" >&2
fi
echo "job-status: $PAYLOAD"
exit 0
