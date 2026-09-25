"""US insider transactions (SEC Form 4) -- the US board's counterpart to
India's Bulk & Block Deals (app/movers/deals.py). NSE's bulk/block deal
disclosure is a SEBI rule: any single trade over a size threshold must be
published with the counterparty's name. US market structure has no
equivalent -- brokers and exchanges never publicly name who did a trade.

The closest thing that is genuinely public, free and automatable is SEC
Form 4: officers, directors and 10%+ owners must disclose their own buy/
sell transactions within two business days. Not the same signal (insider
activity, not "any large trade"), but the same shape as a Deal record: a
named party, a date, shares, a price, bought or sold -- see
scripts/run_insider_us.py for the pipeline that writes this to a file.

Verified against a real filing before writing this (AAPL's most recent
Form 4, CIK 0000320193) -- the schema below (issuer/reportingOwner/
nonDerivativeTable, no XML namespace) is what SEC actually serves, not a
guess from the EDGAR docs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

import requests

SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
HEADERS = {"User-Agent": "Deep Sweep research contact@pkresearch.in"}
TICKERS_URL = f"{SEC_WWW}/files/company_tickers.json"
DELAY = 0.15   # SEC's fair-use rate (same as scripts/us_auto_screen.py)

# Every non-derivative transaction code Form 4 uses. P/S are genuine open-
# market buy/sell -- the closest thing to NSE's bulk/block deal signal.
# Everything else (grants, option exercises, tax withholding, gifts, ...)
# is real insider activity too, kept and labelled, just not the same
# "someone bought/sold a lot in the market" signal.
CODE_LABELS = {
    "P": "Purchase", "S": "Sale", "A": "Award/grant", "M": "Option exercise",
    "F": "Tax withholding", "G": "Gift", "C": "Conversion", "X": "Option exercise (in the money)",
    "D": "Disposition to issuer", "I": "Discretionary transaction", "J": "Other",
}
MARKET_CODES = {"P", "S"}


@dataclass(frozen=True, slots=True)
class InsiderTxn:
    symbol: str
    insider: str
    relationship: str
    code: str
    acquired: bool
    shares: float
    price: float | None
    trade_date: date
    filed_date: date
    accession: str

    @property
    def value_usd(self) -> float:
        return self.shares * (self.price or 0)

    @property
    def side(self) -> str:
        return "BUY" if self.acquired else "SELL"

    @property
    def label(self) -> str:
        return CODE_LABELS.get(self.code, self.code)

    @property
    def is_market(self) -> bool:
        return self.code in MARKET_CODES


def cik_map(tickers: list[str]) -> dict[str, str]:
    """ticker -> zero-padded CIK, for just the tickers asked for. SEC's own
    free map has no per-ticker lookup endpoint, so this fetches the whole
    file once and filters -- the same approach scripts/us_auto_screen.py's
    universe() uses against the same file."""
    r = requests.get(TICKERS_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    wanted = {t.upper() for t in tickers}
    out: dict[str, str] = {}
    for row in r.json().values():
        t = str(row.get("ticker", "")).upper()
        if t in wanted:
            out[t] = str(row["cik_str"]).zfill(10)
    return out


def _relationship(owner_el: ET.Element) -> str:
    rel = owner_el.find("reportingOwnerRelationship")
    if rel is None:
        return ""

    def flag(tag: str) -> bool:
        return (rel.findtext(tag) or "").strip().lower() == "true"

    parts = []
    if flag("isDirector"):
        parts.append("Director")
    if flag("isOfficer"):
        parts.append((rel.findtext("officerTitle") or "").strip() or "Officer")
    if flag("isTenPercentOwner"):
        parts.append("10%+ owner")
    if flag("isOther"):
        parts.append((rel.findtext("otherText") or "").strip() or "Other")
    return ", ".join(parts) or "Reporting person"


def _parse_form4(xml_text: str, symbol: str, accession: str, filed_date: date) -> list[InsiderTxn]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    owner_el = root.find("reportingOwner")
    if owner_el is None:
        return []
    insider = (owner_el.findtext("reportingOwnerId/rptOwnerName") or "").strip()
    relationship = _relationship(owner_el)
    table = root.find("nonDerivativeTable")
    if table is None:
        return []
    out = []
    for txn in table.findall("nonDerivativeTransaction"):
        try:
            trade_date = datetime.strptime(txn.findtext("transactionDate/value") or "", "%Y-%m-%d").date()
            code = (txn.findtext("transactionCoding/transactionCode") or "").strip()
            shares_s = txn.findtext("transactionAmounts/transactionShares/value")
            shares = float(shares_s) if shares_s else 0.0
            price_s = txn.findtext("transactionAmounts/transactionPricePerShare/value")
            price = float(price_s) if price_s else None   # missing for many M/A/F rows -- no market price to report
            ad_code = (txn.findtext("transactionAmounts/transactionAcquiredDisposedCode/value") or "").strip()
        except (TypeError, ValueError):
            continue
        if shares <= 0 or not insider:
            continue
        out.append(InsiderTxn(symbol=symbol, insider=insider, relationship=relationship, code=code,
                              acquired=(ad_code == "A"), shares=shares, price=price,
                              trade_date=trade_date, filed_date=filed_date, accession=accession))
    return out


def transactions_for(symbol: str, cik: str, session: requests.Session, limit: int = 15) -> list[InsiderTxn]:
    """This company's most recent Form 4 filings (up to `limit`), every
    non-derivative (real-share) transaction in each. Derivative-only rows
    (option/RSU grants with no underlying share transaction) are left out --
    they show up as a later non-derivative row once actually exercised."""
    r = session.get(f"{SEC_DATA}/submissions/CIK{cik}.json", headers=HEADERS, timeout=20)
    time.sleep(DELAY)
    if r.status_code != 200:
        return []
    recent = (r.json().get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    out: list[InsiderTxn] = []
    n = 0
    for i, f in enumerate(forms):
        if f != "4":
            continue
        n += 1
        if n > limit:
            break
        accession = recent["accessionNumber"][i]
        # submissions.json's primaryDocument points at SEC's XSL-rendered
        # viewer path (e.g. "xslF345X06/form4.xml"), which serves
        # server-transformed HTML, not the raw XML (confirmed: content-type
        # text/html, an ElementTree parse of it finds nothing). The actual
        # data file is that same filename directly under the accession
        # root -- basename it to get there.
        primary = PurePosixPath(recent["primaryDocument"][i]).name
        filed = datetime.strptime(recent["filingDate"][i], "%Y-%m-%d").date()
        acc_nodash = accession.replace("-", "")
        url = f"{SEC_WWW}/Archives/edgar/data/{int(cik)}/{acc_nodash}/{primary}"
        rr = session.get(url, headers=HEADERS, timeout=20)
        time.sleep(DELAY)
        if rr.status_code != 200:
            continue
        out += _parse_form4(rr.text, symbol, accession, filed)
    return out
