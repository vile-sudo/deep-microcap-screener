"""Sector Strength, live: today's move for every stock on the page, from Zerodha Kite Connect.

The nightly snapshot (app/sector_strength.py) covers sessions that have closed. While Zerodha is connected
(app/kite.py's daily login) this adds "today": each member's last traded price against the previous close,
and the Nifty 500 index the same way. The page aggregates the moves itself -- share up, median, against the
Nifty 500 -- so its Liquid only / Hide SME switches apply to the live column too.

Kite's quote API takes up to 1000 instruments a call at about one call a second, so the ~6,000 stocks are
fetched in chunks every LIVE_TTL seconds at most. Gunicorn runs several workers: the result is kept in a
file in the temp directory and one worker at a time refreshes it (a lock file), the others serve the last
copy, so Zerodha sees one set of requests however many browsers are open.
"""
from __future__ import annotations

import json
import os
import statistics
import tempfile
import time
from datetime import datetime
from pathlib import Path

import requests

from . import kite
from .sector_strength import LATEST_FILE

LIVE_TTL = 60                 # seconds between refreshes while the market is open
CLOSED_TTL = 900              # after the close the numbers don't change; check now and then
CHUNK = 400                   # instruments per call: keeps the request address a sane length
INDEX = "NSE:NIFTY 500"
CACHE = Path(tempfile.gettempdir()) / "deepsweep-sector-live.json"
LOCK = Path(tempfile.gettempdir()) / "deepsweep-sector-live.lock"


def _members() -> tuple[list[tuple[str, str]], list[str], str | None, dict]:
    """Every (exchange, exchange symbol) on the page, the Nifty 500 list, the snapshot's date, and each
    sector's members."""
    try:
        d = json.loads(LATEST_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], [], None, {}
    groups = {sec["slug"]: [f"{s['exchange']}:{s['symbol']}" for s in sec.get("stocks") or [] if s.get("exchange") and s.get("symbol")]
              for sec in d.get("sectors") or []}
    keys = {(s["exchange"], s["symbol"]) for sec in d.get("sectors") or [] for s in sec.get("stocks") or []
            if s.get("exchange") and s.get("symbol")}
    try:
        from .sector_strength import MEMBERS_FILE
        n500 = json.loads(MEMBERS_FILE.read_text(encoding="utf-8")).get("nifty500") or []
    except (OSError, ValueError):
        n500 = []
    return sorted(keys), n500, d.get("asof"), groups


def _today_is_new(asof: str | None) -> bool:
    """A live column is worth showing on a weekday from the open, until the nightly run has that day."""
    now = datetime.now(kite.IST)
    if now.weekday() >= 5 or now.hour * 60 + now.minute < 9 * 60 + 15:
        return False
    return not asof or asof < now.date().isoformat()


def build() -> dict:
    now = datetime.now(kite.IST)
    keys, n500, asof, groups = _members()
    base = {"as_of": now.isoformat(timespec="seconds"), "market_open": kite.market_open(), "snapshot_date": asof}
    if not kite.configured():
        return base | {"status": "not_configured"}
    if not kite.session():
        return base | {"status": "not_connected"}
    if not _today_is_new(asof):
        return base | {"status": "no_session_today"}
    tsyms = kite.tradingsymbols()
    want = {}
    for ex, sym in keys:
        ts = tsyms.get((ex, sym))
        if ts:
            want[f"{ex}:{ts}"] = f"{ex}:{sym}"
    inst = sorted(want) + [INDEX]
    quotes = {}
    for i in range(0, len(inst), CHUNK):
        # the full quote, for today's volume and average price as well (the money-flow column)
        quotes.update(kite._get("/quote", params=[("i", x) for x in inst[i:i + CHUNK]]).json().get("data") or {})
        time.sleep(1.05)                    # Kite's quote limit is about one call a second
    moves, value = {}, {}
    for k, ours in want.items():
        q = quotes.get(k) or {}
        last, prev = q.get("last_price"), (q.get("ohlc") or {}).get("close")
        if last and prev and q.get("ohlc", {}).get("open"):      # no open: hasn't traded today
            moves[ours] = round((last / prev - 1) * 100, 2)
            vol, avg = q.get("volume") or 0, q.get("average_price") or last
            value[ours] = vol * avg / 1e7                         # Rs crore traded so far today
    sector_value = {slug: round(sum(value.get(k, 0) for k in ks), 2) for slug, ks in groups.items()}
    idx = quotes.get(INDEX) or {}
    il, ip = idx.get("last_price"), (idx.get("ohlc") or {}).get("close")
    vals = [moves[f"NSE:{s}"] for s in n500 if f"NSE:{s}" in moves]
    breadth = {"n": len(vals), "up": sum(v > 0 for v in vals), "down": sum(v < 0 for v in vals),
               "up1": sum(v > 1 for v in vals), "median": round(statistics.median(vals), 2) if vals else None}
    return base | {"status": "live" if base["market_open"] else "closed", "date": now.date().isoformat(),
                   "benchmark": {"name": "Nifty 500", "last": il, "chg": round((il / ip - 1) * 100, 2) if il and ip else None,
                                 "breadth": breadth, "value_cr": round(sum(value.get(f"NSE:{s}", 0) for s in n500), 2)},
                   "sector_value": sector_value,
                   "covered": len(moves), "requested": len(want), "moves": moves,
                   "values": {k: round(v, 2) for k, v in value.items()}}


def snapshot() -> dict:
    """The newest copy, refreshed when it's older than its TTL and no other worker is already on it."""
    try:
        cur = json.loads(CACHE.read_text(encoding="utf-8"))
        age = time.time() - CACHE.stat().st_mtime
    except (OSError, ValueError):
        cur, age = None, 1e9
    ttl = LIVE_TTL if kite.market_open() else CLOSED_TTL
    if cur and age < ttl and cur.get("status") in ("live", "closed") and kite.session():
        return cur
    if cur and age < 20:
        return cur                                   # status answers (not connected...) are cheap but not free
    try:
        if time.time() - LOCK.stat().st_mtime > 180:
            LOCK.unlink()
    except OSError:
        pass
    try:
        os.close(os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return cur or {"status": "loading"}
    try:
        try:
            data = build()
        except kite.KiteAuthError:
            data = {"status": "not_connected", "as_of": datetime.now(kite.IST).isoformat(timespec="seconds")}
        except requests.RequestException as e:
            return cur or {"status": "error", "error": str(e)[:120]}
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(CACHE)
        return data
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass
