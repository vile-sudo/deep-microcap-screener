"""Insider trading & SAST disclosures -- promoters, directors, KMPs and other insiders' own buy/sell/
pledge transactions in their own company's stock, disclosed under SEBI's Prohibition of Insider Trading
(PIT) Regulations, 2015. The board's own bulk/block deals (deals.py) name whoever traded, institution or
not; this is the narrower but often more telling signal -- the company's own people, not a fund.

NSE's page for this (companies-listing/corporate-filings-insider-trading) is backed by two calls, found
by watching the page's own network requests (not documented anywhere): a summary list,
api/corporates-pit-gg, that gives only WHO filed and a link to their disclosure -- no transaction detail
at all -- and one XBRL XML file per disclosure (linked as `xmlFileName`) that has the real numbers:
person, category (Promoter/Director/KMP/...), transaction type (Buy/Sell/Pledge/Pledge Invoke/...),
quantity, value, shareholding before and after. So one disclosure costs one extra HTTP fetch beyond the
summary list -- fine for the handful of NEW disclosures a run actually needs (dedup by appId), not fine
if ever done for the whole list every time.

Same Akamai bot-protection host as deals.py's historical_deals() (www.nseindia.com, not the plain
nsearchives.nseindia.com archive) -- needs a real page load first for cookies, same header set.

Verified against real filings before writing this (the field names below, e.g.
SecuritiesAcquiredOrDisposedTransactionType, SecuritiesHeldPriorToAcquisitionOrDisposalNumberOfSecurity,
are exactly what NSE serves, sampled from live disclosures, not guessed from any spec)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from xml.etree import ElementTree as ET

import requests

from .bhavcopy import BROWSER_HEADERS, NSE_HOME

PIT_PAGE = f"{NSE_HOME}/companies-listing/corporate-filings-insider-trading"
PIT_LIST_URL = f"{NSE_HOME}/api/corporates-pit-gg"

# A disclosure can be revised (typeOfSubmission="Revision", prevAppId pointing at the original) -- kept
# as its own row like NSE itself does, not merged into the original, since a reader should be able to see
# a revision happened rather than have it silently overwrite history.


@dataclass(frozen=True, slots=True)
class InsiderTrade:
    app_id: str
    symbol: str
    company_name: str
    person: str
    category: str            # Promoter / Promoter Group / Director / KMP / Designated Person / ...
    transaction_type: str    # Buy / Sell / Pledge / Pledge Invoke / Pledge Revoke / ...
    quantity: float
    value: float
    mode: str                 # Market Sale, Market Purchase, Off Market, ...
    pre_qty: float | None
    pre_pct: float | None
    post_qty: float | None
    post_pct: float | None
    trade_from: date | None
    trade_to: date | None
    intimation_date: date | None
    broadcast_at: str         # "28-Sep-2026 12:23:20", NSE's own filing timestamp
    regulation: str

    @property
    def value_cr(self) -> float:
        return self.value / 10_000_000

    @property
    def side(self) -> str:
        t = (self.transaction_type or "").strip().lower()
        if t == "buy":
            return "BUY"
        if t == "sell":
            return "SELL"
        return "OTHER"          # pledge / pledge invoke / pledge revoke / anything else -- a real
                                 # signal, just not a market buy/sell one

    @property
    def date(self) -> str:
        """The date this row groups under: the trade date when NSE gave one, else the filing date --
        same fallback shape run_deal_clusters.py's own date handling uses elsewhere in this codebase."""
        d = self.trade_to or self.trade_from
        if d:
            return d.isoformat()
        try:
            return datetime.strptime(self.broadcast_at[:11], "%d-%b-%Y").date().isoformat()
        except ValueError:
            return ""


def open_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    s.get(NSE_HOME, timeout=30)
    s.get(PIT_PAGE, timeout=30)      # Akamai cookies for this specific report page
    return s


def list_recent(session: requests.Session, timeout: int = 30) -> list[dict]:
    """Every disclosure NSE's summary feed currently holds (in practice a rolling ~2 months, ~3,000
    rows) -- metadata only (symbol, company, filing time, the XML link), no transaction detail. One
    request regardless of how many rows come back."""
    r = session.get(PIT_LIST_URL, params={"index": "equities"},
                    headers={"Accept": "application/json, text/plain, */*", "Referer": PIT_PAGE}, timeout=timeout)
    r.raise_for_status()
    return r.json().get("data") or []


_NS = "{http://www.bseindia.com/xbrl/co/2017-09-15/in-bse-co}"


def _tag(root: ET.Element, name: str) -> str | None:
    el = root.find(f".//{_NS}{name}")
    return el.text.strip() if el is not None and el.text else None


def _num(root: ET.Element, name: str) -> float | None:
    v = _tag(root, name)
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def _date(root: ET.Element, name: str) -> date | None:
    v = _tag(root, name)
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return None


def fetch_detail(session: requests.Session, meta: dict, timeout: int = 20) -> InsiderTrade | None:
    """One disclosure's transaction detail, from its XBRL XML (meta['xmlFileName']). None for a
    disclosure this parser cannot make sense of (a shape NSE hasn't been seen to use, or a fetch that
    failed) -- skipped rather than guessed at, same as deals.py/announcements.py's own parsers do for a
    row they cannot cleanly read."""
    url = meta.get("xmlFileName")
    if not url:
        return None
    try:
        r = session.get(url, timeout=timeout)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError):
        return None

    person = _tag(root, "NameOfThePerson")
    ttype = _tag(root, "SecuritiesAcquiredOrDisposedTransactionType")
    qty = _num(root, "SecuritiesAcquiredOrDisposedNumberOfSecurity")
    if not person or not ttype or qty is None:
        return None   # a shape this parser does not recognise (a different regulation/instrument type)

    return InsiderTrade(
        app_id=str(meta.get("appId") or ""),
        symbol=str(meta.get("symbol") or _tag(root, "Symbol") or "").strip(),
        company_name=str(meta.get("companyName") or _tag(root, "NameOfTheCompany") or "").strip(),
        person=person,
        category=_tag(root, "CategoryOfPerson") or "Other",
        transaction_type=ttype,
        quantity=qty,
        value=_num(root, "SecuritiesAcquiredOrDisposedValueOfSecurity") or 0.0,
        mode=_tag(root, "ModeOfAcquisitionOrDisposal") or "",
        pre_qty=_num(root, "SecuritiesHeldPriorToAcquisitionOrDisposalNumberOfSecurity"),
        pre_pct=_num(root, "SecuritiesHeldPriorToAcquisitionOrDisposalPercentageOfShareholding"),
        post_qty=_num(root, "SecuritiesHeldPostAcquistionOrDisposalNumberOfSecurity"),
        post_pct=_num(root, "SecuritiesHeldPostAcquistionOrDisposalPercentageOfShareholding"),
        trade_from=_date(root, "DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyFromDate"),
        trade_to=_date(root, "DateOfAllotmentAdviceOrAcquisitionOfSharesOrSaleOfSharesSpecifyToDate"),
        intimation_date=_date(root, "DateOfIntimationToCompany"),
        broadcast_at=str(meta.get("broadcastDateTime") or ""),
        regulation=str(meta.get("regulation") or ""),
    )
