#!/bin/sh
# Apply DB migrations, then start the API. The importer/geocode/thumbnail steps
# are one-time data jobs run manually (see README / infra/DEPLOY.md), not here.
set -e

echo "[entrypoint] alembic upgrade head (DB_PATH=$DB_PATH)"
alembic upgrade head

echo "[entrypoint] starting uvicorn on :8077"
exec uvicorn app.main:app --host 0.0.0.0 --port 8077
