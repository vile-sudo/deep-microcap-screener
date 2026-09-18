"""NSE corporate announcements, windowed to the session they could have moved.

A filing made at 15:59 cannot explain the close at 15:30 that day; it explains the
next session. So the window for a session is the previous close to this close,
and anything outside it is evidence for a different day.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .nse import NseClient, as_records, cached_json, nse_date, parse_nse_datetime

LOGGER = logging.getLogger(__name__)

ANNOUNCEMENTS_URL = (
    "https://www.nseindia.com/api/corporate-announcements"
    "?index=equities&from_date={start}&to_date={end}"
)
MARKET_CLOSE = time(15, 30)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".movers_cache" / "announcements"

# Filing categories that plausibly move a price, most explanatory first.
MATERIAL = (
    (re.compile(r"bagging|receiv.*order|order.*receiv|award", re.I), "order win", 5),
    (re.compile(r"financial result|outcome of board meeting.*result", re.I), "results", 5),
    (re.compile(r"acquisition|amalgamation|merger|demerger|slump sale|divest", re.I), "M&A", 5),
    (re.compile(r"qualified institution|preferential|fund rais|qip|issue of (?:equity|shares)", re.I), "fundraise", 4),
    (re.compile(r"buy ?back|bonus|stock split|sub-division|dividend", re.I), "capital action", 4),
    (re.compile(r"credit rating", re.I), "rating change", 4),
    (re.compile(r"resignation|appointment|cessation|change in (?:key )?management|director", re.I), "management change", 3),
    (re.compile(r"investor|analyst|earnings call|concall|meet", re.I), "investor meeting", 2),
    (re.compile(r"spurt in volume|price movement|clarification", re.I), "exchange query", 2),
    (re.compile(r"plant|capacity|expansion|commission|commercial production", re.I), "capacity or plant", 4),
    (re.compile(r"agreement|contract|mou|partnership|joint venture", re.I), "agreement", 4),
    (re.compile(r"regulatory|penalty|show cause|sebi|litigation|court|tribunal|insolvency", re.I), "regulatory or legal", 4),
)
# Routine housekeeping that almost never moves a price.
ROUTINE = re.compile(
    r"newspaper publication|trading window|compliance certificate|share transfer|"
    r"annual report|notice of|record date intimation|loss of (?:share )?certificate|"
    r"duplicate (?:share )?certificate|reg\.? 74|regulation 74|investor presentation",
    re.I,
)


@dataclass(frozen=True, slots=True)
class Filing:
    symbol: str
    company: str
    category: str
    summary: str
    filed_at: datetime | None
    url: str
    kind: str
    weight: int

    @property
    def is_routine(self) -> bool:
        return self.weight == 0


def classify(category: str, summary: str) -> tuple[str, int]:
    """Label a filing and score how much it could explain a move."""
    text = f"{category} {summary}"
    if ROUTINE.search(text):
        return "routine filing", 0
    for pattern, kind, weight in MATERIAL:
        if pattern.search(text):
            return kind, weight
    return "other disclosure", 1


def load_announcements(
    client: NseClient,
    day: date,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> list[dict]:
    """Fetch one calendar day of filings, cached."""
    path = cache_dir / f"{day.isoformat()}.json"
    payload = cached_json(
        path,
        lambda: client.get_json(ANNOUNCEMENTS_URL.format(start=nse_date(day), end=nse_date(day))),
        label=f"announcements for {day.isoformat()}",
    )
    return as_records(payload)


def filings_for_session(
    client: NseClient,
    trade_date: date,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    previous_session: date | None = None,
) -> dict[str, list[Filing]]:
    """Collect filings that fall in the window able to move this session's close."""
    previous = previous_session or _previous_weekday(trade_date)
    window_start = datetime.combine(previous, MARKET_CLOSE)
    window_end = datetime.combine(trade_date, MARKET_CLOSE)

    records: list[dict] = []
    for day in _days_between(previous, trade_date):
        try:
            records += load_announcements(client, day, cache_dir=cache_dir)
        except Exception:
            LOGGER.exception("Could not load announcements for %s", day.isoformat())

    by_symbol: dict[str, list[Filing]] = {}
    for row in records:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        filed_at = parse_nse_datetime(row.get("an_dt") or row.get("exchdisstime"))
        if filed_at is None or not (window_start < filed_at <= window_end):
            continue
        category = str(row.get("desc") or "").strip()
        summary = re.sub(r"\s+", " ", str(row.get("attchmntText") or "")).strip()
        kind, weight = classify(category, summary)
        by_symbol.setdefault(symbol, []).append(
            Filing(
                symbol=symbol,
                company=str(row.get("sm_name") or "").strip(),
                category=category,
                summary=summary,
                filed_at=filed_at,
                url=str(row.get("attchmntFile") or "").strip(),
                kind=kind,
                weight=weight,
            )
        )
    for filings in by_symbol.values():
        filings.sort(key=lambda f: (-f.weight, f.filed_at or datetime.min))
    LOGGER.info(
        "%d symbol(s) filed between %s and %s",
        len(by_symbol),
        window_start.strftime("%d %b %H:%M"),
        window_end.strftime("%d %b %H:%M"),
    )
    return by_symbol


def range_announcements(
    client: NseClient,
    start: date,
    end: date,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> list[dict]:
    """Every filing NSE published between start and end (inclusive), classified
    but NOT windowed to any single session's close the way filings_for_session()
    is -- for a general announcements feed (scripts/run_announcements.py), not
    move attribution. One dict per filing, not grouped by symbol, keeping
    seq_id (NSE's own announcement id, the reliable dedup key across runs)
    that the Filing dataclass elsewhere in this module has no use for."""
    records: list[dict] = []
    for day in _days_between(start, end):
        try:
            records += load_announcements(client, day, cache_dir=cache_dir)
        except Exception:
            LOGGER.exception("Could not load announcements for %s", day.isoformat())

    out: list[dict] = []
    for row in records:
        symbol = str(row.get("symbol") or "").strip()
        filed_at = parse_nse_datetime(row.get("an_dt") or row.get("exchdisstime"))
        if not symbol or filed_at is None:
            continue
        category = str(row.get("desc") or "").strip()
        summary = re.sub(r"\s+", " ", str(row.get("attchmntText") or "")).strip()
        kind, weight = classify(category, summary)
        out.append({
            "seq_id": str(row.get("seq_id") or ""),
            "symbol": symbol,
            "company": str(row.get("sm_name") or "").strip(),
            "category": category,
            "summary": summary,
            "kind": kind,
            "weight": weight,
            "filed_at": filed_at.isoformat(),
            "url": str(row.get("attchmntFile") or "").strip(),
        })
    return out


def _previous_weekday(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _days_between(start: date, end: date) -> list[date]:
    days, current = [], start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days
