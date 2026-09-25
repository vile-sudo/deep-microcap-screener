"""
Chart gallery endpoints -- India and US, both market-aware via ?market=.

GET /api/charts                  chart stats for every company that has a
                                 chart, keyed by board code (sorting/
                                 filtering the gallery needs all of them up
                                 front; the candles are not included, so
                                 this stays small)
GET /api/charts/universe         every actively traded company in that
                                 market, one small line each -- what the
                                 gallery searches
GET /api/charts/u/{key}          one non-board stock's candles (+ India's
                                 base analysis, see app/universe.py)
GET /api/charts/{code}           one board company's daily candles plus
                                 its stats
GET /api/charts/ema-crossover    India only for now: Screen any Chart's
                                 independent weekly 9/21 EMA crossover
                                 filter (see app/ema_crossover.py)

The data is written by scripts/update_charts.py (India, app/charts.py) and
scripts/update_us_charts.py (US, app/charts_us.py). A board company not in
that data yet -- added since the last scheduled run -- is fetched live on
first request and cached here (India only for now; see _live()), so it
appears in the gallery immediately.
"""
import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from .. import charts, charts_us, ema_crossover, universe
from ..json_file import file_response
from ..database import get_db
from ..models import Company

router = APIRouter(prefix="/api/charts", tags=["charts"])
IN_UNIVERSE_KEY = re.compile(r"^(?:NSE|BSE)-[A-Za-z0-9\-_&]{1,40}$")
US_UNIVERSE_KEY = re.compile(r"^(?:BOARD-)?[A-Za-z0-9\-_&.]{1,40}$")

_LIVE: dict[str, tuple[float, dict | None]] = {}
_LIVE_LOCK = threading.Lock()
_LIVE_TTL = 6 * 3600       # a found chart is good for the trading day
_MISS_TTL = 30 * 60        # a miss is retried every half hour

MarketQ = Query("in", pattern="^(in|us)$")


@router.get("")
def chart_index(request: Request, market: str = MarketQ):
    mod = charts if market == "in" else charts_us
    return file_response(request, mod.INDEX_FILE, {"generated_at": None, "latest_session": None, "companies": {}})


@router.get("/ema-crossover")
def ema_crossover_index(request: Request, market: str = MarketQ):
    """Screen any Chart's own weekly 9/21 EMA crossover filter -- a separate
    screen from compute_stats()'s 50/200-day SMA trend classification. Same
    rule for both markets (backend/app/ema_crossover.py's screen()), just a
    separate file per market (no board/universe dedup in common -- India
    matches on ISIN, US on ticker; see write_us())."""
    cross_file = ema_crossover.CROSS_FILE if market == "in" else ema_crossover.CROSS_FILE_US
    return file_response(request, cross_file, {"as_of": None, "count": 0, "crossovers": {}})


@router.get("/universe")
def universe_index(request: Request, market: str = MarketQ):
    """Every actively traded company in that market: name, last price,
    stage/status. Small enough to search in the browser; the candles come
    per stock."""
    mkt = market.upper()
    mod = charts if market == "in" else charts_us
    idx_file = mod.CHART_DIR / "universe.json" if market == "in" else mod.UNIVERSE_INDEX
    return file_response(request, idx_file, {"as_of": None, "count": 0, "stocks": {}, "charts_ready": False},
                         extra={"charts_ready": bool(universe.status(mkt)["charts_ready"])})


@router.get("/u/{key}")
def universe_chart(key: str, market: str = MarketQ):
    mkt = market.upper()
    key_rx = IN_UNIVERSE_KEY if market == "in" else US_UNIVERSE_KEY
    if not key_rx.match(key):
        raise HTTPException(status_code=404, detail="Unknown stock")
    data = universe.load(key, mkt)
    if data is None:
        st = universe.status(mkt)
        raise HTTPException(status_code=503 if st["error"] else 404,
                            detail="Charts for stocks outside the board are still loading — try again in a minute."
                                   if st["error"] else "No chart for this stock")
    mod = charts if market == "in" else charts_us
    series = {k: v for k, v in data.items() if k != "setups"}
    return {**series, "stats": mod.compute_stats(series), "setups": data.get("setups"), "source": "exchange"}


@router.get("/{code}")
def chart_for(code: str, market: str = MarketQ, db: Session = Depends(get_db)):
    mkt = market.upper()
    mod = charts if market == "in" else charts_us
    series = mod.load_series(code)
    source = "exchange"
    if series is None:
        company = db.get(Company, (mkt, code))
        if company is None:
            raise HTTPException(status_code=404, detail="Company not on the board")
        if market == "in":
            series, source = _live(code, company.data or {}), "live"
        # US has no live-fallback fetch yet (Phase 3's chart pipeline is
        # nightly-batch only) -- series stays None, 404s below like any
        # other not-yet-charted company.
    if series is None:
        raise HTTPException(status_code=404, detail="No price data for this company yet")
    return {**series, "stats": mod.compute_stats(series), "source": source}


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
