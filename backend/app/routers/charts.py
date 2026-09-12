"""
Chart gallery endpoints.

GET /api/charts          chart stats for every company that has a chart,
                         keyed by board code (sorting/filtering the gallery
                         needs all of them up front; the candles are not
                         included, so this stays small)
GET /api/charts/{code}   one company's daily candles plus its stats

The data is written by scripts/update_charts.py (see app/charts.py). A
company that is on the board but not in that data yet -- added since the
last scheduled run -- is fetched live on first request and cached here,
so it appears in the gallery immediately.
"""
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import charts
from ..database import get_db
from ..models import Company

router = APIRouter(prefix="/api/charts", tags=["charts"])

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
