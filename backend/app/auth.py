"""Auth — Cloudflare Access (SPEC §6.3).

In production the app sits behind Cloudflare Access (Zero Trust); every request
carries a signed `Cf-Access-Jwt-Assertion` header. We verify it against the
team's public keys, read the verified email, and map it to a user + role.

For local dev (no Access in front) the verification is bypassed and requests act
as a configurable dev admin — controlled purely by whether CF Access settings
are present, so production is never accidentally open.
"""
import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app import models as m

logger = logging.getLogger("app.auth")

_jwk_client: jwt.PyJWKClient | None = None


def _certs_url() -> str:
    return f"https://{settings.cf_access_team_domain}/cdn-cgi/access/certs"


def _verify_access_jwt(token: str) -> str:
    """Return the verified email from a Cloudflare Access JWT, or raise 401."""
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = jwt.PyJWKClient(_certs_url())
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, signing_key.key, algorithms=["RS256"],
            audience=settings.cf_access_aud,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Access JWT rejected: %s", exc)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid Access token")
    email = claims.get("email")
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="No email in token")
    return email


def _identify(cf_jwt: str | None, dev_override: str | None) -> tuple[str, bool, str | None, bool]:
    """Resolve the caller's email without touching the DB.
    Returns (email, dev_mode, forced_role, impersonating)."""
    dev = not settings.cf_access_enabled
    if not dev:
        if not cf_jwt:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                detail="Missing Cloudflare Access assertion")
        return _verify_access_jwt(cf_jwt), False, None, False
    # dev_override cookie lets the dev switcher impersonate any registered user
    # without editing config; ignored in prod where CF Access JWT governs all.
    email = dev_override or settings.dev_user_email
    role = settings.dev_user_role if not dev_override else None
    return email, True, role, bool(dev_override)


def _load_user(db: Session, ident: tuple[str, bool, str | None, bool], *, touch: bool) -> m.User:
    """Map an identity to a User row (creating viewers on first sight).

    `touch=False` skips the last_login write. Image routes pass it: the gallery
    fires ~60 tile requests at once, and when last_login has aged past the window
    every one of them takes the write path (the check-then-write below is not
    atomic across concurrent requests), serialising them all on SQLite's single
    writer. last_login is a coarse "seen" timestamp — the /api/photos call that
    renders the page keeps it fresh, so tiles don't need to.
    """
    email, dev, role, impersonating = ident
    now = datetime.now(timezone.utc)
    user = db.query(m.User).filter(m.User.email == email).first()
    if user is None:
        user = m.User(email=email, role=role or "viewer",
                      person_id=settings.dev_user_person_id if dev else None,
                      invited_at=now, last_login=now)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    dirty = False
    if role and user.role != role:  # dev override keeps the dev user as admin
        user.role = role
        dirty = True
    if dev and not impersonating and not user.person_id:  # backfill default dev user's tree link
        user.person_id = settings.dev_user_person_id
        dirty = True
    if touch:
        # Refreshing at most every 15 min keeps reads from turning into writes.
        last = user.last_login
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)   # SQLite returns naive datetimes
        if last is None or now - last > timedelta(minutes=15):
            user.last_login = now
            dirty = True
    if dirty:
        db.commit()
    return user


def current_user(
    db: Session = Depends(get_db),
    cf_jwt: str | None = Header(default=None, alias="Cf-Access-Jwt-Assertion"),
    dev_override: str | None = Cookie(default=None),
) -> m.User:
    """Resolve the request to a User row (creating viewers on first sight)."""
    return _load_user(db, _identify(cf_jwt, dev_override), touch=True)


def image_user(
    cf_jwt: str | None = Header(default=None, alias="Cf-Access-Jwt-Assertion"),
    dev_override: str | None = Cookie(default=None),
) -> None:
    """Authenticate an image request without pinning a pooled DB connection.

    Deliberately does NOT use `Depends(get_db)`. FastAPI tears yield-dependencies
    down on the request exit stack, which unwinds *after* the response body has
    been sent (`fastapi/routing.py` — `await response(...)` runs inside the stack
    that owns them). For a FileResponse that means the connection would stay
    checked out for the entire JPEG transfer through the tunnel. A short-lived
    session returns it immediately instead.
    """
    with SessionLocal() as db:
        _load_user(db, _identify(cf_jwt, dev_override), touch=False)


def require_role(*roles: str):
    def _dep(user: m.User = Depends(current_user)) -> m.User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail=f"Requires role: {', '.join(roles)}")
        return user
    return _dep


require_admin = require_role("admin")
require_contributor = require_role("admin", "contributor")
