# Maegley Photo Album — API image (FastAPI + SQLite).
# The SPA is built and served by the separate Caddy image (frontend/Dockerfile).
FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

# Dependencies first for layer caching.
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# App + migrations (alembic.ini and alembic/ live at the backend root).
COPY backend/ /app/

ENV PYTHONPATH=/app
# Overridden by compose; defaults match the mounted volume layout.
ENV DB_PATH=/data/photos.db
ENV LIBRARY_ROOT=/mnt/photos/library

EXPOSE 8077

# Runs alembic upgrade head, then uvicorn (see entrypoint.sh).
ENTRYPOINT ["/app/entrypoint.sh"]
