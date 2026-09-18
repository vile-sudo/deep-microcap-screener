"""
One-time backfill: full daily OHLCV history for every actively traded NSE/BSE
company, from its real listing day to today -- not the ~3-6 year windows the
Chart gallery and Screen any Chart keep for size reasons.

    cd backend
    python scripts/backfill_full_history.py                # keep going until done
    python scripts/backfill_full_history.py --budget 1800   # seconds per run (default 3000)

Resumable and safe to Ctrl-C or re-run: every downloaded day is cached to disk
as it arrives (.bhav_cache_full/, separate from the nightly job's .bhav_cache/
so this never gets pruned by it), and the per-company output files are
rewritten from whatever is cached so far on every run -- an interrupted run
loses nothing, a re-run just picks up more days and produces a fuller file.

Where the data stops
---------------------
NSE: real per-day files are live back to at least 1996 (verified by fetching
real days, not just documented) via a third, older bhavcopy format --
app.charts.fetch_bhavcopy tries the current ("UDiFF") format, then the
~2020-2023 legacy format, then this one. EARLIEST below is set there.

BSE: only the current bhavcopy format is wired up, and it was verified (by
probing real dates) to only go back to about January 2024 -- the URL pattern
older tools document for deeper BSE history (.../BhavCopy/Equity/EQddmmyy_CSV.zip)
returns BSE's homepage now, not data. A BSE-only company (never listed on
NSE) will not get real pre-2024 history from this script; one listed on both
exchanges is unaffected, since NSE is preferred (see "Dedup" below).

Dedup: NSE preferred
---------------------
app.charts.active_universe() already does exactly this -- matches a company's
NSE and BSE listings by ISIN and drops the BSE line when the same ISIN also
trades on NSE -- reused as is, not reimplemented here.

Output
------
backend/full_history/<EXCHANGE>-<key>.json, one per company:
  {"symbol", "exchange", "isin", "name", "listed", "rows": [[date,o,h,l,c,v], ...]}
Not committed (thousands of files, hundreds of MB) -- same treatment as
backend/universe_charts/.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.charts import (  # noqa: E402
    BACKEND_DIR, _adjust, active_universe, fetch_bhavcopy, isin_index, read_day, universe_key, write_day,
)

OUT_DIR = BACKEND_DIR / "full_history"
CACHE = BACKEND_DIR / ".bhav_cache_full"
NO_SESSION = CACHE / "no_session.json"
FMT = ".v2.csv.gz"
EARLIEST = date(1996, 1, 1)   # confirmed live for NSE; see module docstring
DEFAULT_BUDGET = 3000         # seconds spent downloading per run; the rest is left for the next run


def ist_today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def trading_days(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def sync_cache(budget: int) -> None:
    CACHE.mkdir(exist_ok=True)
    try:
        no_session = set(json.loads(NO_SESSION.read_text()))
    except (OSError, ValueError):
        no_session = set()
    today = ist_today()
    wanted = trading_days(EARLIEST, today)
    todo = [(d, ex) for d in wanted for ex in ("NSE", "BSE")
            if not (CACHE / f"{ex}-{d:%Y%m%d}{FMT}").exists() and f"{ex}-{d:%Y%m%d}" not in no_session]
    print(f"backfill: {len(wanted)} trading days wanted ({EARLIEST} to {today}), "
          f"{len(todo)} day-files still to fetch")
    if not todo:
        return

    deadline = time.monotonic() + budget
    stopped = 0
    session = requests.Session()

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
            return d, ex, False
        except Exception as e:  # noqa: BLE001 - one bad day (a format surprise decades back)
            print(f"  {ex} {d}: could not parse ({e}); skipping, will retry next run")
            return d, ex, False

    got = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, (d, ex, rows) in enumerate(pool.map(one, todo)):
            if rows:
                write_day(CACHE / f"{ex}-{d:%Y%m%d}{FMT}", rows)
                got += 1
            elif rows is None:
                no_session.add(f"{ex}-{d:%Y%m%d}")
            if (i + 1) % 200 == 0:
                print(f"  ...{i + 1}/{len(todo)} checked, {got} downloaded so far")

    NO_SESSION.write_text(json.dumps(sorted(no_session)))
    left = f", {stopped} left for the next run (time budget reached)" if stopped else " -- all caught up"
    print(f"backfill: {got} day-files downloaded this run{left}")


def load_days() -> list:
    """Every cached session in memory, oldest first. Same string-interning trick
    as update_charts.load_days() -- years of files for thousands of stocks is
    the one structure here big enough for it to matter."""
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


def write_output(days: list) -> int:
    """One file per actively-traded company, full history, NSE preferred over
    BSE when a company trades on both (active_universe() does that dedup)."""
    if not days:
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    by_isin, isin_first = isin_index(days)
    written = 0
    for (exch, key), meta in active_universe(days).items():
        idx = 0 if exch == "NSE" else 1
        isin = meta["isin"]
        raw = []
        for (day, *books), isins in zip(days, by_isin):
            r = (isins[idx].get(isin) if isin else None) or books[idx].get(key)
            if r:
                raw.append((day.isoformat(), r[3], r[4], r[5], r[6], r[7], r[8]))
        if len(raw) < 20:
            continue
        first = min(x for x in (raw[0][0], isin_first.get(isin or None), isin_first.get((exch, key))) if x)
        rows = _adjust(raw)   # split/bonus adjusted, same as the board's own charts
        payload = {"symbol": meta["symbol"], "exchange": exch, "isin": isin, "name": meta["name"],
                   "listed": first, "rows": rows}
        (OUT_DIR / f"{universe_key(exch, key)}.json").write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        written += 1
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="seconds to spend downloading this run")
    args = ap.parse_args()

    sync_cache(args.budget)
    days = load_days()
    if not days:
        print("backfill: nothing cached yet")
        return 1
    n = write_output(days)
    size_mb = sum(f.stat().st_size for f in OUT_DIR.glob("*.json")) / 1e6
    print(f"backfill: {n} companies written to {OUT_DIR} ({size_mb:.0f} MB), "
          f"from {days[0][0]} to {days[-1][0]} ({len(days)} cached sessions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
