# Maegley Photo Album — API image (FastAPI + SQLite).
# The SPA is built and served by the separate Caddy image (frontend/Dockerfile).
FROM python:3.12-slim AS api

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


# ---- ingest image (SPEC §15.12) -------------------------------------------------
# The website image above stays small and ML-free; this target adds the face models'
# runtime on top of it for the one-shot `ingest` service. ONNX only — no torch.
# Model WEIGHTS are not baked in and are never downloaded at run time: they are
# installed once on the host and mounted read-only (see docker-compose.yml).
FROM api AS ingest

COPY backend/requirements-ingest.txt /app/requirements-ingest.txt
# insightface compiles a small Cython extension, so the compiler is needed for the
# install only; it is removed in the same layer to keep the image down.
RUN apt-get update \
 && apt-get install -y --no-install-recommends g++ libgomp1 \
 && pip install --no-cache-dir -r /app/requirements-ingest.txt \
 # insightface drags in the GUI build of OpenCV, which needs X11 libs (libxcb) that a
 # slim image doesn't have and shadows the headless one. Keep only headless.
 && pip uninstall -y opencv-python opencv-python-headless \
 && pip install --no-cache-dir opencv-python-headless==4.13.0.92 \
 && apt-get purge -y g++ && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# NOT the api entrypoint: that one ignores its arguments, migrates and starts uvicorn
# (STATUS gotcha). Ingest runs exactly the module it is given and exits.
ENTRYPOINT ["python", "-m"]
CMD ["app.detect_faces"]
