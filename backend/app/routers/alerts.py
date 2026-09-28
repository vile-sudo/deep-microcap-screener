"""
Alerts: new highs / lows over 1D, 1W, 1M, 6M, 1Y, IPO-base breakouts and base
breakouts, from chart_data/alerts.json (written daily by scripts/update_charts.py,
rules in app/alerts.py) -- merged with institutional deal clusters (unusually
one-sided bulk/block deal activity in a stock) from data/deals/clusters.json
(written daily by scripts/run_deal_clusters.py, rules in app/deal_clusters.py)
and insider clusters (several different insiders buying/selling the same
stock within a week) from data/insider_trades/clusters.json (written by
scripts/run_insider_clusters.py, rules in app/insider_clusters.py).

GET /api/alerts?highs=1W,1M,6M,1Y&lows=1M,6M,1Y&ipo=1&vcp=1&deals=1&insiders=1&scope=board&sessions=5&seen=2026-09-10

  scope     watchlist (the logged-in user's stars), board (every board company) or
            market (board plus the liquid NSE universe: 6M / 1Y highs and lows, IPO-base breakouts)
  deals     institutional deal clusters (deal_buy / deal_sell) -- always drawn from the whole NSE
            market regardless of `scope` above (most flagged names are not board companies at all;
            gating them behind the "market" scope would hide them by default, and they are a small,
            already-filtered list, not the flood scope=market's NSE-wide highs/lows is)
  insiders  insider clusters (insider_buy / insider_sell) -- same "not gated by scope" reasoning as deals
  seen      the last session the user has looked at; "new" counts what came after it
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from .. import auth
from ..charts import CHART_DIR
from ..config import BASE_DIR
from ..database import get_db
from ..models import WatchItem

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
ALERTS_FILE = CHART_DIR / "alerts.json"
CLUSTERS_FILE = BASE_DIR / "data" / "deals" / "clusters.json"
INSIDER_CLUSTERS_FILE = BASE_DIR / "data" / "insider_trades" / "clusters.json"
WINDOWS = ["1D", "1W", "1M", "6M", "1Y"]
DEAL_TYPES = ("deal_buy", "deal_sell")
INSIDER_TYPES = ("insider_buy", "insider_sell")


@lru_cache(maxsize=4)
def _read(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_or(path: Path, default: dict) -> dict:
    try:
        return _read(str(path), path.stat().st_mtime)
    except (OSError, ValueError):
        return default


def load() -> dict:
    """chart_data/alerts.json's sessions, with data/deals/clusters.json's and data/insider_trades/
    clusters.json's sessions merged in by date -- three files written by three independent daily steps,
    none of them ever overwriting another's items."""
    doc = _read_or(ALERTS_FILE, {"latest": None, "sessions": []})
    clusters = _read_or(CLUSTERS_FILE, {"sessions": []})
    insider_clusters = _read_or(INSIDER_CLUSTERS_FILE, {"sessions": []})
    by_date: dict[str, list[dict]] = {s["date"]: list(s["items"]) for s in doc.get("sessions", [])}
    for s in clusters.get("sessions", []):
        by_date.setdefault(s["date"], []).extend(s["items"])
    for s in insider_clusters.get("sessions", []):
        by_date.setdefault(s["date"], []).extend(s["items"])
    dates = sorted(by_date)
    latest = max(doc.get("latest") or "", dates[-1] if dates else "") or None
    return {"latest": latest, "market_windows": doc.get("market_windows", ["6M", "1Y"]),
            "sessions": [{"date": d, "items": by_date[d]} for d in dates]}


def _set(value: str | None) -> set[str]:
    return {w for w in (value or "").split(",") if w in WINDOWS}


@router.get("")
def get_alerts(request: Request, db: Session = Depends(get_db),
               highs: str = Query("1W,1M,6M,1Y"), lows: str = Query("1W,1M,6M,1Y"),
               ipo: bool = True, vcp: bool = True, deals: bool = True, insiders: bool = True,
               scope: str = Query("board", pattern="^(watchlist|board|market)$"),
               sessions: int = Query(5, ge=1, le=15), seen: str | None = None, codes: str | None = None):
    doc = load()
    want_hi, want_lo = _set(highs), _set(lows)
    watch: set[str] | None = None
    if scope == "watchlist":
        user = getattr(request.state, "user", None) or (auth.user_for_token(request.cookies.get(auth.COOKIE)) if auth.accounts_enabled() else None)
        if user:
            watch = {w.code for w in db.query(WatchItem).filter(WatchItem.user_id == user["id"])}
        else:   # accounts off: the browser sends its own watchlist
            watch = {c for c in (codes or "").split(",") if c}

    def keep(it: dict) -> bool:
        t = it["type"]
        if t in DEAL_TYPES or t in INSIDER_TYPES:
            # not gated by `scope` (see the module docstring): only by the watchlist filter, when that's
            # the active scope, and by whether this cluster kind is wanted at all
            want = deals if t in DEAL_TYPES else insiders
            if watch is not None:
                return want and it.get("code") in watch
            return want
        if it["scope"] == "market" and scope != "market":
            return False
        if watch is not None and it.get("code") not in watch:
            return False
        if t == "high":
            return bool(want_hi.intersection(it.get("windows") or []))
        if t == "low":
            return bool(want_lo.intersection(it.get("windows") or []))
        if t == "ipo_breakout":
            return ipo
        if t == "vcp_breakout":
            return vcp
        return False

    out, new = [], 0
    for s in doc.get("sessions", []):
        items = [it for it in s["items"] if keep(it)]
        if seen is None or s["date"] > seen:
            new += len(items)
        out.append({"date": s["date"], "items": items})
    out = out[-sessions:][::-1]
    return {"latest": doc.get("latest"), "windows": WINDOWS, "market_windows": doc.get("market_windows", ["6M", "1Y"]),
            "sessions": out, "new": new if seen is not None else (len(out[0]["items"]) if out else 0)}
