"""
Accounts: password hashing, login sessions, and the gate in front of the app.

How it fits together
--------------------
* Passwords are stored as salted scrypt hashes (hash_password / verify_password);
  the plain password never touches the database or the logs.
* Logging in creates a random 256-bit token. The browser keeps it in an
  HttpOnly, SameSite=Lax cookie (Secure on HTTPS); the database keeps only its
  SHA-256, in auth_sessions, with an expiry. Every gunicorn worker and every
  redeploy (on a persistent database) sees the same sessions.
* AccountGateMiddleware lets through the login page, the auth API, static
  assets and the health check; everything else needs an approved user. API
  calls without one get 401 JSON, page loads are redirected to /login.
* The admin account is created at startup from the settings (see
  config.admin_login) and its password kept in sync with them, so the owner
  can always get in -- and rotate the password by changing an env var.
* With no admin configured at all (local development) the gate is off.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from .config import get_settings
from .database import SessionLocal
from .models import AuthSession, User

log = logging.getLogger("deepsweep.auth")

COOKIE = "ds_session"
PUBLIC_PREFIXES = ("/static/", "/api/auth/")
PUBLIC_PATHS = {"/login", "/healthz", "/favicon.ico", "/api/meta/logic-gates", "/api/cron/news-channel"}
# /api/meta/logic-gates (GET) is public because it's already documented on the
# board's own "How this board is built" page, and the daily auto-screen reads
# it over plain HTTP from GitHub Actions, which has no session cookie.
# /api/cron/news-channel (POST) is public the same way: an external cron
# service calls it with no session cookie either. It's safe to leave
# unauthenticated at this layer because the route itself gates on the
# CRON_KEY shared secret (see routers/news_channel.py) -- same model as
# Market view's kite_admin_key, just reachable without a login at all.


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- passwords
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(base64.b64decode(dk)))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- sessions
def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db, user: User) -> str:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(AuthSession(token_hash=_digest(token), user_id=user.id, created_at=now,
                       expires_at=now + timedelta(days=get_settings().session_days)))
    user.last_login = now
    # tidy up anything expired while we're here
    db.execute(delete(AuthSession).where(AuthSession.expires_at < now))
    db.commit()
    return token


def end_session(db, token: str | None) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _digest(token)))
        db.commit()
        _cache_drop(token)


def end_all_sessions(db, user_id: int) -> None:
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
    db.commit()
    with _cache_lock:
        _cache.clear()


# a short per-worker cache so every API call doesn't hit the database
_cache: dict[str, tuple[float, dict | None]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 10


def _cache_drop(token: str) -> None:
    with _cache_lock:
        _cache.pop(_digest(token), None)


def _cache_clear() -> None:
    with _cache_lock:
        _cache.clear()


def user_for_token(token: str | None) -> dict | None:
    """The approved user behind a session cookie, as a plain dict, or None."""
    if not token:
        return None
    key = _digest(token)
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    with SessionLocal() as db:
        row = db.execute(
            select(User).join(AuthSession, AuthSession.user_id == User.id)
            .where(AuthSession.token_hash == key, AuthSession.expires_at > utcnow())
        ).scalar_one_or_none()
        user = public_user(row) if row is not None and row.status == "approved" else None
    with _cache_lock:
        _cache[key] = (now, user)
    return user


def public_user(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "status": u.status, "is_admin": u.is_admin,
            "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
            "approved_at": u.approved_at.isoformat() + "Z" if u.approved_at else None,
            "last_login": u.last_login.isoformat() + "Z" if u.last_login else None}


def set_cookie(response, request: Request, token: str) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
    response.set_cookie(COOKIE, token, max_age=get_settings().session_days * 86400, httponly=True,
                        secure=secure, samesite="lax", path="/")


def clear_cookie(response) -> None:
    response.delete_cookie(COOKIE, path="/")


# ---------------------------------------------------------------- admin bootstrap
def accounts_enabled() -> bool:
    return get_settings().admin_login is not None


def ensure_admin() -> None:
    """Create the configured admin, or bring its password and rights in line.

    Every gunicorn worker runs this at startup at the same moment, so two can
    race to insert the same admin; the loser just retries and finds the row.
    It never raises: a problem here must not stop the site from starting."""
    login = get_settings().admin_login
    if not login:
        return
    ident, password = login
    for attempt in range(5):
        try:
            with SessionLocal() as db:
                user = db.execute(select(User).where(User.email == ident)).scalar_one_or_none()
                now = utcnow()
                if user is None:
                    db.add(User(email=ident, name="Admin", password_hash=hash_password(password), status="approved",
                                is_admin=True, created_at=now, approved_at=now))
                else:
                    if not verify_password(password, user.password_hash):
                        user.password_hash = hash_password(password)
                    if not user.is_admin or user.status != "approved":
                        user.is_admin, user.status = True, "approved"
                    user.approved_at = user.approved_at or now
                db.commit()
            return
        except SQLAlchemyError as e:
            if attempt == 4:
                log.warning("could not set up the admin account: %s", e)
            time.sleep(0.2 * (attempt + 1) + secrets.randbelow(100) / 1000)


# ---------------------------------------------------------------- rate limits
_hits: dict[str, deque] = {}
_hits_lock = threading.Lock()


def rate_limited(bucket: str, limit: int, window_s: int) -> bool:
    """True when this bucket (e.g. "login:1.2.3.4") has used its allowance."""
    now = time.time()
    with _hits_lock:
        q = _hits.setdefault(bucket, deque())
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        return False


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


# ---------------------------------------------------------------- the gate
class AccountGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not accounts_enabled() or path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)
        user = user_for_token(request.cookies.get(COOKIE))
        if user is None:
            if path.startswith("/api/") or path in ("/docs", "/redoc", "/openapi.json"):
                return JSONResponse({"detail": "Login required"}, status_code=401)
            nxt = path + (("?" + request.url.query) if request.url.query else "")
            return RedirectResponse(f"/login?next={quote(nxt)}", status_code=303)
        request.state.user = user
        return await call_next(request)
