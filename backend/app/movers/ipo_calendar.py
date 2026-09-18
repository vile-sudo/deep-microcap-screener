"""Currently-open mainboard and SME IPOs, from NSE's own live-issue feed.

Same Akamai-protected host (www.nseindia.com) as app/movers/deals.py's
historical_deals() -- needs a real page fetch first for the bot-check
cookies, plain requests get 403. index=sme on the query string is what
actually returns SME issues alongside mainboard; the plain call only ever
returned mainboard (checked: fetching both and comparing showed index=sme
is a superset, not a filter, despite the name).

There is no separate "upcoming, not yet open" feed worth using here: an
issue that will close within the next few days is -- necessarily -- already
open, so the live-issue feed alone is enough to drive a "report due N days
before close" schedule.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import requests

IPO_REPORT_PAGE = "https://www.nseindia.com/market-data/all-upcoming-issues-ipo"
IPO_URL = "https://www.nseindia.com/api/ipo-current-issue"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass(frozen=True, slots=True)
class OpenIssue:
    symbol: str
    company: str
    board: str          # "mainboard" or "sme"
    open_date: date
    close_date: date
    price_band: str
    status: str


def _parse_date(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def open_issues(timeout: int = 30) -> list[OpenIssue]:
    """Every mainboard/SME issue NSE currently shows as open for subscription."""
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get(IPO_REPORT_PAGE, timeout=timeout)   # Akamai cookies, same as a real page load
    r = session.get(IPO_URL, params={"index": "sme"},
                    headers={"Accept": "application/json, text/plain, */*", "Referer": IPO_REPORT_PAGE},
                    timeout=timeout)
    r.raise_for_status()
    out: list[OpenIssue] = []
    seen: set[str] = set()
    for row in r.json():
        symbol = str(row.get("symbol") or "").strip()
        open_dt, close_dt = _parse_date(row.get("issueStartDate")), _parse_date(row.get("issueEndDate"))
        if not symbol or symbol in seen or open_dt is None or close_dt is None:
            continue
        seen.add(symbol)
        out.append(OpenIssue(
            symbol=symbol,
            company=str(row.get("companyName") or "").strip(),
            board="sme" if str(row.get("series") or "").upper() == "SME" else "mainboard",
            open_date=open_dt,
            close_date=close_dt,
            price_band=str(row.get("issuePrice") or "").strip(),
            status=str(row.get("status") or "").strip(),
        ))
    return out
