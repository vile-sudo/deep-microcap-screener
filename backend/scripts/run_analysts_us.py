"""Analyst coverage and ratings for the US board's companies (Finnhub free tier).

    cd backend
    python scripts/run_analysts_us.py

/stock/recommendation gives, per month, how many analysts rate a stock strong buy / buy /
hold / sell / strong sell. Written to data/analysts_us/latest.json (GET /api/analysts-us) with
the latest three months per company. A company nobody covers comes back empty: that is kept
as "covered: false" because a stock with no analysts is itself information for a board that
looks for under-covered names. (Price targets are a paid Finnhub endpoint and are not used.)

Needs FINNHUB_API_KEY; without it the file is left alone. Runs from us_analytics.yml.
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
OUT_FILE = BACKEND_DIR / "data" / "analysts_us" / "latest.json"
FINNHUB = "https://finnhub.io/api/v1"
DELAY = 1.1
MAX_CONSECUTIVE_ERRORS = 6


def entry_from(rows: list) -> dict:
    rows = [r for r in rows if isinstance(r, dict) and r.get("period")]
    rows.sort(key=lambda r: r["period"], reverse=True)
    months = [{"period": r["period"], "strong_buy": r.get("strongBuy", 0), "buy": r.get("buy", 0), "hold": r.get("hold", 0),
               "sell": r.get("sell", 0), "strong_sell": r.get("strongSell", 0)} for r in rows[:3]]
    if not months:
        return {"covered": False, "months": []}
    m = months[0]
    n = sum(m[k] for k in ("strong_buy", "buy", "hold", "sell", "strong_sell"))
    return {"covered": n > 0, "analysts": n, "months": months}


def main() -> int:
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key:
        print("run_analysts_us: FINNHUB_API_KEY not set; leaving the analysts file as it is")
        return 0
    records = json.loads(RAW_US.read_text(encoding="utf-8")) if RAW_US.exists() else []
    codes = sorted({r["code"] for r in records if r.get("code")})
    if not codes:
        return 0
    try:
        prev = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("companies") or {}
    except (OSError, ValueError):
        prev = {}

    session = requests.Session()
    out, errors, consecutive = {}, 0, 0
    for code in codes:
        try:
            r = session.get(f"{FINNHUB}/stock/recommendation", params={"symbol": code, "token": key}, timeout=30)
            if r.status_code == 429:
                time.sleep(30)
                r = session.get(f"{FINNHUB}/stock/recommendation", params={"symbol": code, "token": key}, timeout=30)
            r.raise_for_status()
            out[code] = entry_from(r.json() if isinstance(r.json(), list) else [])
            consecutive = 0
        except (requests.RequestException, ValueError) as e:
            errors += 1
            consecutive += 1
            print(f"  {code}: {e}")
            if code in prev:
                out[code] = prev[code]
            if consecutive >= MAX_CONSECUTIVE_ERRORS:
                print("run_analysts_us: Finnhub keeps failing; stopping")
                out.update({c: prev[c] for c in codes if c not in out and c in prev})
                break
        time.sleep(DELAY)
    if not out:
        print("run_analysts_us: nothing came back; keeping the existing file")
        return 0
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "companies": out},
                                   separators=(",", ":")), encoding="utf-8")
    print(f"run_analysts_us: {sum(1 for e in out.values() if e.get('covered'))}/{len(codes)} board companies have analyst coverage, {errors} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
