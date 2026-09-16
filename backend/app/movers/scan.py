"""End-of-day scan: flag stocks whose close moved far from the previous close,
and persist the result as one JSON snapshot per trading day.

Adapted from the standalone tool's eod.py: the scan and the snapshot shape are
unchanged; what is gone is the SQLite bookkeeping (has_flagged_today /
record_flag) and the static-site rendering, neither of which this dashboard
needs -- the snapshot file itself is the record, exactly as Sector Research and
Research Reports already keep theirs (see app/routers/sectors.py, reports.py).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

from .bhavcopy import Close

LOGGER = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True, slots=True)
class ClosingMove:
    symbol: str
    series: str
    trade_date: date
    direction: str
    pct_change: float
    close: float
    prev_close: float
    delivery_percent: float | None
    # The unadjusted change, kept so an ex-date move can show both figures.
    raw_pct_change: float | None = None
    corporate_action: str | None = None
    explanation: object | None = None


def scan_closes(
    closes: dict[str, Close],
    symbols: Iterable[str],
    *,
    trade_date: date,
    threshold: float = 4.0,
    adjustments: dict | None = None,
) -> tuple[list[ClosingMove], dict[str, int]]:
    """Flag every symbol whose close is at least `threshold` from the previous close.

    The bhavcopy's PREV_CLOSE is not adjusted for a corporate action going ex that
    morning, so the raw change on an ex-date mixes a real move with arithmetic. Any
    adjustment supplied is applied to the previous close first, which means a stock
    is measured on what actually moved and drops out if only the action explains it.
    """
    adjustments = adjustments or {}
    moves: list[ClosingMove] = []
    stats = {"scanned": 0, "flagged": 0, "missing": 0, "adjusted": 0, "mechanical_only": 0}
    for symbol in symbols:
        row = closes.get(symbol)
        if row is None:
            stats["missing"] += 1
            continue
        stats["scanned"] += 1

        prev_close = row.prev_close
        adjustment = adjustments.get(symbol)
        corporate_action = None
        if adjustment is not None:
            corporate_action = adjustment.subject
            if adjustment.quantifiable:
                adjusted = adjustment.apply(row.prev_close)
                if adjusted > 0:
                    stats["adjusted"] += 1
                    prev_close = adjusted

        pct_change = (row.close - prev_close) / prev_close * 100
        if abs(pct_change) < threshold:
            if abs(row.pct_change) >= threshold:
                # It only cleared the threshold before the action was taken out.
                stats["mechanical_only"] += 1
                LOGGER.info(
                    "%s: %+.2f%% raw is %+.2f%% once %s is adjusted out; not flagged",
                    symbol,
                    row.pct_change,
                    pct_change,
                    adjustment.kind if adjustment else "the action",
                )
            continue

        stats["flagged"] += 1
        moves.append(
            ClosingMove(
                symbol=row.symbol,
                series=row.series,
                trade_date=trade_date,
                direction="UP" if pct_change > 0 else "DOWN",
                pct_change=pct_change,
                close=row.close,
                prev_close=prev_close,
                delivery_percent=row.delivery_percent,
                raw_pct_change=row.pct_change,
                corporate_action=corporate_action,
            )
        )
    moves.sort(key=lambda move: move.pct_change, reverse=True)
    return moves, stats


def write_snapshot(
    directory: Path,
    moves: Iterable[ClosingMove],
    stats: dict[str, int],
    threshold: float,
    trade_date: date,
    *,
    names: dict[str, str] | None = None,
    board_codes: dict[str, str] | None = None,
    news_checked: set[str] | None = None,
    generated_at: datetime | None = None,
) -> Path:
    """Persist one session as JSON, so the Movers page can show history.

    Each session is written once and never rewritten unless its content
    actually changes -- so a rerun (a news window closing, say) does not churn
    the file every night.
    """
    names = names or {}
    board_codes = board_codes or {}
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{trade_date.isoformat()}.json"
    payload = {
        "trade_date": trade_date.isoformat(),
        "threshold_percent": threshold,
        "generated_at": (generated_at or datetime.now(IST)).isoformat(timespec="seconds"),
        "stats": stats,
        "moves": [
            {
                "symbol": m.symbol,
                "name": names.get(m.symbol) or m.symbol,
                "board_code": board_codes.get(m.symbol),
                "series": m.series,
                "direction": m.direction,
                "pct_change": round(m.pct_change, 2),
                "close": m.close,
                "prev_close": m.prev_close,
                "delivery_percent": m.delivery_percent,
                "raw_pct_change": (
                    round(m.raw_pct_change, 2) if m.raw_pct_change is not None else None
                ),
                "corporate_action": m.corporate_action,
                "why": _explanation_payload(m.explanation),
                # News is not searched for every one of a busy day's several hundred
                # moves (see MOVERS_NEWS_LIMIT in scripts/run_movers.py) -- False here
                # means "not checked", which is different from "checked, found nothing".
                "news_checked": True if news_checked is None else m.symbol in news_checked,
            }
            for m in moves
        ],
    }

    # A settled session never changes, so rewriting it only churns the timestamp.
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = None
        if isinstance(existing, dict) and _without_stamp(existing) == _without_stamp(payload):
            LOGGER.info("Snapshot for %s is unchanged; leaving it as it is", trade_date.isoformat())
            return path

    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + chr(10), encoding="utf-8")
    return path


def _explanation_payload(explanation: object) -> dict | None:
    """Flatten an Explanation for storage, or None when there is nothing to store."""
    if explanation is None:
        return None
    return {
        "headline": explanation.headline,
        "confidence": explanation.confidence,
        "evidence": [
            {
                "tier": e.tier,
                "kind": e.kind,
                "detail": e.detail,
                "url": e.url,
                "source": e.source,
            }
            for e in explanation.evidence
        ],
    }


def _without_stamp(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "generated_at"}


def load_snapshot(directory: Path, trade_date: str) -> dict | None:
    path = directory / f"{trade_date}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def available_dates(directory: Path) -> list[str]:
    """Every scanned trading day, newest first."""
    if not directory.is_dir():
        return []
    return sorted((p.stem for p in directory.glob("????-??-??.json")), reverse=True)


def format_report(moves: list[ClosingMove], stats: dict[str, int], threshold: float, trade_date: date) -> str:
    """Render the scan as a plain-text table, for the automation log."""
    lines = [
        f"NSE closing moves of {threshold:.1f}% or more on {trade_date.isoformat()}",
        "",
        f"{'SYMBOL':<14}{'SER':<5}{'DIR':<6}{'CHANGE':>9}{'CLOSE':>12}{'PREV':>12}{'DELIV%':>9}  WHY",
        "-" * 90,
    ]
    for move in moves:
        delivery = f"{move.delivery_percent:>8.2f}" if move.delivery_percent is not None else f"{'-':>8}"
        why = move.explanation.headline if move.explanation is not None else ""
        lines.append(
            f"{move.symbol:<14}{move.series:<5}{move.direction:<6}"
            f"{move.pct_change:>8.2f}%{move.close:>12.2f}{move.prev_close:>12.2f}{delivery}  {why}"
        )
    if not moves:
        lines.append("(none)")
    gainers = sum(1 for move in moves if move.direction == "UP")
    lines += [
        "-" * 90,
        f"{len(moves)} flagged ({gainers} up, {len(moves) - gainers} down) from {stats['scanned']} scanned",
    ]
    if stats["missing"]:
        lines.append(f"{stats['missing']} universe symbol(s) had no row in the bhavcopy")
    if stats.get("mechanical_only"):
        lines.append(
            f"{stats['mechanical_only']} move(s) fell below the threshold once a "
            "corporate action was adjusted out"
        )
    return "\n".join(lines)
