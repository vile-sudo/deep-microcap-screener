"""Bulk and block deals, as evidence that a single large trade moved the price.

NSE publishes both as rolling archive CSVs covering recent sessions, so they are
filtered by date rather than fetched per day.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import requests

from .bhavcopy import BROWSER_HEADERS, NSE_HOME

LOGGER = logging.getLogger(__name__)

BULK_URL = "https://nsearchives.nseindia.com/content/equities/bulk.csv"
BLOCK_URL = "https://nsearchives.nseindia.com/content/equities/block.csv"

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".movers_cache" / "deals"


@dataclass(frozen=True, slots=True)
class Deal:
    symbol: str
    kind: str
    client: str
    side: str
    quantity: int
    price: float

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


def _parse(text: str, kind: str, trade_date: date) -> list[Deal]:
    deals: list[Deal] = []
    for row in csv.DictReader(io.StringIO(text)):
        clean = {
            (key or "").strip().lower(): (value.strip() if isinstance(value, str) else value)
            for key, value in row.items()
        }
        raw_date = clean.get("date", "")
        # The archives use DD-MM-YYYY.
        parts = raw_date.split("-")
        if len(parts) != 3:
            continue
        try:
            if date(int(parts[2]), int(parts[1]), int(parts[0])) != trade_date:
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
            )
        )
    return deals


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
