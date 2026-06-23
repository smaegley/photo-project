"""Maegley Photo Album — FastAPI app (SPEC §6)."""
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import admin, facets, images, photos

app = FastAPI(title="Maegley Photo Album", version="1.0.0",
              docs_url="/api/docs", redoc_url=None)

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
