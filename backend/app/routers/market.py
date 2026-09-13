"""
Market view endpoints.

GET  /api/market/indices    NIFTY 50 and SENSEX. Live from Zerodha when a Kite
                            session is active; otherwise a delayed quote
                            (Yahoo Finance), labelled as such.
GET  /api/market/trending   board companies breaking their 1-day, 1-week,
                            1-month or 52-week high or low today. Live (Kite
                            quotes against the stored daily history) while
                            the market is open and Zerodha is connected;
                            otherwise from the latest end-of-day session.
GET  /api/kite/status       is Zerodha configured / connected
GET  /api/kite/login?key=   start the daily Zerodha login (needs KITE_ADMIN_KEY)
GET  /api/kite/callback     where Zerodha sends the login back
POST /api/kite/logout?key=  drop the session
"""
from __future__ import annotations

import hmac
import json
import html
import threading
import time
from datetime import datetime

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import charts, kite
from ..config import get_settings
from ..database import get_db
from ..models import Company

router = APIRouter(tags=["market"])

_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, ttl: float, build):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = build()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


# ---------------------------------------------------------------- indices
def _pct(last, prev):
    return round((last / prev - 1) * 100, 2) if last is not None and prev else None


def _indices_kite() -> list[dict] | None:
    try:
        data = kite.ohlc([i["kite"] for i in kite.INDICES])
    except (kite.KiteAuthError, requests.RequestException):
        return None
    out = []
    for i in kite.INDICES:
        q = data.get(i["kite"])
        if not q:
            return None
        o = q.get("ohlc") or {}
        last, prev = q.get("last_price"), o.get("close")
        out.append({"key": i["key"], "name": i["name"], "last": last, "prev_close": prev,
                    "change": round(last - prev, 2) if last is not None and prev else None,
                    "change_pct": _pct(last, prev), "open": o.get("open"), "high": o.get("high"), "low": o.get("low")})
    return out


def _indices_delayed() -> list[dict]:
    out = []
    for i in kite.INDICES:
        item = {"key": i["key"], "name": i["name"], "last": None, "prev_close": None, "change": None,
                "change_pct": None, "open": None, "high": None, "low": None}
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{i['yahoo']}?range=1d&interval=5m",
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            res = r.json()["chart"]["result"][0]
            m = res["meta"]
            q = (res.get("indicators") or {}).get("quote", [{}])[0]
            highs = [x for x in (q.get("high") or []) if x is not None]
            lows = [x for x in (q.get("low") or []) if x is not None]
            opens = [x for x in (q.get("open") or []) if x is not None]
            last, prev = m.get("regularMarketPrice"), m.get("chartPreviousClose") or m.get("previousClose")
            item.update(last=last, prev_close=prev, change=round(last - prev, 2) if last and prev else None,
                        change_pct=_pct(last, prev), open=opens[0] if opens else None,
                        high=m.get("regularMarketDayHigh") or (max(highs) if highs else None),
                        low=m.get("regularMarketDayLow") or (min(lows) if lows else None))
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
            pass
        out.append(item)
    return out


@router.get("/api/market/indices")
def indices():
    def build():
        live = _indices_kite() if kite.session() else None
        return {
            "source": "zerodha" if live else "delayed",
            "market_open": kite.market_open(),
            "as_of": datetime.now(kite.IST).isoformat(timespec="seconds"),
            "items": live or _indices_delayed(),
        }
    # live ticks are worth a 2 s cache; a delayed quote isn't worth hammering
    ttl = 2 if kite.session() else 30
    return _cached(f"indices:{bool(kite.session())}", ttl, build)


# --------------------------------------------------------------- trending
WINDOWS = [("1d", 1, 1), ("1w", 5, 5), ("1m", 21, 21), ("52w", 250, 200)]  # (key, sessions, minimum history)


def _breaks(prior: list[list], high: float, low: float) -> tuple[dict, dict]:
    """Which prior highs/lows today's range took out. prior rows: [date,o,h,l,c,v]."""
    highs, lows = {}, {}
    for key, n, need in WINDOWS:
        if len(prior) < need:
            continue
        window = prior[-n:]
        top = max(r[2] for r in window)
        bottom = min(r[3] for r in window)
        if high > top:
            highs[key] = top
        if low < bottom:
            lows[key] = bottom
    return highs, lows


_SERIES: dict = {"stamp": None, "data": {}}


def _series(code: str, stamp) -> dict | None:
    if _SERIES["stamp"] != stamp:
        _SERIES.update(stamp=stamp, data={})
    if code not in _SERIES["data"]:
        _SERIES["data"][code] = charts.load_series(code)
    return _SERIES["data"][code]


def _trending(db: Session, live: bool) -> dict:
    names = {code: (data or {}).get("name") for code, data in db.query(Company.code, Company.data)}
    index = charts.load_index()
    idx, stamp, latest = index.get("companies", {}), index.get("generated_at"), index.get("latest_session")
    today = datetime.now(kite.IST).date().isoformat()

    quotes, keymap = {}, {}
    if live:
        try:
            tsyms = kite.tradingsymbols()
            for code, st in idx.items():
                ts = tsyms.get((st["exchange"], st["symbol"]))
                if ts and code in names:
                    keymap[code] = f"{st['exchange']}:{ts}"
            quotes = kite.ohlc(sorted(set(keymap.values())))
        except (kite.KiteAuthError, requests.RequestException):
            live, quotes = False, {}

    groups = {f"{side}_{k}": [] for side in ("high", "low") for k, _, _ in WINDOWS}
    as_of = None
    for code, st in idx.items():
        if code not in names:
            continue   # has chart data but has left the board
        series = _series(code, stamp)
        if not series or len(series["rows"]) < 2:
            continue
        rows = series["rows"]
        q = quotes.get(keymap.get(code, "")) if live else None
        if q and (q.get("ohlc") or {}).get("high"):
            o = q["ohlc"]
            prior = rows[:-1] if rows[-1][0] == today else rows
            last, high, low, prev = q["last_price"], o["high"], o["low"], o.get("close") or prior[-1][4]
            day = today
        elif live or rows[-1][0] != latest:
            continue   # no live quote, or didn't trade in the latest session: nothing to say about "today"
        else:
            prior, cur = rows[:-1], rows[-1]
            last, high, low, prev, day = cur[4], cur[2], cur[3], prior[-1][4], cur[0]
        as_of = max(as_of or day, day)
        highs, lows = _breaks(prior, high, low)
        base = {"code": code, "name": names[code], "symbol": st["symbol"], "exchange": st["exchange"],
                "last": last, "high": high, "low": low, "chg_pct": _pct(last, prev), "day": day}
        for k, level in highs.items():
            groups[f"high_{k}"].append({**base, "level": level, "beyond_pct": _pct(high, level)})
        for k, level in lows.items():
            groups[f"low_{k}"].append({**base, "level": level, "beyond_pct": _pct(low, level)})

    for key, items in groups.items():
        # strongest move first: highs by day's gain, lows by day's fall
        items.sort(key=lambda x: (x["chg_pct"] is None, -(x["chg_pct"] or 0) if key.startswith("high") else (x["chg_pct"] or 0)))
    return {
        "mode": "live" if live and quotes else "eod",
        "as_of": as_of,
        "generated_at": datetime.now(kite.IST).isoformat(timespec="seconds"),
        "counts": {k: len(v) for k, v in groups.items()},
        "groups": groups,
    }


@router.get("/api/market/trending")
def trending(db: Session = Depends(get_db)):
    live = bool(kite.session()) and kite.market_open()
    ttl = 30 if live else 600
    stamp = charts.load_index().get("generated_at")
    return _cached(f"trending:{live}:{stamp}", ttl, lambda: _trending(db, live))


# ----------------------------------------------------------------- setups
@router.get("/api/market/setups")
def market_setups():
    """Stages, measures and the what-changed feed from the daily scan (app/setups.py)."""
    path = charts.CHART_DIR / "setups.json"
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {"as_of": None, "counts": {}, "market_breakouts": [], "feed": [], "stocks": {}}
    return _cached(f"setups:{stamp}", 3600, lambda: json.loads(path.read_text(encoding="utf-8")))


# ------------------------------------------------------------------- kite
def _check_admin(key: str, request: Request | None = None) -> None:
    # a logged-in dashboard admin needs no separate key
    user = getattr(request.state, "user", None) if request is not None else None
    if user and user.get("is_admin"):
        return
    admin = get_settings().kite_admin_key
    if not admin or not hmac.compare_digest(key or "", admin):
        raise HTTPException(status_code=403, detail="Wrong or missing admin key")


@router.get("/api/kite/status")
def kite_status():
    return kite.status()


@router.get("/api/kite/login")
def kite_login(request: Request, key: str = Query("")):
    if not kite.configured():
        raise HTTPException(status_code=503, detail="Zerodha is not configured: set KITE_API_KEY and KITE_API_SECRET")
    _check_admin(key, request)
    return RedirectResponse(kite.login_url(), status_code=302)


@router.get("/api/kite/callback")
def kite_callback(request_token: str = "", status: str = "", state: str = ""):
    if status != "success" or not request_token:
        return _page("Zerodha login was cancelled.", ok=False)
    try:
        kite.complete_login(request_token, state)
    except PermissionError as e:
        return _page(str(e), ok=False)
    except (RuntimeError, requests.RequestException) as e:
        return _page(f"Zerodha rejected the login: {e}", ok=False)
    with _cache_lock:
        _cache.clear()
    return RedirectResponse("/#v=market", status_code=302)


@router.post("/api/kite/logout")
def kite_logout(request: Request, key: str = Query("")):
    _check_admin(key, request)
    kite.logout()
    with _cache_lock:
        _cache.clear()
    return {"connected": False}


def _page(message: str, ok: bool) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>Zerodha</title>"
        f"<body style='background:#07141d;color:#edf5ff;font:15px system-ui;display:grid;place-items:center;height:100vh;margin:0'>"
        f"<div style='max-width:460px;text-align:center'><p>{'✓' if ok else '⚠'} {html.escape(message)}</p>"
        f"<p><a style='color:#62d6de' href='/#v=market'>Back to Market view</a></p></div>",
        status_code=200 if ok else 400)
