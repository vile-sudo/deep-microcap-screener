"""
Alerts: new highs / lows over 1D, 1W, 1M, 6M, 1Y, IPO-base breakouts and base
breakouts, from chart_data/alerts.json (written daily by scripts/update_charts.py,
rules in app/alerts.py).

GET /api/alerts?highs=1W,1M,6M,1Y&lows=1M,6M,1Y&ipo=1&vcp=1&scope=board&sessions=5&seen=2026-09-10

  scope     watchlist (the logged-in user's stars), board (every board company) or
            market (board plus the liquid NSE universe: 6M / 1Y highs and lows, IPO-base breakouts)
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
from ..database import get_db
from ..models import WatchItem

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
ALERTS_FILE = CHART_DIR / "alerts.json"
WINDOWS = ["1D", "1W", "1M", "6M", "1Y"]


@lru_cache(maxsize=4)
def _read(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load() -> dict:
    try:
        return _read(str(ALERTS_FILE), ALERTS_FILE.stat().st_mtime)
    except (OSError, ValueError):
        return {"latest": None, "sessions": []}


def _set(value: str | None) -> set[str]:
    return {w for w in (value or "").split(",") if w in WINDOWS}


@router.get("")
def get_alerts(request: Request, db: Session = Depends(get_db),
               highs: str = Query("1W,1M,6M,1Y"), lows: str = Query("1W,1M,6M,1Y"),
               ipo: bool = True, vcp: bool = True, scope: str = Query("board", pattern="^(watchlist|board|market)$"),
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
        if it["scope"] == "market" and scope != "market":
            return False
        if watch is not None and it.get("code") not in watch:
            return False
        t = it["type"]
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
