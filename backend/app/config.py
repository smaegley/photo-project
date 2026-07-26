from pathlib import Path

from pydantic_settings import BaseSettings

# Repo layout: backend/app/config.py -> repo root is two parents up.
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # SQLite per SPEC §6 (low RAM, D1-compatible). Default lives in repo data/ for dev;
    # in the container this is overridden to the mounted /data volume.
    db_path: str = str(REPO_ROOT / "data" / "photos.db")

    # Image library (SPEC §3.1 / §8): slides, index cards, thumbnails. Mounted
    # read-write — the app rotates slides in place and (re)generates thumbnails.
    library_root: str = "/mnt/photos/library"

    # Cloudflare Access (SPEC §6.3). When both are set, JWT verification is enforced;
    # otherwise the app runs in local-dev mode as the dev user below.
    cf_access_team_domain: str = ""
    cf_access_aud: str = ""
    dev_user_email: str = "steve@maegley.com"
    dev_user_role: str = "admin"
    dev_user_person_id: str = "steve"  # links the dev user to their tree person ("Self")

    # Backblaze B2 — masters for origin=digital (SPEC §13). Read-only, S3-compatible.
    # All blank by default (local-only serving unchanged); set in backend/.env (dev)
    # or the prod .env when the digital tier is built.
    b2_key_id: str = ""
    b2_app_key: str = ""       # secret — .env only, never committed
    b2_bucket: str = ""
    b2_endpoint: str = ""      # e.g. s3.us-west-001.backblazeb2.com

    @property
    def cf_access_enabled(self) -> bool:
        return bool(self.cf_access_team_domain and self.cf_access_aud)

    @property
    def b2_enabled(self) -> bool:
        """True once B2 is configured — gates any origin=digital serving (SPEC §13)."""
        return bool(self.b2_key_id and self.b2_app_key and self.b2_bucket and self.b2_endpoint)

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def slides_dir(self) -> Path:
        return Path(self.library_root) / "slides"

    @property
    def library_root_path(self) -> Path:
        return Path(self.library_root)

    @property
    def cards_dir(self) -> Path:
        return Path(self.library_root) / "index_cards"

    @property
    def thumbnails_dir(self) -> Path:
        return Path(self.library_root) / "thumbnails"

    @property
    def display_dir(self) -> Path:
        # Sized lightbox derivatives (SPEC §11.5) — originals can be 24MP.
        return Path(self.library_root) / "display"

    @property
    def faces_dir(self) -> Path:
        # Cropped face thumbnails for the People filter (SPEC §4.2).
        return Path(self.library_root) / "faces"

    @property
    def card_thumbs_dir(self) -> Path:
        # Sized index-card derivatives for grid/panel views (full-res via /api/cards).
        return Path(self.library_root) / "card_thumbs"

    # Load backend/.env when present (dev). Prod injects the same keys as env vars via
    # docker-compose, which take precedence; a missing env_file is silently ignored, so
    # this changes nothing until a real backend/.env exists.
    model_config = {"env_file": str(REPO_ROOT / "backend" / ".env"), "extra": "ignore"}


settings = Settings()
