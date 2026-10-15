"""Quarterly results for every actively traded NSE/BSE company -- the training data for Results Preview.

The board's own results (scripts/run_results_in.py) cover ~490 companies; Results Preview learns from the
whole market instead (~4,300 companies in Screen any Chart's universe, chart_data/universe.json), which gives
it about ten times the company-quarters to learn from. Same screener.in pages and parser as run_results_in.py;
only the quarterly table is kept.

    cd backend
    python scripts/run_results_universe.py                 # nightly: companies that just filed results,
                                                           #   plus the oldest entries up to --budget
    python scripts/run_results_universe.py --budget 5000   # the first full pass (~3 hours, resumable)

Writes data/results_universe/latest.json.gz: {"companies": {key: {"quarters": {...}, "at": iso, "exchange",
"symbol"}}} with key "NSE:SYMBOL" or "BSE:CODE". Paced like run_results_in.py; stops if screener refuses.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_results_in as R  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
UNIVERSE = BACKEND_DIR / "chart_data" / "universe.json"
OUT_FILE = BACKEND_DIR / "data" / "results_universe" / "latest.json.gz"
KEEP = ("periods", "sales", "operating_profit", "opm", "other_income", "interest", "depreciation", "pbt", "net_profit")


def load() -> dict:
    try:
        return json.loads(gzip.decompress(OUT_FILE.read_bytes()).decode("utf-8")).get("companies") or {}
    except (OSError, ValueError, EOFError):
        return {}


def save(out: dict) -> None:
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "companies": out},
                      separators=(",", ":")).encode("utf-8")
    tmp = OUT_FILE.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(blob, mtime=0))
    tmp.replace(OUT_FILE)


def just_reported(days: int = 4) -> set[str]:
    """NSE symbols with a results filing in the last few days (the Deals history), so a fresh quarter lands fast."""
    try:
        from app import disclosures_archive as A
        p = A.archive_path()
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        since = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
        s = {r[0] for r in con.execute("SELECT DISTINCT symbol FROM ann WHERE kind='results' AND date >= ?", (since,))}
        con.close()
        return s
    except Exception:  # noqa: BLE001
        return set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=400, help="companies to (re)fetch this run")
    a = ap.parse_args()
    uni = json.loads(UNIVERSE.read_text(encoding="utf-8")).get("stocks") or {}
    out = load()
    fresh = just_reported()
    todo = []
    for key, s in uni.items():
        if s.get("on_board") and not (s.get("exchange") == "NSE" and str(s.get("symbol") or "") in fresh):
            continue                     # the board's own results come from run_results_in.py -- except one that just
                                         # reported, which the overnight read (Results Preview) needs tonight
        ex, sym = s.get("exchange"), str(s.get("symbol") or "")
        bse = str(s.get("code") or key.split("-", 1)[1]) if ex == "BSE" else None   # the BSE code lives in the key
        k = f"{ex}:{sym if ex == 'NSE' else bse}"
        rec = {"nse_code": sym if ex == "NSE" else None, "bse_code": bse}
        # just reported first, then never fetched (NSE before BSE: NSE has the result dates), then the oldest
        prio = 0 if (ex == "NSE" and sym in fresh) else (1 if ex == "NSE" else 2) if k not in out else 3
        todo.append((prio, (out.get(k) or {}).get("at") or "", k, rec, ex, sym))
    # a company that just declared results but isn't actively traded enough to be in the universe list (SME,
    # thinly traded): fetch it too, so the overnight read covers every result declared
    listed = {(s.get("exchange"), str(s.get("symbol") or "")) for s in uni.values()}
    for sym in sorted(fresh):
        if ("NSE", sym) not in listed:
            todo.append((0, (out.get(f"NSE:{sym}") or {}).get("at") or "", f"NSE:{sym}", {"nse_code": sym, "bse_code": None}, "NSE", sym))
    todo.sort(key=lambda x: (x[0], x[1], x[2]))
    todo = todo[:a.budget]
    session = requests.Session()
    session.headers.update(R.HEADERS)
    ok = miss = blocks = 0
    for i, (_, _, k, rec, ex, sym) in enumerate(todo, 1):
        try:
            entry, blocked = R.fetch(rec, session)
        except requests.RequestException:
            entry, blocked = None, True      # a refused / timed-out connection is the site pushing back too
        if blocked:
            blocks += 1
            time.sleep(20)
            if blocks >= R.MAX_CONSECUTIVE_BLOCKS:
                print("run_results_universe: screener.in is refusing requests; stopping")
                break
            continue
        blocks = 0
        if entry:
            q = entry["quarters"]
            out[k] = {"quarters": {x: q.get(x) for x in KEEP if q.get(x) is not None}, "basis": entry.get("basis"),
                      "exchange": ex, "symbol": sym, "name": entry.get("name"),
                      "at": datetime.now(timezone.utc).date().isoformat()}
            ok += 1
        else:
            miss += 1
        if i % 100 == 0:
            save(out)
            print(f"  ...{i}/{len(todo)} ({ok} ok, {miss} without a results table)", flush=True)
        time.sleep(R.DELAY)
    save(out)
    print(f"run_results_universe: {ok} fetched, {miss} without results, {len(out)} companies on file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
