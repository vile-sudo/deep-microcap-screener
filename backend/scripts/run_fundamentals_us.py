"""Five-year financial trend for the US board's companies, from SEC XBRL (free).

    cd backend
    python scripts/run_fundamentals_us.py

For each board company this reads SEC's "company facts" (data.sec.gov/api/xbrl/companyfacts,
every number the company has ever tagged in a filing) and keeps, per fiscal year from the
10-K: revenue, gross profit, operating income, net income, operating cash flow, long-term
debt, cash and shareholders' equity. Written to data/fundamentals_us/latest.json, served at
GET /api/fundamentals-us, shown in the scorecard's "Financial trend" section.

Companies tag the same idea with different names (Revenues, SalesRevenueNet,
RevenueFromContractWithCustomer...), so each metric tries a list in order of preference and
takes the first that has annual figures. A metric a company never reports stays null; nothing
is estimated. Flow items must cover a full year (350-380 days); balance-sheet items are the
value at the fiscal year end.

Each company is refreshed at most every 6 days (fiscal years change once a year), so the
nightly run is a few seconds unless a company was just added. Runs from us_analytics.yml.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.movers.insider_us import cik_map  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW_US = BACKEND_DIR / "data" / "companies_us_raw.json"
OUT_FILE = BACKEND_DIR / "data" / "fundamentals_us" / "latest.json"
HEADERS = {"User-Agent": "Deep Sweep research contact@pkresearch.in"}
DELAY = 0.2                     # SEC asks for under ~10 requests a second
REFRESH_DAYS = 6
YEARS = 6

FLOW = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax",
                "SalesRevenueNet", "SalesRevenueGoodsNet", "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
}
INSTANT = {
    "debt": ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermNotesPayable", "ConvertibleNotesPayable"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
}
ANNUAL_FORMS = {"10-K", "10-K/A", "10-KT", "20-F", "40-F"}


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def annual_series(facts: dict, tags: list[str], flow: bool) -> dict[str, float]:
    """{fiscal-year-end date: value} from the first tag that has annual figures. When a year was
    restated the most recently filed value wins."""
    usgaap = (facts.get("facts") or {}).get("us-gaap") or {}
    for tag in tags:
        units = (usgaap.get(tag) or {}).get("units") or {}
        rows = units.get("USD") or []
        best: dict[str, tuple[str, float]] = {}
        for r in rows:
            if r.get("form") not in ANNUAL_FORMS or r.get("val") is None or not r.get("end"):
                continue
            if flow:
                if not r.get("start") or not 350 <= (_d(r["end"]) - _d(r["start"])).days <= 380:
                    continue
            elif r.get("start"):        # an instant fact has no start
                continue
            cur = best.get(r["end"])
            if cur is None or r.get("filed", "") >= cur[0]:
                best[r["end"]] = (r.get("filed", ""), float(r["val"]))
        if best:
            return {end: v for end, (_, v) in best.items()}
    return {}


def build_entry(facts: dict) -> dict:
    series = {k: annual_series(facts, tags, True) for k, tags in FLOW.items()}
    series.update({k: annual_series(facts, tags, False) for k, tags in INSTANT.items()})
    # fiscal year ends are anchored on the income statement; the others are matched to the same date
    ends = sorted(series["revenue"] or series["net_income"] or {})[-YEARS:]
    years = []
    for end in ends:
        row = {"end": end, "fy": int(end[:4]) if int(end[5:7]) > 1 else int(end[:4]) - 1}
        for k, s in series.items():
            row[k] = s.get(end)
        years.append(row)
    return {"years": years, "entity": facts.get("entityName")}


def main() -> int:
    records = json.loads(RAW_US.read_text(encoding="utf-8")) if RAW_US.exists() else []
    codes = sorted({r["code"] for r in records if r.get("code")})
    if not codes:
        print("run_fundamentals_us: no US board companies yet; nothing to fetch")
        return 0
    try:
        prev = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("companies") or {}
    except (OSError, ValueError):
        prev = {}

    now = datetime.now(timezone.utc)
    stale = (now - timedelta(days=REFRESH_DAYS)).isoformat(timespec="seconds")
    todo = [c for c in codes if c not in prev or (prev[c].get("fetched_at") or "") < stale]
    out = {c: prev[c] for c in codes if c in prev}
    if not todo:
        print("run_fundamentals_us: every board company is fresh; nothing to do")
        if set(out) != set(prev):
            OUT_FILE.write_text(json.dumps({"fetched_at": now.isoformat(timespec="seconds"), "companies": out}, separators=(",", ":")), encoding="utf-8")
        return 0
    try:
        ciks = cik_map(todo)
    except requests.RequestException as e:
        print(f"run_fundamentals_us: could not fetch SEC's ticker->CIK map ({e}); keeping what we have")
        return 0

    session = requests.Session()
    ok = bad = 0
    for code in todo:
        cik = ciks.get(code.upper())
        if not cik:
            continue
        try:
            r = session.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", headers=HEADERS, timeout=60)
            r.raise_for_status()
            entry = build_entry(r.json())
            entry["fetched_at"] = now.isoformat(timespec="seconds")
            out[code] = entry
            ok += 1
        except (requests.RequestException, ValueError) as e:
            bad += 1
            print(f"  {code}: {e}")
        time.sleep(DELAY)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({"fetched_at": now.isoformat(timespec="seconds"), "companies": out}, separators=(",", ":")), encoding="utf-8")
    with_years = sum(1 for e in out.values() if e.get("years"))
    print(f"run_fundamentals_us: {ok} refreshed, {bad} failed; {with_years}/{len(codes)} board companies have annual figures -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
