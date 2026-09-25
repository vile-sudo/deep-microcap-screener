"""
Refresh the Chart Gallery for the US board and the wider actively-traded
US market -- the Polygon.io counterpart to update_charts.py.

    cd backend
    python scripts/update_us_charts.py

Needs POLYGON_API_KEY (free tier, polygon.io/dashboard/signup) as an env var.

Steps:
  1. Make sure Polygon's grouped-daily bars are cached locally
     (.polygon_cache/, kept between CI runs via actions/cache) back
     LOOKBACK_DAYS -- one call per missing trading day, not per ticker,
     which is what makes 5 years of the whole US market fit inside
     Polygon's free 5-calls/minute tier at all. Only missing days are
     fetched, rate-limited to stay under that ceiling and time-budgeted
     per run (see sync_cache()) -- the first run's ~1,260-day backfill
     spans several runs; every one after that is a single day.
  2. Rebuild multi-year candles for every US board company
     (data/companies_us_raw.json), full history.
  3. Rebuild candles for every actively-traded US ticker not already on
     the board (build_universe_us) -- the Chart Gallery's "All US market"
     scope. Small committed search index (chart_data_us/universe.json) +
     big gitignored per-ticker files (universe_charts_us/), uploaded as a
     GitHub release asset by charts_us.yml, same split app.charts.py uses
     for the India gallery and for the same reason (too big to commit).
  4. Run the weekly EMA crossover screen (ema_crossover.write_us) and
     Market view's base/breakout scanner (setups.py, write_setups_us
     below) over the board and the wider universe.

Market view runs all four of setups.py's screens (VCP, Blue Sky,
Multi-year, IPO base). Not built yet: CSV export, the IPO breakout-window
sub-filter, the share button and live index quotes -- see app/setups.py's
own docstring for what each screen needs. No Alerts integration either
(alerts.py) -- that's a notification feature layered on top of this data,
not something Market view itself needs to render.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import ema_crossover, setups  # noqa: E402
from app.setups import _usd  # noqa: E402
from app.charts_us import (  # noqa: E402
    CHART_DIR, INDEX_FILE, PRICES_DIR, UNIVERSE_DIR, UNIVERSE_INDEX,
    build_series_us, build_universe_us, compute_stats, fetch_grouped_day, load_index, read_day, safe_name, write_day,
)

SETUPS_FILE = CHART_DIR / "setups.json"      # Market view's stages, measures and feed for the US board
MARKET_ALL = CHART_DIR / "market_all.json"   # the same four (for now, one) screens over the wider universe

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW_US = BACKEND_DIR / "data" / "companies_us_raw.json"
CACHE = BACKEND_DIR / ".polygon_cache"
NO_SESSION = CACHE / "no_session.json"
FMT = ".json.gz"
LOOKBACK_DAYS = 365 * 5 + 30      # 5 years, +30 days' slack for weekends/holidays eaten out of the window
BACKFILL_BUDGET = 40 * 60         # seconds spent downloading missing days in one run -- what's left
                                  # over is picked up by the next run, same reasoning as update_charts.py
MIN_CALL_INTERVAL = 13.0          # seconds between Polygon calls -- 5/minute allows 12s; 13 leaves margin

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def sync_cache(session: requests.Session) -> None:
    CACHE.mkdir(exist_ok=True)
    try:
        no_session = set(json.loads(NO_SESSION.read_text()))
    except (OSError, ValueError):
        no_session = set()
    today = utc_today()
    wanted = [today - timedelta(days=i) for i in range(1, LOOKBACK_DAYS + 1)]   # yesterday back; today may not have closed yet
    todo = [d for d in wanted if d.weekday() < 5
            and not (CACHE / f"{d:%Y%m%d}{FMT}").exists() and d.isoformat() not in no_session]

    deadline = time.monotonic() + BACKFILL_BUDGET
    last_call = 0.0
    got = stopped = 0
    for d in todo:
        if time.monotonic() > deadline:
            stopped += 1
            continue
        wait = MIN_CALL_INTERVAL - (time.monotonic() - last_call)
        if wait > 0:
            time.sleep(wait)
        last_call = time.monotonic()
        try:
            rows = fetch_grouped_day(d, session, POLYGON_API_KEY)
        except RuntimeError as e:
            print(f"  {e}")
            continue   # transient (rate limit): try again next run
        except requests.RequestException as e:
            print(f"  {d}: {e}")
            continue
        if rows:
            write_day(CACHE / f"{d:%Y%m%d}{FMT}", rows)
            got += 1
        elif (today - d).days > 3:
            no_session.add(d.isoformat())   # a weekday more than 3 days back with no data is a market holiday

    oldest = today - timedelta(days=LOOKBACK_DAYS)
    for f in CACHE.glob(f"*{FMT}"):
        if datetime.strptime(f.name[:8], "%Y%m%d").date() < oldest:
            f.unlink()
    no_session = {k for k in no_session if date.fromisoformat(k) >= oldest}
    NO_SESSION.write_text(json.dumps(sorted(no_session)))
    left = f", {stopped} left for the next run (time budget reached)" if stopped else ""
    print(f"polygon cache: {len(todo)} day-files checked, {got} downloaded{left}")


def load_days() -> list[tuple[date, dict]]:
    """Every cached session in memory, oldest first, as (date, {ticker: row})."""
    out = []
    for f in sorted(CACHE.glob(f"*{FMT}")):
        d = datetime.strptime(f.name[:8], "%Y%m%d").date()
        book = {row[0]: row for row in read_day(f)}
        out.append((d, book))
    return out


def main() -> int:
    if not POLYGON_API_KEY:
        print("update_us_charts: POLYGON_API_KEY is not set", file=sys.stderr)
        return 2

    records = json.loads(RAW_US.read_text(encoding="utf-8")) if RAW_US.exists() else []
    codes = sorted({r["code"] for r in records if r.get("code")})

    sync_cache(requests.Session())
    days = load_days()
    if not days:
        print("update_us_charts: no Polygon data available yet; existing chart data left as is")
        return 1

    series = build_series_us(codes, days)
    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    old = load_index().get("companies", {})
    companies, kept, missing = {}, 0, []
    for code in codes:
        path = PRICES_DIR / f"{safe_name(code)}.json"
        if code in series:
            path.write_text(json.dumps(series[code], separators=(",", ":")), encoding="utf-8")
            companies[code] = compute_stats(series[code])
        elif code in old and path.exists():
            companies[code] = old[code]
            kept += 1
        else:
            missing.append(code)

    wanted = {f"{safe_name(c)}.json" for c in codes}
    for f in PRICES_DIR.glob("*.json"):
        if f.name not in wanted:
            f.unlink()

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_session": days[-1][0].isoformat(),
        "companies": dict(sorted(companies.items())),
        "no_data": sorted(missing),
    }
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"update_us_charts: {len(series)} board charts built from {len(days)} sessions "
          f"(latest {days[-1][0]}), {kept} kept from last run, {len(missing)} with no data: {', '.join(missing) or 'none'}")

    crossovers = ema_crossover.write_us(days, series, build_universe_us)
    print(f"ema crossover: {crossovers} US stocks with a weekly 9/21 cross in the last 8 weeks")

    write_setups_us(records, series, days)
    return 0


def write_universe(days: list, board: dict, rank) -> tuple[int, dict[str, list[dict]]]:
    """Chart Gallery's "All US market" scope, same as before, plus (new)
    Market view's four screens (VCP, Blue Sky, Multi-year, IPO base) run
    inline over the same tickers while their candles are already in hand --
    one pass rather than a second walk over the whole universe. Writes
    chart_data_us/market_all.json (what the "All US market" scope shows)
    and returns each screen's tickers that broke out market-wide today,
    for setups.json's own "N broke out market-wide" banner (write_setups_us
    below).

    "Listed" (the IPO base screen's gate) is a heuristic, same one India's
    write_setups() uses: a ticker whose earliest row in our cache is within
    the last ~20 sessions is treated as newly listed. Not a real listing
    date -- Polygon's grouped-daily bars don't carry one -- but the same
    "first appeared within our loaded window" approximation India already
    relies on, so it degrades the same way for both markets."""
    skip_codes = frozenset(board.keys())
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    for f in UNIVERSE_DIR.glob("*.json"):
        f.unlink()

    latest = days[-1][0].isoformat()
    cutoff = days[min(20, len(days) - 1)][0].isoformat()
    index, charted = {}, 0
    market = {sc: [] for sc in setups.SCREENS}
    market_all_stocks, feed = {}, []
    for ticker, s in build_universe_us(days, skip_codes):
        charted += 1
        stats = compute_stats(s)
        rows = s["rows"]
        first_seen = rows[0][0] if rows else None
        listed = first_seen if first_seen and first_seen > cutoff else None
        # A handful of thin OTC tickers carry a genuine $0.00 print on some
        # day (a Polygon data artifact, not a real trading pattern) --
        # setups.analyze() divides by the prior close with no zero guard
        # (fine for India's curated board + liquid-NSE universe, where that
        # never happens; not fine for the full US market's long OTC tail).
        a = (setups.analyze(rows, rank, listed=listed, long_bases=True, fmt_money=_usd)
             if rows and rows[-1][0] == latest and len(rows) >= 20 and all(r[4] > 0 for r in rows) else None)
        if a:
            m = setups.compact(a)
            if m:
                market_all_stocks[ticker] = {"name": ticker, "symbol": ticker, "exchange": "US", **m}
                for item in a.get("feed") or []:
                    if item["kind"] not in ("high52", "low52"):   # thousands of those market-wide; stage changes only
                        feed.append({"code": ticker, "name": ticker, "close": a["last"]["c"],
                                     "chg_pct": a["last"]["chg_pct"], **item})
            for screen, hit in setups.market_breakout_today(rows, listed).items():
                market[screen].append({"symbol": ticker, "name": ticker, "on_board": False, "listed": listed, **hit})
        payload = {**s, "setups": a}
        (UNIVERSE_DIR / f"{ticker}.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        recent = rows[-20:]
        index[ticker] = {"symbol": ticker, "name": ticker, "exchange": "US",
                         "last": stats.get("last"), "chg_pct": stats.get("chg_pct"),
                         "from_high_pct": stats.get("from_high_pct"), "status": stats.get("status"),
                         "vol_ratio": stats.get("vol_ratio"), "sessions": len(rows),
                         "turnover": round(sum(r[4] * r[5] for r in recent) / max(1, len(recent))),
                         "stage": (a or {}).get("stage"), "rs": (a or {}).get("rs"),
                         "bases": len((a or {}).get("breakouts") or [])}
    for code, s in board.items():
        stats = compute_stats(s)
        index[f"BOARD-{safe_name(code)}"] = {"symbol": code, "name": code, "exchange": "US", "code": code,
                                             "on_board": True, "last": stats.get("last"), "chg_pct": stats.get("chg_pct"),
                                             "from_high_pct": stats.get("from_high_pct"), "status": stats.get("status"),
                                             "vol_ratio": stats.get("vol_ratio"), "sessions": len(s["rows"])}
    UNIVERSE_INDEX.write_text(json.dumps({
        "as_of": latest, "count": len(index), "stocks": dict(sorted(index.items())),
    }, separators=(",", ":")), encoding="utf-8")
    for items in market.values():
        items.sort(key=lambda x: -x["vol_x"])
    feed.sort(key=lambda x: (x["order"], -abs(x["chg_pct"] or 0)))
    MARKET_ALL.write_text(json.dumps({
        "as_of": latest, "scanned": charted, "feed": feed, "stocks": dict(sorted(market_all_stocks.items())),
    }, separators=(",", ":")), encoding="utf-8")
    size_mb = sum(f.stat().st_size for f in UNIVERSE_DIR.glob("*.json")) / 1e6
    print(f"universe: {charted} traded US tickers charted ({size_mb:.0f} MB in {UNIVERSE_DIR.name}/), "
          f"{len(index)} rows in universe.json; market_all.json {len(market_all_stocks)} in a setup")
    return charted, market


def write_setups_us(records: list[dict], board: dict, days: list) -> None:
    """Market view's stages, measures and feed for the US board -- all four
    screens (VCP, Blue Sky, Multi-year, IPO base). Same engine as India's
    write_setups() in update_charts.py, fed from Polygon's grouped-daily
    rows instead of bhavcopy, and with the same "listed" heuristic
    write_universe() above uses (no real listing date in Polygon data)."""
    latest = days[-1][0].isoformat()
    cutoff = days[min(20, len(days) - 1)][0].isoformat()
    rank_pool = []
    for _, s in build_universe_us(days, frozenset()):
        rows = s["rows"]
        if rows and rows[-1][0] == latest and len(rows) >= 127:
            rank_pool.append(setups.rs_score([r[4] for r in rows[-250:]]))
    rank = setups.percentile_ranker([x for x in rank_pool if x is not None])

    charted, market = write_universe(days, board, rank)

    stocks, feed = {}, []
    for rec in records:
        code = rec.get("code")
        if not code or code not in board:
            continue
        rows = board[code]["rows"]
        if not rows or rows[-1][0] != latest or not all(r[4] > 0 for r in rows):
            continue
        first_seen = rows[0][0]
        listed = first_seen if first_seen > cutoff else None
        a = setups.analyze(rows, rank, listed=listed, long_bases=True, fmt_money=_usd)
        if not a:
            continue
        for item in a.pop("feed"):
            feed.append({"code": code, "name": rec.get("name"), "close": a["last"]["c"],
                        "chg_pct": a["last"]["chg_pct"], **item})
        stocks[code] = a
    feed.sort(key=lambda x: (x["order"], -abs(x["chg_pct"] or 0)))
    stages = ("forming", "fresh", "climbing", "played")
    counts = {k: sum(1 for a in stocks.values() if a["stage"] == k) for k in stages}
    per_screen = {sc: {k: sum(1 for a in stocks.values() if a.get(sc, {}).get("stage") == k) for k in stages}
                  for sc in setups.SCREENS if sc != "vcp"}
    SETUPS_FILE.write_text(json.dumps({
        "as_of": latest,
        "universe": len(rank_pool),
        "counts": counts,
        **{f"counts_{sc}": c for sc, c in per_screen.items()},
        "ipo_listed": sum(1 for a in stocks.values() if "ipo" in a),
        "market_breakouts": market["vcp"],
        **{f"market_breakouts_{sc}": market[sc] for sc in setups.SCREENS if sc != "vcp"},
        "feed": feed,
        "stocks": dict(sorted(stocks.items())),
    }, separators=(",", ":")), encoding="utf-8")
    print(f"setups (US): VCP {counts}; Blue sky {per_screen['bluesky']}; Multi-year {per_screen['multiyear']}; "
          f"IPO base {per_screen['ipo']} ({sum(1 for a in stocks.values() if 'ipo' in a)} recent listings); "
          f"market-wide breakouts {' / '.join(f'{len(market[sc])} {sc}' for sc in setups.SCREENS)} "
          f"across {len(rank_pool)} liquid US stocks (of {charted} charted); {len(feed)} feed items")


if __name__ == "__main__":
    sys.exit(main())
