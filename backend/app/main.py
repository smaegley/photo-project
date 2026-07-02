"""Maegley Photo Album — FastAPI app (SPEC §6)."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import admin, facets, images, photos

USAGE_RETENTION_DAYS = 180


@asynccontextmanager
async def lifespan(app: FastAPI):
    # created_at is stored naive-UTC, so compare against a naive cutoff
    cutoff = (datetime.now(timezone.utc) - timedelta(days=USAGE_RETENTION_DAYS)).replace(tzinfo=None)
    from app.database import SessionLocal
    from app import models as m
    db = SessionLocal()
    try:
        n = (db.query(m.UsageEvent)
             .filter(m.UsageEvent.created_at < cutoff)
             .delete(synchronize_session=False))
        db.commit()
        if n:
            print(f"[usage] pruned {n} events older than {USAGE_RETENTION_DAYS}d")
    finally:
        db.close()
    yield


app = FastAPI(title="Maegley Photo Album", version="1.0.0",
              docs_url="/api/docs", redoc_url=None, lifespan=lifespan)

# Dev convenience: the React dev server runs on another port. In production the
# SPA is served by Caddy from the same origin, so this is harmless.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(photos.router)
app.include_router(facets.router)
app.include_router(images.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "maegley-photo-album",
        "auth": "cloudflare-access" if settings.cf_access_enabled else "dev-bypass",
        "time": datetime.now(timezone.utc).isoformat(),
    }
