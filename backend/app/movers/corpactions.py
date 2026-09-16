"""NSE corporate actions, and the price adjustment they imply.

The bhavcopy's PREV_CLOSE is the previous session's raw close. It is not adjusted
for a corporate action taking effect that morning, so on an ex-date the reported
change mixes a real move with pure arithmetic. A stock going ex-dividend Rs 2 on a
Rs 85 close shows a 2.3% "fall" that nobody traded; a 1:1 bonus shows as -50%.

This module reads the actions and returns the factor that puts the previous close
on the same footing as today's, so the screener measures only what actually moved.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .nse import NseClient, as_records, cached_json, nse_date, parse_nse_datetime

LOGGER = logging.getLogger(__name__)

ACTIONS_URL = (
    "https://www.nseindia.com/api/corporates-corporateActions"
    "?index=equities&from_date={start}&to_date={end}"
)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".movers_cache" / "corpactions"

# "Dividend - Rs 2 Per Share", "Interim Dividend - Rs 1.50 Per Share"
DIVIDEND = re.compile(r"dividend.*?rs\.?\s*([0-9]+(?:\.[0-9]+)?)", re.I | re.S)
# "Bonus 1:1", "Bonus issue 2:5"
BONUS = re.compile(r"bonus\D*([0-9]+)\s*:\s*([0-9]+)", re.I)
# "Face Value Split From Rs 10 To Rs 2", "Stock Split From Rs 10/- To Re 1/-"
SPLIT = re.compile(
    r"(?:split|sub-?division).*?from\s*(?:rs\.?|re\.?)\s*([0-9]+(?:\.[0-9]+)?)"
    r".*?to\s*(?:rs\.?|re\.?)\s*([0-9]+(?:\.[0-9]+)?)",
    re.I | re.S,
)
# Actions whose effect on price cannot be computed from the subject line alone.
UNQUANTIFIABLE = re.compile(r"demerger|scheme of arrangement|rights|amalgamat|capital reduction", re.I)


@dataclass(frozen=True, slots=True)
class Adjustment:
    """How a corporate action changes the comparable previous close."""

    symbol: str
    subject: str
    # adjusted_prev_close = prev_close * factor - cash
    factor: float = 1.0
    cash: float = 0.0
    quantifiable: bool = True

    def apply(self, prev_close: float) -> float:
        return prev_close * self.factor - self.cash

    @property
    def kind(self) -> str:
        text = self.subject.lower()
        if "bonus" in text:
            return "bonus issue"
        if "split" in text or "sub-division" in text:
            return "stock split"
        if "dividend" in text:
            return "ex-dividend"
        return self.subject.split("-")[0].strip().lower() or "corporate action"


def parse_adjustment(symbol: str, subject: str) -> Adjustment | None:
    """Turn an action's subject line into a price adjustment, if it implies one."""
    text = (subject or "").strip()
    if not text:
        return None

    bonus = BONUS.search(text)
    if bonus:
        new, held = float(bonus.group(1)), float(bonus.group(2))
        if new > 0 and held > 0:
            # a:b means a new shares for every b held, so the price scales by b/(a+b).
            return Adjustment(symbol, text, factor=held / (new + held))

    split = SPLIT.search(text)
    if split:
        old_fv, new_fv = float(split.group(1)), float(split.group(2))
        if old_fv > 0 and new_fv > 0:
            return Adjustment(symbol, text, factor=new_fv / old_fv)

    dividend = DIVIDEND.search(text)
    if dividend:
        return Adjustment(symbol, text, cash=float(dividend.group(1)))

    if UNQUANTIFIABLE.search(text):
        # Real and often large, but not derivable from the subject line.
        return Adjustment(symbol, text, quantifiable=False)
    return None


def load_actions(
    client: NseClient,
    trade_date: date,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    window_days: int = 6,
) -> list[dict]:
    """Fetch the actions around a session, cached by date."""
    from datetime import timedelta

    start = trade_date - timedelta(days=window_days)
    end = trade_date + timedelta(days=window_days)
    path = cache_dir / f"{trade_date.isoformat()}.json"
    payload = cached_json(
        path,
        lambda: client.get_json(ACTIONS_URL.format(start=nse_date(start), end=nse_date(end))),
        label=f"corporate actions around {trade_date.isoformat()}",
    )
    return as_records(payload)


def adjustments_for(records: list[dict], trade_date: date) -> dict[str, Adjustment]:
    """Return the adjustment per symbol whose action goes ex on this session."""
    found: dict[str, Adjustment] = {}
    for row in records:
        ex_date = parse_nse_datetime(row.get("exDate"))
        symbol = str(row.get("symbol") or "").strip()
        if not symbol or ex_date is None or ex_date.date() != trade_date:
            continue
        adjustment = parse_adjustment(symbol, str(row.get("subject") or ""))
        if adjustment is None:
            continue
        existing = found.get(symbol)
        if existing is None:
            found[symbol] = adjustment
        else:
            # A stock can go ex on several actions at once; combine them.
            found[symbol] = Adjustment(
                symbol,
                f"{existing.subject}; {adjustment.subject}",
                factor=existing.factor * adjustment.factor,
                cash=existing.cash + adjustment.cash,
                quantifiable=existing.quantifiable and adjustment.quantifiable,
            )
    if found:
        LOGGER.info(
            "%d symbol(s) go ex on %s: %s",
            len(found),
            trade_date.isoformat(),
            ", ".join(sorted(found)[:8]),
        )
    return found
