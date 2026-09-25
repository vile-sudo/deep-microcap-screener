"""US earnings calendar and results for the US board's companies.

    cd backend
    python scripts/run_earnings_us.py

For every company on the US board (data/companies_us_raw.json) this pulls, from
Finnhub's free tier (the same FINNHUB_API_KEY the US auto-screen uses):

  * /stock/earnings   the last few reported quarters -- EPS actual vs the analyst
                      estimate, and the surprise;
  * /calendar/earnings  the company's own calendar between ~200 days ago and ~150
                      days ahead -- the next report date (and whether it is before
                      the open or after the close), the estimates, and, for past
                      quarters, the date it reported and revenue actual vs estimate.

and writes data/earnings_us/latest.json, which GET /api/earnings-us serves and the
US board's Earnings tab and company scorecard read. Runs nightly from
.github/workflows/board_disclosures_us.yml (and right after the US auto-screen
adds names), so a company added today has its dates today.

Free tier is 60 calls/min; two calls per company at DELAY seconds apart. A company
whose fetch fails keeps its previous entry rather than vanishing, and a run with
no FINNHUB_API_KEY changes nothing (it must not blank a good file).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW_US = BACKEND_DIR / "data" / "companies_us_raw.json"
OUT_FILE = BACKEND_DIR / "data" / "earnings_us" / "latest.json"

FINNHUB = "https://finnhub.io/api/v1"
DELAY = 1.1                    # 60 calls/min on the free tier -- stay under it
LOOK_BACK_DAYS = 200
LOOK_AHEAD_DAYS = 150
KEEP_RESULTS = 6               # quarters kept per company
MAX_CONSECUTIVE_ERRORS = 6     # Finnhub down or throttling: stop, don't grind


class FinnhubError(Exception):
    pass


def _get(session: requests.Session, key: str, path: str, **params):
    """One Finnhub GET. A 429 waits out the minute once; anything else non-200 raises."""
    params["token"] = key
    for attempt in (1, 2):
        r = session.get(FINNHUB + path, params=params, timeout=30)
        if r.status_code == 429 and attempt == 1:
            time.sleep(30)
            continue
        if r.status_code != 200:
            raise FinnhubError(f"{path} -> HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise FinnhubError(f"{path} -> bad JSON") from e
    raise FinnhubError(f"{path} -> throttled")


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def build_entry(name: str, history: list, calendar: list, today: date) -> dict:
    """Merge /stock/earnings and /calendar/earnings rows into one company entry."""
    reported = {}
    upcoming = []
    for c in calendar or []:
        if not isinstance(c, dict) or not c.get("date"):
            continue
        if _num(c.get("epsActual")) is None and c["date"] >= today.isoformat():
            upcoming.append(c)
        else:
            reported[(c.get("year"), c.get("quarter"))] = c

    results = []
    for h in sorted((x for x in (history or []) if isinstance(x, dict) and x.get("period")),
                    key=lambda x: x["period"], reverse=True)[:KEEP_RESULTS]:
        cal = reported.get((h.get("year"), h.get("quarter"))) or {}
        results.append({
            "period": h["period"], "quarter": h.get("quarter"), "year": h.get("year"),
            "reported": cal.get("date"), "hour": cal.get("hour") or None,
            "eps_actual": _num(h.get("actual")), "eps_estimate": _num(h.get("estimate")),
            "surprise": _num(h.get("surprise")), "surprise_pct": _num(h.get("surprisePercent")),
            "revenue_actual": _num(cal.get("revenueActual")), "revenue_estimate": _num(cal.get("revenueEstimate")),
        })

    if not results:    # no /stock/earnings history: fall back to what the calendar itself reported
        for (year, quarter), c in sorted(reported.items(), key=lambda kv: kv[1]["date"], reverse=True)[:KEEP_RESULTS]:
            if _num(c.get("epsActual")) is None:
                continue
            act, est = _num(c.get("epsActual")), _num(c.get("epsEstimate"))
            results.append({
                "period": None, "quarter": quarter, "year": year, "reported": c["date"], "hour": c.get("hour") or None,
                "eps_actual": act, "eps_estimate": est,
                "surprise": round(act - est, 4) if est is not None else None,
                "surprise_pct": round((act - est) / abs(est) * 100, 2) if est else None,
                "revenue_actual": _num(c.get("revenueActual")), "revenue_estimate": _num(c.get("revenueEstimate")),
            })

    nxt = None
    if upcoming:
        u = min(upcoming, key=lambda c: c["date"])
        nxt = {"date": u["date"], "hour": u.get("hour") or None, "quarter": u.get("quarter"), "year": u.get("year"),
               "eps_estimate": _num(u.get("epsEstimate")), "revenue_estimate": _num(u.get("revenueEstimate"))}
    return {"name": name, "next": nxt, "results": results}


def fetch_company(session, key, code: str, name: str, today: date) -> dict:
    hist = _get(session, key, "/stock/earnings", symbol=code, limit=8)
    time.sleep(DELAY)
    cal = _get(session, key, "/calendar/earnings", symbol=code,
               **{"from": (today - timedelta(days=LOOK_BACK_DAYS)).isoformat(),
                  "to": (today + timedelta(days=LOOK_AHEAD_DAYS)).isoformat()})
    cal_rows = cal.get("earningsCalendar") if isinstance(cal, dict) else None
    return build_entry(name, hist if isinstance(hist, list) else [], cal_rows or [], today)


def main() -> int:
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key:
        print("run_earnings_us: FINNHUB_API_KEY not set; leaving the earnings file as it is")
        return 0
    records = json.loads(RAW_US.read_text(encoding="utf-8")) if RAW_US.exists() else []
    board = {r["code"]: r.get("name") or r["code"] for r in records if r.get("code")}
    if not board:
        print("run_earnings_us: no US board companies yet; nothing to fetch")
        return 0

    try:
        previous = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("companies") or {}
    except (OSError, ValueError):
        previous = {}

    today = datetime.now(timezone.utc).date()
    session = requests.Session()
    companies, errors, consecutive = {}, 0, 0
    for code, name in sorted(board.items()):
        try:
            companies[code] = fetch_company(session, key, code, name, today)
            consecutive = 0
        except (requests.RequestException, FinnhubError) as e:
            errors += 1
            consecutive += 1
            print(f"  {code}: {e}" + (" -- keeping its previous entry" if code in previous else ""))
            if code in previous:
                companies[code] = previous[code]
            if consecutive >= MAX_CONSECUTIVE_ERRORS:
                print("run_earnings_us: Finnhub keeps failing; stopping and keeping the previous entries for the rest")
                for c in board:
                    if c not in companies and c in previous:
                        companies[c] = previous[c]
                break
        time.sleep(DELAY)

    fresh = sum(1 for c in companies.values() if c.get("next") or c.get("results"))
    if not fresh and previous:
        print("run_earnings_us: nothing came back at all; keeping the existing file")
        return 0

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "asof": today.isoformat(),
        "companies_checked": len(board),
        "companies_with_data": fresh,
        "companies": companies,
    }, separators=(",", ":")), encoding="utf-8")
    upcoming = sum(1 for c in companies.values() if c.get("next"))
    print(f"run_earnings_us: {fresh}/{len(board)} board companies with earnings data, {upcoming} with a date ahead, "
          f"{errors} errors -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
