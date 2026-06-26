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

    @property
    def cf_access_enabled(self) -> bool:
        return bool(self.cf_access_team_domain and self.cf_access_aud)

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def slides_dir(self) -> Path:
        return Path(self.library_root) / "slides"

    @property
    def cards_dir(self) -> Path:
        return Path(self.library_root) / "index_cards"

    @property
    def thumbnails_dir(self) -> Path:
        return Path(self.library_root) / "thumbnails"

    model_config = {"extra": "ignore"}


settings = Settings()
