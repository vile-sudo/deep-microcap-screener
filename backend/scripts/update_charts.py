"""
Refresh the Chart gallery for every company on the board.

    cd backend
    python scripts/update_charts.py

Run by .github/workflows/charts.yml every weekday evening and whenever
data/companies_raw.json changes, so a company added to the board gets its
chart without anyone touching this script.

Steps:
  1. Make sure NSE and BSE daily bhavcopies back to about January 2020 are
     in the local cache (.bhav_cache/, kept between Action runs by
     actions/cache). Only days not already cached are downloaded -- normally
     just today, except the one-time backfill the first time this runs.
  2. Rebuild multi-year candles for every company in companies_raw.json,
     as far back as real exchange data goes (see app/charts.fetch_bhavcopy).
  3. Write chart_data/prices/<code>.json and chart_data/index.json, and
     drop the price file of any company no longer on the board.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import alerts, setups  # noqa: E402
from app.charts import (  # noqa: E402
    BACKEND_DIR, CHART_DIR, INDEX_FILE, PRICES_DIR, _adjust,
    build_series, build_universe, isin_index, compute_stats, fetch_bhavcopy, load_index, read_day, safe_name, write_day,
)

SETUPS_FILE = CHART_DIR / "setups.json"
ALERTS_FILE = CHART_DIR / "alerts.json"
UNIVERSE_INDEX = CHART_DIR / "universe.json"        # small, committed: what Screen any Chart searches
UNIVERSE_DIR = BACKEND_DIR / "universe_charts"      # big, never committed: uploaded as a release asset

RAW = BACKEND_DIR / "data" / "companies_raw.json"
CACHE = BACKEND_DIR / ".bhav_cache"
NO_SESSION = CACHE / "no_session.json"   # weekday holidays, so they aren't re-asked every run
FMT = ".v2.csv.gz"                       # cache file format: v2 adds the ISIN column
LOOKBACK_DAYS = 2450                     # calendar days: real NSE data via app.charts.fetch_bhavcopy goes back to
                                          # about January 2020 (a legacy-format fallback beyond ~Dec 2023); this
                                          # window comfortably covers all of it for the chart gallery's "X-ray"
                                          # multi-year base view, and two years is plenty to date IPO listings
BACKFILL_BUDGET = 40 * 60                # seconds spent downloading missing days in one run; what is
                                         # left over is fetched by the next run rather than risking
                                         # a job that never finishes and saves nothing


def ist_today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def sync_cache(session: requests.Session) -> None:
    CACHE.mkdir(exist_ok=True)
    try:
        no_session = set(json.loads(NO_SESSION.read_text()))
    except (OSError, ValueError):
        no_session = set()
    today = ist_today()
    wanted = [today - timedelta(days=i) for i in range(LOOKBACK_DAYS)]
    todo = [(d, ex) for d in wanted if d.weekday() < 5 for ex in ("NSE", "BSE")
            if not (CACHE / f"{ex}-{d:%Y%m%d}{FMT}").exists() and f"{ex}-{d:%Y%m%d}" not in no_session]

    # A first run against years of history has thousands of files to fetch,
    # which can outlast the CI job. Every day that lands is written to the
    # cache as it arrives, so stopping early costs nothing: the run finishes
    # with what it has and the next one picks up where this stopped.
    deadline = time.monotonic() + BACKFILL_BUDGET
    stopped = 0

    def one(job):
        nonlocal stopped
        d, ex = job
        if time.monotonic() > deadline:
            stopped += 1
            return d, ex, False
        try:
            return d, ex, fetch_bhavcopy(d, ex, session)
        except RuntimeError as e:
            print(f"  {e}")
            return d, ex, False          # transient failure: try again next run
        except Exception as e:  # noqa: BLE001 - one malformed day (a parsing surprise years
            # back, say) must never abort a run that is otherwise fetching thousands of good days
            print(f"  {ex} {d}: could not parse ({e}); skipping this day, will retry next run")
            return d, ex, False

    got = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for d, ex, rows in pool.map(one, todo):
            if rows:
                write_day(CACHE / f"{ex}-{d:%Y%m%d}{FMT}", rows)
                got += 1
            elif rows is None and (today - d).days > 3:
                # no file for a weekday more than 3 days back is a market holiday;
                # a recent one may simply not be published yet
                no_session.add(f"{ex}-{d:%Y%m%d}")

    # forget what fell out of the window
    oldest = today - timedelta(days=LOOKBACK_DAYS)
    for f in CACHE.glob("*.csv.gz"):
        # files from an older cache format (no ISIN column) are re-fetched
        if not f.name.endswith(FMT) or datetime.strptime(f.name[4:12], "%Y%m%d").date() < oldest:
            f.unlink()
    no_session = {k for k in no_session if datetime.strptime(k[4:], "%Y%m%d").date() >= oldest}
    NO_SESSION.write_text(json.dumps(sorted(no_session)))
    left = f", {stopped} left for the next run (time budget reached)" if stopped else ""
    print(f"bhavcopy cache: {len(todo)} day-files checked, {got} downloaded{left}")


def load_days() -> list:
    """Every cached session in memory, oldest first.

    Years of daily files for thousands of stocks is the one structure in this
    script big enough to matter: each ticker, company name and ISIN otherwise
    gets a fresh string object per day, which is most of it. Interning them
    against a single pool cuts the whole thing by about 40% and makes the
    difference between a nightly job that fits the runner and one that does
    not."""
    pool: dict[str, str] = {}

    def keep(s):
        if type(s) is not str:
            return s
        got = pool.get(s)
        if got is None:
            pool[s] = got = s
        return got

    by_day: dict[date, list] = {}
    for f in sorted(CACHE.glob(f"*{FMT}")):
        d = datetime.strptime(f.name[4:12], "%Y%m%d").date()
        slot = by_day.setdefault(d, [{}, {}])
        book = {}
        for r in read_day(f):
            r[0], r[1], r[2], r[9] = keep(r[0]), keep(r[1]), keep(r[2]), keep(r[9])
            book[r[0]] = r
        slot[0 if f.name.startswith("NSE") else 1] = book
    return [(d, nse, bse) for d, (nse, bse) in sorted(by_day.items())]


def main() -> int:
    records = list({r["code"]: r for r in json.loads(RAW.read_text(encoding="utf-8")) if r.get("code")}.values())
    sync_cache(requests.Session())
    days = load_days()
    if not days:
        print("charts: no bhavcopy data available; existing chart data left as is")
        return 1

    series = build_series(records, days)
    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    old = load_index().get("companies", {})
    companies, kept, missing = {}, 0, []
    for rec in records:
        code = rec["code"]
        path = PRICES_DIR / f"{safe_name(code)}.json"
        if code in series:
            path.write_text(json.dumps(series[code], separators=(",", ":")), encoding="utf-8")
            companies[code] = compute_stats(series[code])
        elif code in old and path.exists():
            companies[code] = old[code]   # suspended or not traded lately: keep its last chart
            kept += 1
        else:
            missing.append(code)

    wanted = {f"{safe_name(r['code'])}.json" for r in records}
    for f in PRICES_DIR.glob("*.json"):
        if f.name not in wanted:
            f.unlink()

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_session": days[-1][0].isoformat(),
        "companies": dict(sorted(companies.items())),
        "no_data": sorted(missing),
    }
    prior = load_index()
    if all(prior.get(k) == index[k] for k in ("latest_session", "companies", "no_data")):
        # nothing new (e.g. the late retry after a normal run): leave the file
        # untouched so the workflow has nothing to commit and nothing to redeploy
        index["generated_at"] = prior.get("generated_at") or index["generated_at"]
    INDEX_FILE.write_text(json.dumps(index, indent=1), encoding="utf-8")

    write_setups(records, series, days)

    print(f"charts: {len(series)} built from {len(days)} sessions (latest {days[-1][0]}), "
          f"{kept} kept from last run, {len(missing)} with no exchange data: {', '.join(missing) or 'none'}")
    return 0


def write_universe(days: list, board: dict, rank) -> int:
    """Every actively traded NSE/BSE company, for "Screen any Chart".

    Two outputs, deliberately split:
      chart_data/universe.json   one small line per stock (symbol, name, last
                                 price, stats) -- committed, so searching and
                                 browsing works the moment the page opens;
      universe_charts/<KEY>.json its candles and its base/X-ray analysis --
                                 thousands of files, hundreds of MB, so these
                                 are uploaded as a GitHub release asset rather
                                 than committed, and the API fetches them on
                                 demand (see routers/charts.py).
    The board's own companies keep their fuller, committed charts and are
    skipped here; the index points at them instead."""
    on_board_isins = frozenset(s.get("isin") for s in board.values() if s.get("isin"))
    on_board_keys = frozenset((s["exchange"], s.get("key") or s["symbol"]) for s in board.values())
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    for f in UNIVERSE_DIR.glob("*.json"):
        f.unlink()

    index, charted = {}, 0
    for key, s in build_universe(days, on_board_isins, on_board_keys):
        charted += 1
        stats = compute_stats(s)
        a = setups.analyze(s["rows"], rank, s.get("listed"))
        payload = {**s, "setups": a}
        (UNIVERSE_DIR / f"{key}.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        recent = s["rows"][-20:]
        index[key] = {"symbol": s["symbol"], "name": s["name"], "exchange": s["exchange"],
                      "last": stats.get("last"), "chg_pct": stats.get("chg_pct"),
                      "from_high_pct": stats.get("from_high_pct"), "status": stats.get("status"),
                      "vol_ratio": stats.get("vol_ratio"), "sessions": len(s["rows"]),
                      # rupees traded a day, so a thin ticker at a 52-week high
                      # cannot be mistaken for a liquid one
                      "turnover": round(sum(r[4] * r[5] for r in recent) / max(1, len(recent))),
                      "stage": (a or {}).get("stage"), "rs": (a or {}).get("rs"),
                      "bases": len((a or {}).get("breakouts") or [])}
    # the board's companies belong in the same search: same shape, flagged, and
    # pointing at the board code so the chart drawer opens the richer version
    for code, s in board.items():
        stats = compute_stats(s)
        index[f"BOARD-{safe_name(code)}"] = {"symbol": s["symbol"], "name": s.get("name") or s["symbol"],
                                             "exchange": s["exchange"], "code": code, "on_board": True,
                                             "last": stats.get("last"), "chg_pct": stats.get("chg_pct"),
                                             "from_high_pct": stats.get("from_high_pct"),
                                             "status": stats.get("status"), "vol_ratio": stats.get("vol_ratio"),
                                             "sessions": len(s["rows"])}
    UNIVERSE_INDEX.write_text(json.dumps({
        "as_of": days[-1][0].isoformat(),
        "count": len(index),
        "stocks": dict(sorted(index.items())),
    }, separators=(",", ":")), encoding="utf-8")
    size_mb = sum(f.stat().st_size for f in UNIVERSE_DIR.glob("*.json")) / 1e6
    print(f"universe: {charted} traded companies charted ({size_mb:.0f} MB in {UNIVERSE_DIR.name}/), "
          f"{len(index)} rows in universe.json")
    return charted


def write_setups(records: list[dict], series: dict, days: list) -> None:
    """Market view's stages, measures and feed -- see app/setups.py."""
    latest = days[-1][0].isoformat()
    # the NSE universe: RS ratings are ranked against every liquid NSE stock,
    # and "broke out market-wide" counts breakouts across all of them
    raw: dict[str, list] = {}
    names: dict[str, str] = {}
    for day, nse_rows, _ in days:
        for key, r in nse_rows.items():
            raw.setdefault(key, []).append((day.isoformat(), r[3], r[4], r[5], r[6], r[7], r[8]))
            names[key] = r[2]
    cutoff = days[min(20, len(days) - 1)][0].isoformat()
    _, isin_first = isin_index(days)
    isin_of = {}
    for _, nse_rows, _ in days:
        for key, r in nse_rows.items():
            if r[9]:
                isin_of[key] = r[9]
    universe, listed = {}, {}
    for key, tuples in raw.items():
        first_seen = min(tuples[0][0], isin_first.get(isin_of.get(key)) or tuples[0][0])
        is_new = first_seen > cutoff
        if tuples[-1][0] != latest or len(tuples) < (20 if is_new else 80):
            continue
        tail = tuples[-50:]
        if sum(t[4] * t[5] for t in tail) / len(tail) < 1e7:   # under Rs 1 crore a day: not liquid
            continue
        universe[key] = _adjust(tuples)
        if is_new:
            listed[key] = first_seen
    rank = setups.percentile_ranker([x for x in (setups.rs_score([r[4] for r in rows[-250:]]) for rows in universe.values()) if x is not None])

    board_symbols = {(v["exchange"], v["symbol"]) for v in series.values()}
    market = {"vcp": [], "ipo": []}
    for key, rows in universe.items():
        for screen, hit in setups.market_breakout_today(rows[-250:] if key not in listed else rows, listed.get(key)).items():
            market[screen].append({"symbol": key, "name": names.get(key, key).title(), "on_board": ("NSE", key) in board_symbols,
                                   "listed": listed.get(key), **hit})
    for items in market.values():
        items.sort(key=lambda x: -x["vol_x"])

    stocks, feed = {}, []
    for rec in records:
        code = rec["code"]
        if code not in series or series[code]["rows"][-1][0] != latest:
            continue
        a = setups.analyze(series[code]["rows"], rank, series[code].get("listed"))
        if not a:
            continue
        for item in a.pop("feed"):
            feed.append({"code": code, "name": rec.get("name"), "close": a["last"]["c"], "chg_pct": a["last"]["chg_pct"], **item})
        stocks[code] = a
    feed.sort(key=lambda x: (x["order"], -abs(x["chg_pct"] or 0)))
    stages = ("forming", "fresh", "climbing", "played")
    counts = {k: sum(1 for a in stocks.values() if a["stage"] == k) for k in stages}
    counts_ipo = {k: sum(1 for a in stocks.values() if a.get("ipo", {}).get("stage") == k) for k in stages}
    SETUPS_FILE.write_text(json.dumps({
        "as_of": latest,
        "universe": len(universe),
        "counts": counts,
        "counts_ipo": counts_ipo,
        "ipo_listed": sum(1 for a in stocks.values() if "ipo" in a),
        "market_breakouts": market["vcp"],
        "market_breakouts_ipo": market["ipo"],
        "feed": feed,
        "stocks": dict(sorted(stocks.items())),
    }, separators=(",", ":")), encoding="utf-8")
    board_items = alerts.board_alerts(records, series, stocks, latest)
    market_items = alerts.market_alerts(universe, names, listed, market["ipo"], board_symbols, latest)
    alerts.write(ALERTS_FILE, latest, board_items + market_items)
    write_universe(days, series, rank)
    tally = {}
    for it in board_items:
        tally[it["type"]] = tally.get(it["type"], 0) + 1
    print(f"alerts: board {tally}; market-wide {len(market_items)}")
    print(f"setups: VCP {counts}; IPO base {counts_ipo} ({sum(1 for a in stocks.values() if 'ipo' in a)} recent listings); "
          f"market-wide breakouts {len(market['vcp'])} VCP / {len(market['ipo'])} IPO across {len(universe)} liquid NSE stocks; {len(feed)} feed items")


if __name__ == "__main__":
    sys.exit(main())
