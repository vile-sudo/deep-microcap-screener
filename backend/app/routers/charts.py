"""
Chart gallery endpoints.

GET /api/charts           chart stats for every company that has a chart,
                          keyed by board code (sorting/filtering the gallery
                          needs all of them up front; the candles are not
                          included, so this stays small)
GET /api/charts/universe  every actively traded NSE/BSE company, one small
                          line each -- what Screen any Chart searches
GET /api/charts/u/{key}   one non-board stock's candles and base analysis
                          (see app/universe.py)
GET /api/charts/{code}    one board company's daily candles plus its stats
GET /api/charts/ema-crossover  Screen any Chart's independent weekly 9/21
                          EMA crossover filter (see app/ema_crossover.py) --
                          unrelated to compute_stats()'s 50/200-day SMA

The data is written by scripts/update_charts.py (see app/charts.py). A
company that is on the board but not in that data yet -- added since the
last scheduled run -- is fetched live on first request and cached here,
so it appears in the gallery immediately.
"""
import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import charts, ema_crossover, universe
from ..database import get_db
from ..models import Company

router = APIRouter(prefix="/api/charts", tags=["charts"])
UNIVERSE_KEY = re.compile(r"^(?:NSE|BSE)-[A-Za-z0-9\-_&]{1,40}$")

_LIVE: dict[str, tuple[float, dict | None]] = {}
_LIVE_LOCK = threading.Lock()
_LIVE_TTL = 6 * 3600       # a found chart is good for the trading day
_MISS_TTL = 30 * 60        # a miss is retried every half hour


@router.get("")
def chart_index():
    idx = charts.load_index()
    return {
        "generated_at": idx.get("generated_at"),
        "latest_session": idx.get("latest_session"),
        "companies": idx.get("companies", {}),
    }


@router.get("/ema-crossover")
def ema_crossover_index():
    """Screen any Chart's own weekly 9/21 EMA crossover filter -- a separate
    screen from compute_stats()'s 50/200-day SMA trend classification."""
    return ema_crossover.load()


@router.get("/universe")
def universe_index():
    """Every actively traded NSE/BSE company: name, last price, stage, base
    count. Small enough to search in the browser; the candles come per stock."""
    idx = universe.index()
    return {**idx, "charts_ready": universe.status()["charts_ready"]}


@router.get("/u/{key}")
def universe_chart(key: str):
    if not UNIVERSE_KEY.match(key):
        raise HTTPException(status_code=404, detail="Unknown stock")
    data = universe.load(key)
    if data is None:
        st = universe.status()
        raise HTTPException(status_code=503 if st["error"] else 404,
                            detail="Charts for stocks outside the board are still loading — try again in a minute."
                                   if st["error"] else "No chart for this stock")
    series = {k: v for k, v in data.items() if k != "setups"}
    return {**series, "stats": charts.compute_stats(series), "setups": data.get("setups"), "source": "exchange"}


@router.get("/{code}")
def chart_for(code: str, db: Session = Depends(get_db)):
    series = charts.load_series(code)
    source = "exchange"
    if series is None:
        company = db.get(Company, code)
        if company is None:
            raise HTTPException(status_code=404, detail="Company not on the board")
        series, source = _live(code, company.data or {}), "live"
    if series is None:
        raise HTTPException(status_code=404, detail="No price data for this company yet")
    return {**series, "stats": charts.compute_stats(series), "source": source}


def _live(code: str, rec: dict) -> dict | None:
    now = time.time()
    with _LIVE_LOCK:
        hit = _LIVE.get(code)
        if hit and now - hit[0] < (_LIVE_TTL if hit[1] else _MISS_TTL):
            return hit[1]
    series = charts.fetch_live(rec)
    with _LIVE_LOCK:
        _LIVE[code] = (now, series)
    return series
