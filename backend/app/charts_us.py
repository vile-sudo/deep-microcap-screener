"""
US-market chart data -- the Polygon.io counterpart to app/charts.py.

Polygon's "grouped daily bars" endpoint (one call = every US ticker's OHLCV
for one trading day) plays the same role NSE/BSE's bhavcopy plays for
India: a single, free, whole-market-per-day source, not one call per
ticker. See scripts/update_us_charts.py for the pipeline that uses this.

Reuses app.charts' genuinely market-agnostic pieces outright rather than
re-implementing them -- _adjust() (split/bonus back-adjustment, worked out
purely from the price series itself, no NSE-specific input), _sma(),
compute_stats() and safe_name() all operate on plain [date,o,h,l,c,v] rows
with no assumption about which exchange they came from.
"""
from __future__ import annotations

import gzip
import json
from datetime import date, timedelta
from pathlib import Path

import requests

from .charts import _adjust, _sma, compute_stats, safe_name  # noqa: F401 -- re-exported for callers

BACKEND_DIR = Path(__file__).resolve().parent.parent
CHART_DIR = BACKEND_DIR / "chart_data_us"
PRICES_DIR = CHART_DIR / "prices"
INDEX_FILE = CHART_DIR / "index.json"
UNIVERSE_INDEX = CHART_DIR / "universe.json"          # small, committed: what the gallery searches
UNIVERSE_DIR = BACKEND_DIR / "universe_charts_us"      # big, never committed: a GitHub release asset

POLYGON_API = "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks"

# A stock under this average daily dollar volume is too thin to trade --
# same role as app.charts.UNIVERSE_MIN_TURNOVER, a materially higher bar
# since $1 is worth far less "attention" than Rs 1 (roughly $1 ~ Rs 88) and
# US microcaps trade at very different typical prices than Indian ones.
UNIVERSE_MIN_TURNOVER = 5000.0
UNIVERSE_LOOKBACK = 20
UNIVERSE_SESSIONS = 750   # about three years, matching app.charts' own board-vs-universe split


def _get(url: str, session: requests.Session, api_key: str) -> dict | None:
    r = session.get(url, params={"adjusted": "true", "apiKey": api_key}, timeout=30)
    if r.status_code == 404:
        return None   # no session that day (weekend already filtered, but a holiday lands here)
    if r.status_code == 429:
        raise RuntimeError("Polygon rate limit hit (429) -- back off and retry next run")
    r.raise_for_status()
    return r.json()


def fetch_grouped_day(day: date, session: requests.Session, api_key: str) -> list[list] | None:
    """Every US ticker's OHLCV for one trading day, as
    [ticker, o, h, l, c, v] rows, or None if the market wasn't open.

    One call regardless of how many thousand tickers traded -- the whole
    reason this pipeline can live inside Polygon's free 5-calls/minute tier
    at all; the per-ticker candle endpoint would not."""
    data = _get(f"{POLYGON_API}/{day.isoformat()}", session, api_key)
    if not data or not data.get("results"):
        return None
    return [[r["T"], r["o"], r["h"], r["l"], r["c"], r.get("v") or 0] for r in data["results"] if r.get("T")]


def write_day(path: Path, rows: list[list]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(rows, f, separators=(",", ":"))


def read_day(path: Path) -> list[list]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _to_raw_tuples(days: list[tuple[date, dict]], ticker: str) -> list[tuple]:
    """One ticker's (day, o, h, l, c, v, prev) tuples across every cached
    session it appears in -- app.charts._adjust()'s expected input shape.
    prev (the restated-previous-close hint bhavcopy sometimes carries) has
    no Polygon equivalent, so it's always 0 here; _adjust() falls back to
    its overnight-gap detection in that case, which is its primary,
    market-agnostic method anyway -- the restated-close path is a bhavcopy-
    specific refinement on top of it, not a requirement."""
    out = []
    for day, book in days:
        r = book.get(ticker)
        if r:
            out.append((day.isoformat(), r[1], r[2], r[3], r[4], r[5], 0))
    return out


def build_series_us(codes: list[str], days: list[tuple[date, dict]]) -> dict[str, dict]:
    """Board companies' candles, full history -- the US-market counterpart
    to app.charts.build_series(). codes are Polygon/US tickers directly
    (Company.code for market="US" rows), no dual-exchange matching needed
    since Polygon is already one flat market-wide list."""
    out = {}
    for code in codes:
        raw = _to_raw_tuples(days, code)
        if len(raw) < 5:
            continue
        out[code] = {"symbol": code, "exchange": "US", "rows": _adjust(raw)}
    return out


def active_universe(days: list[tuple[date, dict]], lookback: int = UNIVERSE_LOOKBACK) -> dict[str, dict]:
    """ticker -> {turnover, sessions} for every US ticker that genuinely
    traded in the last `lookback` sessions -- app.charts.active_universe()'s
    counterpart. No ISIN/equity-type filtering (Polygon's grouped bars are
    already common stock tickers; unlike bhavcopy they don't mix in bonds/
    NCDs under company-looking names), no exchange-collapse (Polygon has
    already deduped NYSE/NASDAQ/AMEX/OTC into one row per ticker)."""
    recent = days[-lookback:]
    traded: dict[str, dict] = {}
    for _, book in recent:
        for ticker, r in book.items():
            if not r[5]:   # no volume that day
                continue
            e = traded.setdefault(ticker, {"turnover": 0.0, "sessions": 0})
            e["turnover"] += r[4] * r[5]
            e["sessions"] += 1
    return {t: e for t, e in traded.items() if e["turnover"] / max(1, e["sessions"]) >= UNIVERSE_MIN_TURNOVER}


def build_universe_us(days: list[tuple[date, dict]], skip_codes: frozenset = frozenset()):
    """Candles for every actively traded US ticker, yielded as (ticker,
    series) -- a generator for the same reason app.charts.build_universe()
    is one: thousands of multi-year series held at once is more memory
    than the nightly job needs when each can be written out as it arrives.

    skip_codes leaves out whatever build_series_us already charted for the
    US board, so a board company isn't written twice."""
    for ticker, meta in active_universe(days).items():
        if ticker in skip_codes:
            continue
        raw = _to_raw_tuples(days, ticker)
        if len(raw) < 20:
            continue
        rows = _adjust(raw)
        yield ticker, {"symbol": ticker, "exchange": "US", "rows": rows[-UNIVERSE_SESSIONS:]}


def load_index() -> dict:
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"generated_at": None, "companies": {}}


def load_series(code: str) -> dict | None:
    try:
        return json.loads((PRICES_DIR / f"{safe_name(code)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
