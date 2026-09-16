"""Company names and board links for the movers scan, from this dashboard's
own data -- no separate Nifty Total Market CSV to keep re-downloading.

A ticker is a poor search term ("SAILIFE" and "INOXINDIA" return nothing on
Google News), so news.stories_for needs a name per symbol. This dashboard
already has one for essentially every NSE-traded company: the board's own
417 (backend/data/companies_raw.json) plus Screen any Chart's universe of
every actively traded NSE/BSE company (backend/chart_data/universe.json,
see app/charts.py and app/universe.py). Anything neither covers -- a stock
too thin to be in that "actively traded" set -- falls back to its own
ticker, same as the standalone tool did for a name it could not find.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
COMPANIES_RAW = BACKEND_DIR / "data" / "companies_raw.json"
UNIVERSE_INDEX = BACKEND_DIR / "chart_data" / "universe.json"

_TRAILING_LTD = re.compile(r"\s*\b(ltd\.?|limited)\b\.?\s*$", re.I)


def _clean_name(name: str) -> str:
    # Trailing "Ltd."/"Limited" only narrows a news search, so drop it, same
    # as the standalone tool did.
    return _TRAILING_LTD.sub("", name or "").strip()


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def board_map() -> dict[str, str]:
    """NSE symbol -> board code, for every board company traded on NSE."""
    records = _load_json(COMPANIES_RAW) or []
    out: dict[str, str] = {}
    for rec in records:
        nse = str(rec.get("nse_code") or "").strip().upper()
        code = str(rec.get("code") or "").strip()
        if nse and code and not nse.isdigit():
            out[nse] = code
    return out


def name_lookup() -> dict[str, str]:
    """NSE symbol -> company name, from the board plus the wider universe."""
    lookup: dict[str, str] = {}

    universe = _load_json(UNIVERSE_INDEX) or {}
    for key, row in (universe.get("stocks") or {}).items():
        if not key.startswith("NSE-"):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        name = _clean_name(str(row.get("name") or ""))
        if symbol and name:
            lookup[symbol] = name

    # The board's own names are hand-curated and take precedence.
    for rec in _load_json(COMPANIES_RAW) or []:
        nse = str(rec.get("nse_code") or "").strip().upper()
        name = _clean_name(str(rec.get("name") or ""))
        if nse and not nse.isdigit() and name:
            lookup[nse] = name
    return lookup


def company_names(symbols: list[str]) -> dict[str, str]:
    """Map symbols to names for a news search, falling back to the ticker."""
    lookup = name_lookup()
    missing = [s for s in symbols if s not in lookup]
    if missing:
        import logging

        logging.getLogger(__name__).info(
            "No company name for %d symbol(s); using the ticker: %s",
            len(missing), ", ".join(sorted(missing)[:8]),
        )
    return {symbol: lookup.get(symbol, symbol) for symbol in symbols}
