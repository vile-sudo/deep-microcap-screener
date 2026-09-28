"""FII/FPI & DII daily cash-market trading activity -- NSE's own combined report (covering NSE, BSE
and MSEI together), one of the most-watched macro numbers in Indian markets: is the market being driven
by foreign selling or domestic buying that day.

Found by watching nseindia.com/reports/fii-dii's own network requests (undocumented, same technique
already used for insider trading and deals): api/fiidiiTradeReact is the combined cross-exchange figure
this page's own title promises (api/fiidiiTradeNse, also seen, is an NSE-only subset -- not used here).
Same Akamai bot-protection host as deals.py/insider_trading.py -- needs a real page load first for
cookies, same header set.

Only ever returns the latest session's two rows (one for FII/FPI, one for DII) -- like bulk.csv/block.csv,
not a real date-range archive -- so scripts/run_fii_dii.py accumulates its own rolling history across
runs, same "merge in, dedupe by date, prune the tail" shape as run_deals.py.

Verified against a live fetch before writing this (the exact field names below -- buyValue, sellValue,
netValue, category, date -- are what NSE actually serves, not a guess)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import requests

from .bhavcopy import BROWSER_HEADERS, NSE_HOME

FIIDII_PAGE = f"{NSE_HOME}/reports/fii-dii"
FIIDII_URL = f"{NSE_HOME}/api/fiidiiTradeReact"


@dataclass(frozen=True, slots=True)
class FlowRow:
    trade_date: date
    fii_buy_cr: float
    fii_sell_cr: float
    fii_net_cr: float
    dii_buy_cr: float
    dii_sell_cr: float
    dii_net_cr: float


def open_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    s.get(NSE_HOME, timeout=30)
    s.get(FIIDII_PAGE, timeout=30)   # Akamai cookies for this specific report page
    return s


def _num(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def fetch_latest(session: requests.Session, timeout: int = 20) -> FlowRow | None:
    """The most recent session's combined FII/FPI + DII cash-market activity, or None if NSE's own
    response could not be parsed (a shape this parser does not recognise -- skipped, not guessed at,
    same as deals.py/insider_trading.py's own parsers do for a row they cannot cleanly read)."""
    r = session.get(FIIDII_URL, headers={"Accept": "application/json, text/plain, */*", "Referer": FIIDII_PAGE}, timeout=timeout)
    r.raise_for_status()
    rows = {row.get("category"): row for row in (r.json() or []) if isinstance(row, dict)}
    fii, dii = rows.get("FII/FPI"), rows.get("DII")
    if not fii or not dii:
        return None
    try:
        trade_date = datetime.strptime(fii.get("date") or dii.get("date"), "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None
    return FlowRow(
        trade_date=trade_date,
        fii_buy_cr=_num(fii.get("buyValue")), fii_sell_cr=_num(fii.get("sellValue")), fii_net_cr=_num(fii.get("netValue")),
        dii_buy_cr=_num(dii.get("buyValue")), dii_sell_cr=_num(dii.get("sellValue")), dii_net_cr=_num(dii.get("netValue")),
    )
