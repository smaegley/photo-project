"""Auth — Cloudflare Access (SPEC §6.3).

In production the app sits behind Cloudflare Access (Zero Trust); every request
carries a signed `Cf-Access-Jwt-Assertion` header. We verify it against the
team's public keys, read the verified email, and map it to a user + role.

For local dev (no Access in front) the verification is bypassed and requests act
as a configurable dev admin — controlled purely by whether CF Access settings
are present, so production is never accidentally open.
"""
from datetime import datetime, timezone

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app import models as m

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
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail=f"Invalid Access token: {exc}")
    email = claims.get("email")
    if not email:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="No email in token")
    return email


def current_user(
    db: Session = Depends(get_db),
    cf_jwt: str | None = Header(default=None, alias="Cf-Access-Jwt-Assertion"),
) -> m.User:
    """Resolve the request to a User row (creating viewers on first sight)."""
    dev = not settings.cf_access_enabled
    if dev:
        email = settings.dev_user_email
        role = settings.dev_user_role
    else:
        if not cf_jwt:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                detail="Missing Cloudflare Access assertion")
        email = _verify_access_jwt(cf_jwt)
        role = None  # role comes from our DB / default below

    user = db.query(m.User).filter(m.User.email == email).first()
    if user is None:
        user = m.User(email=email, role=role or "viewer",
                      person_id=settings.dev_user_person_id if dev else None,
                      invited_at=datetime.now(timezone.utc))
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if role and user.role != role:  # dev override keeps the dev user as admin
            user.role = role
        if dev and not user.person_id:  # backfill the dev user's tree link
            user.person_id = settings.dev_user_person_id
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    return user


def require_role(*roles: str):
    def _dep(user: m.User = Depends(current_user)) -> m.User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail=f"Requires role: {', '.join(roles)}")
        return user
    return _dep


require_admin = require_role("admin")
require_contributor = require_role("admin", "contributor")
