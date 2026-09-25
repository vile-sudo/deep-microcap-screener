"""US IPO calendar (Finnhub free tier).

    cd backend
    python scripts/run_ipo_us.py

/calendar/ipo lists IPOs that are expected, priced, filed or withdrawn: date, exchange,
company, ticker, price range, number of shares and deal size. This keeps the last 30 days and
the next 90 in data/ipo_us/latest.json (GET /api/ipo-us) for the US board's IPO tab, with a
link to the company's SEC filings so the prospectus (S-1 / 424B) is one click away.

The IPOs are market-wide, not board companies -- an IPO is by definition not on the board yet.
Needs FINNHUB_API_KEY; without it, or if Finnhub returns nothing, the file is left alone.
Runs from us_analytics.yml.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
OUT_FILE = BACKEND_DIR / "data" / "ipo_us" / "latest.json"
FINNHUB = "https://finnhub.io/api/v1"
BACK_DAYS, AHEAD_DAYS = 30, 90


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_price(s):
    """'15.00-17.00' -> (15.0, 17.0); '16' -> (16.0, 16.0); anything else -> (None, None)."""
    try:
        parts = [float(x) for x in str(s).replace("$", "").split("-") if x.strip()]
    except ValueError:
        return None, None
    return (parts[0], parts[-1]) if parts else (None, None)


def main() -> int:
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key:
        print("run_ipo_us: FINNHUB_API_KEY not set; leaving the IPO file as it is")
        return 0
    today = datetime.now(timezone.utc).date()
    try:
        r = requests.get(f"{FINNHUB}/calendar/ipo", params={
            "from": (today - timedelta(days=BACK_DAYS)).isoformat(), "to": (today + timedelta(days=AHEAD_DAYS)).isoformat(), "token": key}, timeout=60)
        r.raise_for_status()
        rows = (r.json() or {}).get("ipoCalendar") or []
    except (requests.RequestException, ValueError) as e:
        print(f"run_ipo_us: Finnhub failed ({e}); keeping the existing file")
        return 0
    if not rows and OUT_FILE.exists():
        print("run_ipo_us: nothing came back; keeping the existing file")
        return 0

    items = []
    for x in rows:
        if not isinstance(x, dict) or not x.get("name") or not x.get("date"):
            continue
        lo, hi = parse_price(x.get("price"))
        items.append({
            "date": x["date"], "name": x["name"], "symbol": x.get("symbol") or "", "exchange": x.get("exchange") or "",
            "status": (x.get("status") or "").lower(), "price_low": lo, "price_high": hi,
            "shares": _num(x.get("numberOfShares")), "deal_value": _num(x.get("totalSharesValue")),
            "sec_url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=" + quote(x["name"]) + "&type=&dateb=&owner=include&count=40",
        })
    items.sort(key=lambda i: i["date"])
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "count": len(items), "items": items}, separators=(",", ":")), encoding="utf-8")
    ahead = sum(1 for i in items if i["date"] >= today.isoformat())
    print(f"run_ipo_us: {len(items)} IPOs on the calendar ({ahead} today or later)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
