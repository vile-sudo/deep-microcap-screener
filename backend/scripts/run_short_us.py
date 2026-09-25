"""Short interest for the US board's companies, from FINRA's free bi-monthly files.

    cd backend
    python scripts/run_short_us.py

FINRA publishes the short position of every US-listed stock twice a month (settlement on the
15th and the last business day), as one pipe-delimited file per date at
cdn.finra.org/equity/otcmarket/biweekly/shrtYYYYMMDD.csv, about a week after the settlement
date. This finds the newest file that exists, and keeps for each board company: shares sold
short, the previous period's figure, the change, average daily volume, days to cover, and the
short position as a share of shares outstanding (market cap / price from the board record).
Written to data/short_us/latest.json (GET /api/short-us). No key needed.

Skips the download when the newest settlement date is already on file and every board company
has been looked up. Runs from us_analytics.yml.
"""
from __future__ import annotations

import csv
import io
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW_US = BACKEND_DIR / "data" / "companies_us_raw.json"
OUT_FILE = BACKEND_DIR / "data" / "short_us" / "latest.json"
BASE = "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{d}.csv"
LOOKBACK_DAYS = 45


def _num(v, cast=float):
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


def newest_file(session: requests.Session, today: date):
    """(settlement date, csv text) for the newest published file, or (None, None)."""
    for back in range(0, LOOKBACK_DAYS):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:                 # settlement dates are business days
            continue
        r = session.get(BASE.format(d=d.strftime("%Y%m%d")), timeout=60)
        if r.status_code == 200 and "symbolCode" in r.text[:400]:
            return d.isoformat(), r.text
    return None, None


def main() -> int:
    records = json.loads(RAW_US.read_text(encoding="utf-8")) if RAW_US.exists() else []
    board = {}
    for r in records:
        if r.get("code"):
            cap, px = r.get("market_cap_usd"), r.get("price")
            board[r["code"].upper()] = (cap * 1e6 / px) if cap and px else None
    if not board:
        return 0
    try:
        prev = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}

    session = requests.Session()
    today = datetime.now(timezone.utc).date()
    try:
        settle, text = newest_file(session, today)
    except requests.RequestException as e:
        print(f"run_short_us: could not reach FINRA ({e}); keeping what we have")
        return 0
    if not settle:
        print("run_short_us: no FINRA short-interest file found in the last 45 days; keeping what we have")
        return 0
    if prev.get("settlement_date") == settle and set(board) <= set(prev.get("attempted") or []):
        print(f"run_short_us: {settle} already on file; nothing to do")
        return 0

    out = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        sym = (row.get("symbolCode") or "").upper()
        if sym not in board:
            continue
        cur, old = _num(row.get("currentShortPositionQuantity"), int), _num(row.get("previousShortPositionQuantity"), int)
        so = board[sym]
        out[sym] = {
            "short_shares": cur, "prev_short_shares": old, "change_pct": _num(row.get("changePercent")),
            "avg_daily_volume": _num(row.get("averageDailyVolumeQuantity"), int),
            "days_to_cover": _num(row.get("daysToCoverQuantity")),
            "short_pct_shares": round(cur / so * 100, 2) if cur is not None and so else None,
        }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settlement_date": settle, "attempted": sorted(board), "companies": out,
    }, separators=(",", ":")), encoding="utf-8")
    print(f"run_short_us: settlement {settle}: {len(out)}/{len(board)} board companies found in FINRA's file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
