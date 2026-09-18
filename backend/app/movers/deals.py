"""Bulk and block deals, as evidence that a single large trade moved the price.

NSE publishes both as rolling archive CSVs covering recent sessions, so they are
filtered by date rather than fetched per day.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

from .bhavcopy import BROWSER_HEADERS, NSE_HOME

LOGGER = logging.getLogger(__name__)

BULK_URL = "https://nsearchives.nseindia.com/content/equities/bulk.csv"
BLOCK_URL = "https://nsearchives.nseindia.com/content/equities/block.csv"

# The real NSE-website date-range API behind the interactive report at
# nseindia.com/report-detail/display-bulk-and-block-deals -- not the same host
# as BULK_URL/BLOCK_URL above (those are the static, latest-session-only
# archive). www.nseindia.com sits behind Akamai bot protection that a plain
# User-Agent does not clear (confirmed: a bare request gets 403/503); it needs
# a full browser-shaped header set and a real page fetch first, for the
# Akamai cookies. Without &csv=true the JSON response is silently paginated
# to 70 rows regardless of how many days are in range (confirmed: a known day
# with 212 real bulk deals came back as exactly 70) -- csv=true bypasses that
# and returns the true count.
HIST_URL = "https://www.nseindia.com/api/historicalOR/bulk-block-short-deals"
HIST_REPORT_PAGE = "https://www.nseindia.com/report-detail/display-bulk-and-block-deals"
HIST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".movers_cache" / "deals"


@dataclass(frozen=True, slots=True)
class Deal:
    symbol: str
    kind: str
    client: str
    side: str
    quantity: int
    price: float
    trade_date: date

    @property
    def value_crore(self) -> float:
        return self.quantity * self.price / 10_000_000


def _fetch(url: str, timeout: int = 30) -> str:
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    session.get(NSE_HOME, timeout=timeout)
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def _parse(text: str, kind: str, trade_date: date | None) -> list[Deal]:
    """Rows matching `trade_date`, or every row when `trade_date` is None --
    used by deals_for() (one session) and all_deals() (the whole rolling
    archive NSE currently serves) respectively."""
    deals: list[Deal] = []
    for row in csv.DictReader(io.StringIO(text)):
        clean = {
            (key or "").strip().lower(): (value.strip() if isinstance(value, str) else value)
            for key, value in row.items()
        }
        raw_date = clean.get("date", "")
        # The archives use DD-MMM-YYYY ("17-SEP-2026"), a month abbreviation,
        # not a numeric month -- int(parts[1]) raised ValueError on every row
        # and was silently swallowed by the except below, so this never
        # actually matched anything before.
        try:
            row_date = datetime.strptime(raw_date, "%d-%b-%Y").date()
            if trade_date is not None and row_date != trade_date:
                continue
            quantity = int(float(clean.get("quantity traded", "0").replace(",", "")))
            price = float(clean.get("trade price / wght. avg. price", "0").replace(",", ""))
        except (TypeError, ValueError):
            continue
        symbol = str(clean.get("symbol") or "").strip()
        if not symbol or quantity <= 0 or price <= 0:
            continue
        deals.append(
            Deal(
                symbol=symbol,
                kind=kind,
                client=str(clean.get("client name") or "").strip(),
                side=str(clean.get("buy/sell") or "").strip().upper(),
                quantity=quantity,
                price=price,
                trade_date=row_date,
            )
        )
    return deals


def all_deals() -> list[Deal]:
    """Every bulk and block deal nsearchives.nseindia.com's static archive is
    currently serving -- in practice only the latest settled session (checked
    by fetching it directly; despite being named an archive it does not hold
    a rolling window), newest first. For scripts/run_deals.py's nightly run;
    see historical_deals() below for a real date-range backfill."""
    bulk_text, block_text = _fetch(BULK_URL), _fetch(BLOCK_URL)
    found = _parse(bulk_text, "bulk", None) + _parse(block_text, "block", None)
    found.sort(key=lambda d: (d.trade_date, d.value_crore), reverse=True)
    return found


def _parse_hist_csv(text: str, kind: str) -> list[Deal]:
    """historicalOR's &csv=true export -- same fields as the archive CSVs,
    but padded column names ("Date ", "Symbol ", ...) and Indian-grouped
    numbers ("1,29,600"), so its own small parser rather than reusing _parse()."""
    deals: list[Deal] = []
    for row in csv.DictReader(io.StringIO(text)):
        clean = {(key or "").strip(): (value.strip() if isinstance(value, str) else value) for key, value in row.items()}
        try:
            row_date = datetime.strptime(clean.get("Date", ""), "%d-%b-%Y").date()
            quantity = int(float(clean.get("Quantity Traded", "0").replace(",", "")))
            price = float(clean.get("Trade Price / Wght. Avg. Price", "0").replace(",", ""))
        except (TypeError, ValueError):
            continue
        symbol = str(clean.get("Symbol") or "").strip()
        if not symbol or quantity <= 0 or price <= 0:
            continue
        deals.append(Deal(symbol=symbol, kind=kind, client=str(clean.get("Client Name") or "").strip(),
                          side=str(clean.get("Buy / Sell") or "").strip().upper(),
                          quantity=quantity, price=price, trade_date=row_date))
    return deals


def historical_deals(from_date: date, to_date: date, timeout: int = 30) -> list[Deal]:
    """Every bulk and block deal NSE actually has on record for [from_date,
    to_date] -- the real date-range report behind the dashboard's own
    "display-bulk-and-block-deals" page, for a one-off backfill beyond what
    the nightly accumulation (run_deals.py, all_deals() above) has built up
    so far. Not part of the nightly run: a much heavier fetch (Akamai
    handshake against www.nseindia.com, not the plain archive host), and
    only ever needed once to seed history, or occasionally to heal a gap."""
    session = requests.Session()
    session.headers.update(HIST_HEADERS)
    session.get(HIST_REPORT_PAGE, timeout=timeout)   # Akamai cookies, same as a real page load
    found: list[Deal] = []
    for option_type, kind in (("bulk_deals", "bulk"), ("block_deals", "block")):
        r = session.get(HIST_URL, params={"optionType": option_type, "from": from_date.strftime("%d-%m-%Y"),
                                          "to": to_date.strftime("%d-%m-%Y"), "csv": "true"},
                        headers={"Accept": "application/json, text/plain, */*", "Referer": HIST_REPORT_PAGE},
                        timeout=timeout)
        r.raise_for_status()
        found += _parse_hist_csv(r.content.decode("utf-8-sig"), kind)
    found.sort(key=lambda d: (d.trade_date, d.value_crore), reverse=True)
    return found


def deals_for(trade_date: date, *, cache_dir: Path = DEFAULT_CACHE_DIR) -> dict[str, list[Deal]]:
    """Return bulk and block deals for a session, keyed by symbol."""
    path = cache_dir / f"{trade_date.isoformat()}.csv"
    if path.is_file() and path.stat().st_size > 10:
        combined = path.read_text(encoding="utf-8")
        parts = combined.split("###BLOCK###")
        found = _parse(parts[0], "bulk", trade_date)
        if len(parts) > 1:
            found += _parse(parts[1], "block", trade_date)
    else:
        try:
            bulk_text, block_text = _fetch(BULK_URL), _fetch(BLOCK_URL)
        except Exception:
            LOGGER.exception("Could not download deal archives for %s", trade_date.isoformat())
            return {}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(bulk_text + "###BLOCK###" + block_text, encoding="utf-8")
        found = _parse(bulk_text, "bulk", trade_date) + _parse(block_text, "block", trade_date)

    by_symbol: dict[str, list[Deal]] = {}
    for deal in found:
        by_symbol.setdefault(deal.symbol, []).append(deal)
    for rows in by_symbol.values():
        rows.sort(key=lambda d: -d.value_crore)
    LOGGER.info("%d symbol(s) had bulk or block deals on %s", len(by_symbol), trade_date.isoformat())
    return by_symbol
