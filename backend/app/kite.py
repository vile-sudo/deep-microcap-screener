"""
Zerodha Kite Connect: the session, and live quotes for Market view.

How the session works
---------------------
Kite Connect has no long-lived token. Once a day the account owner logs in
at kite.zerodha.com; Zerodha redirects back to /api/kite/callback with a
request_token, which is exchanged (with the API secret) for an access_token
that is valid until about 6 a.m. the next day. That token is kept in the
database (MetaKV "KITE_SESSION") so every gunicorn worker shares it, and it
is never sent to the browser.

Starting a login needs KITE_ADMIN_KEY, and the callback only accepts the
one-time `state` issued by that login -- so a visitor can neither start a
login nor swap in a session of their own.

When there is no valid session, callers get None and fall back (delayed
index quotes, end-of-day breakouts) rather than an error.
"""
from __future__ import annotations

import csv
import hashlib
import io
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from .config import get_settings
from .database import SessionLocal
from .models import MetaKV

API = "https://api.kite.trade"
LOGIN = "https://kite.zerodha.com/connect/login"
IST = timezone(timedelta(hours=5, minutes=30))

INDICES = [
    {"key": "NIFTY50", "name": "NIFTY 50", "kite": "NSE:NIFTY 50", "yahoo": "^NSEI"},
    {"key": "SENSEX", "name": "SENSEX", "kite": "BSE:SENSEX", "yahoo": "^BSESN"},
]

_lock = threading.Lock()


class KiteAuthError(Exception):
    """The stored session was rejected (expired or revoked)."""


def configured() -> bool:
    s = get_settings()
    return bool(s.kite_api_key and s.kite_api_secret)


# ---------------------------------------------------------------- storage
def _get_kv(key: str):
    with SessionLocal() as db:
        row = db.get(MetaKV, key)
        return row.value if row else None


def _set_kv(key: str, value) -> None:
    with SessionLocal() as db:
        row = db.get(MetaKV, key)
        if row is None:
            db.add(MetaKV(key=key, value=value))
        else:
            row.value = value
        db.commit()


def _session_expiry(login: datetime) -> datetime:
    """Kite tokens lapse at 6 a.m. IST the day after login."""
    login = login.astimezone(IST)
    six = login.replace(hour=6, minute=0, second=0, microsecond=0)
    return six if login < six else six + timedelta(days=1)


def session() -> dict | None:
    sess = _get_kv("KITE_SESSION")
    if not sess or not sess.get("access_token"):
        return None
    try:
        login = datetime.fromisoformat(sess["login_time"])
    except (KeyError, ValueError):
        return None
    if datetime.now(IST) >= _session_expiry(login):
        return None
    return sess


def status() -> dict:
    sess = session()
    return {
        "configured": configured(),
        "connected": bool(sess),
        "user": (sess.get("user_name") or sess.get("user_id")) if sess else None,
        "login_time": sess.get("login_time") if sess else None,
        "expires": _session_expiry(datetime.fromisoformat(sess["login_time"])).isoformat() if sess else None,
    }


# ------------------------------------------------------------------ login
def login_url() -> str:
    state = secrets.token_urlsafe(16)
    _set_kv("KITE_LOGIN_STATE", {"state": state, "issued": time.time()})
    api_key = get_settings().kite_api_key
    # redirect_params come back untouched on the callback
    return f"{LOGIN}?v=3&api_key={api_key}&redirect_params=state%3D{state}"


def complete_login(request_token: str, state: str) -> dict:
    pending = _get_kv("KITE_LOGIN_STATE") or {}
    if not state or state != pending.get("state") or time.time() - pending.get("issued", 0) > 900:
        raise PermissionError("This login link has expired or was not started here. Start again from Market view.")
    s = get_settings()
    checksum = hashlib.sha256((s.kite_api_key + request_token + s.kite_api_secret).encode()).hexdigest()
    r = requests.post(f"{API}/session/token", headers={"X-Kite-Version": "3"},
                      data={"api_key": s.kite_api_key, "request_token": request_token, "checksum": checksum},
                      timeout=15)
    body = r.json()
    if r.status_code != 200 or body.get("status") != "success":
        raise RuntimeError(body.get("message") or f"Kite returned HTTP {r.status_code}")
    d = body["data"]
    sess = {
        "access_token": d["access_token"],
        "user_id": d.get("user_id"),
        "user_name": d.get("user_name"),
        "login_time": datetime.now(IST).isoformat(timespec="seconds"),
    }
    _set_kv("KITE_SESSION", sess)
    _set_kv("KITE_LOGIN_STATE", {})
    return sess


def logout() -> None:
    _set_kv("KITE_SESSION", {})


# ----------------------------------------------------------------- quotes
def _headers() -> dict | None:
    sess = session()
    if not sess:
        return None
    return {"X-Kite-Version": "3", "Authorization": f"token {get_settings().kite_api_key}:{sess['access_token']}"}


def _get(path: str, params=None) -> requests.Response:
    h = _headers()
    if h is None:
        raise KiteAuthError("not connected")
    r = requests.get(f"{API}{path}", headers=h, params=params, timeout=15)
    if r.status_code == 403:
        logout()   # TokenException: the token was revoked or has lapsed
        raise KiteAuthError("session expired")
    r.raise_for_status()
    return r


def ohlc(instruments: list[str]) -> dict:
    """{"NSE:INFY": {"last_price", "ohlc": {open, high, low, close}}, ...}

    close is the previous session's close. Kite takes up to 1000
    instruments per call; batches of 400 keep the URL a sane length."""
    out = {}
    for i in range(0, len(instruments), 400):
        chunk = instruments[i:i + 400]
        out.update(_get("/quote/ohlc", params=[("i", x) for x in chunk]).json().get("data") or {})
    return out


_INSTRUMENTS: dict = {"day": None, "map": {}}


def tradingsymbols() -> dict[tuple[str, str], str]:
    """(exchange, exchange symbol) -> Kite tradingsymbol, for equities.

    Kite's symbols usually equal the exchange's, but SME and trade-for-trade
    listings carry a series suffix on NSE (e.g. QLINE-SM, APSISAERO-ST), so
    the instrument dump is read once a day to map one onto the other."""
    today = datetime.now(IST).date().isoformat()
    with _lock:
        if _INSTRUMENTS["day"] == today and _INSTRUMENTS["map"]:
            return _INSTRUMENTS["map"]
    mapping = {}
    for exch in ("NSE", "BSE"):
        text = _get(f"/instruments/{exch}").text
        for row in csv.DictReader(io.StringIO(text)):
            if row.get("instrument_type") != "EQ" or row.get("segment") != exch:
                continue
            ts = row["tradingsymbol"]
            base = ts.rsplit("-", 1)[0] if "-" in ts and ts.rsplit("-", 1)[1] in {"SM", "ST", "BE", "BZ", "SZ", "IL", "RR", "E1"} else ts
            # the plain symbol wins over a suffixed duplicate
            if (exch, base) not in mapping or ts == base:
                mapping[(exch, base)] = ts
    with _lock:
        _INSTRUMENTS.update(day=today, map=mapping)
    return mapping


# ----------------------------------------------------------- market hours
def market_open(now: datetime | None = None) -> bool:
    """NSE/BSE cash session, 09:15-15:30 IST on weekdays (holidays not known)."""
    now = (now or datetime.now(IST)).astimezone(IST)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= mins <= 15 * 60 + 30
