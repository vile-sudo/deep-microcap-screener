"""How the US board's companies have done since they were added, and how their score has moved.

    cd backend
    python scripts/run_performance_us.py

The point: without this there is no way to tell whether the gates pick anything worth having.
Every run, for each company on the board, it records today's price and score in
data/performance_us/latest.json (GET /api/performance-us), and works out the return since the
day the company was added.

The price the return starts from:
  * the close on (or just after) the added date, when the chart job has candles for the company
    (chart_data_us/prices/<CODE>.json) -- source "chart";
  * otherwise the price on the first day this job saw the company -- source "first-seen", and
    the return then counts from that day, which the scorecard says.
Today's price is Finnhub's /quote when FINNHUB_API_KEY is set, else the chart job's last close,
else the board record's own price. The daily score history feeds the score sparkline.

History per company is capped at 400 daily points. A company taken off the board drops out.
Runs from us_analytics.yml.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW_US = BACKEND_DIR / "data" / "companies_us_raw.json"
OUT_FILE = BACKEND_DIR / "data" / "performance_us" / "latest.json"
CHART_INDEX = BACKEND_DIR / "chart_data_us" / "index.json"
PRICES_DIR = BACKEND_DIR / "chart_data_us" / "prices"
FINNHUB = "https://finnhub.io/api/v1"
DELAY = 1.1
KEEP_POINTS = 400


def close_on_or_after(code: str, added_on: str):
    try:
        rows = json.loads((PRICES_DIR / f"{code}.json").read_text(encoding="utf-8")).get("rows") or []
    except (OSError, ValueError):
        return None
    for r in rows:                                  # rows are oldest first: [date, o, h, l, close, volume]
        if r[0] >= added_on and r[4]:
            return float(r[4])
    return None


def quote_price(session, key: str, code: str):
    try:
        r = session.get(f"{FINNHUB}/quote", params={"symbol": code, "token": key}, timeout=30)
        if r.status_code == 429:
            time.sleep(30)
            r = session.get(f"{FINNHUB}/quote", params={"symbol": code, "token": key}, timeout=30)
        r.raise_for_status()
        c = r.json().get("c")
        return float(c) if c else None
    except (requests.RequestException, ValueError, TypeError):
        return None


def main() -> int:
    records = json.loads(RAW_US.read_text(encoding="utf-8")) if RAW_US.exists() else []
    board = {r["code"]: r for r in records if r.get("code")}
    if not board:
        return 0
    try:
        prev = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("companies") or {}
    except (OSError, ValueError):
        prev = {}
    try:
        chart = (json.loads(CHART_INDEX.read_text(encoding="utf-8")).get("companies")) or {}
    except (OSError, ValueError):
        chart = {}

    key = os.environ.get("FINNHUB_API_KEY", "")
    session = requests.Session()
    today = datetime.now(timezone.utc).date().isoformat()
    out = {}
    for code, rec in sorted(board.items()):
        price = quote_price(session, key, code) if key else None
        if key:
            time.sleep(DELAY)
        price = price or (chart.get(code) or {}).get("last") or rec.get("price")
        if not price:
            if code in prev:
                out[code] = prev[code]
            continue
        score = rec.get("final_score")
        e = prev.get(code) or {}
        added_on = rec.get("added_on") or today
        if not e:
            start = close_on_or_after(code, added_on)
            e = {"added_on": added_on, "added_price": start or price, "added_source": "chart" if start else "first-seen",
                 "tracking_from": added_on if start else today, "added_score": score, "history": []}
        hist = [h for h in e.get("history", []) if h[0] != today]
        hist.append([today, round(float(price), 4), score])
        e["history"] = hist[-KEEP_POINTS:]
        e["price"], e["score"] = round(float(price), 4), score
        e["since_added_pct"] = round((price / e["added_price"] - 1) * 100, 2) if e.get("added_price") else None
        out[code] = e

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "asof": today, "companies": out},
                                   separators=(",", ":")), encoding="utf-8")
    moved = [e["since_added_pct"] for e in out.values() if e.get("since_added_pct") is not None]
    print(f"run_performance_us: {len(out)}/{len(board)} board companies tracked"
          + (f"; average since added {sum(moved) / len(moved):+.1f}%" if moved else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
