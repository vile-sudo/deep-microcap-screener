"""
Movers: every NSE stock that closed 4%+ up or down, with why.

    cd backend && python scripts/run_movers.py                  # most recent settled session
    python scripts/run_movers.py --date 2026-09-10               # rescan/backfill one date
    python scripts/run_movers.py --no-attribution                # prices only, no network beyond the bhavcopy

Reads NSE's daily bhavcopy (every EQ/BE/BZ/SM/ST series it carries -- main
board, trade-for-trade and the SME platform, not a fixed index list), flags
every close at least THRESHOLD_PERCENT from the previous close, adjusts for
a corporate action going ex that session, and -- unless --no-attribution --
looks up why: NSE's own filings and corporate actions, allowlisted news
coverage, and bulk/block deals. Writes one JSON snapshot per trading day to
backend/data/movers/, which app/routers/movers.py serves to the dashboard's
Movers page.

This scan itself was proven out in a standalone tool before crossing over
here (see backend/app/movers/__init__.py for what changed in the move); the
scan, adjustment and attribution logic in backend/app/movers/ are otherwise
the same modules.
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
from datetime import date, datetime, time as time_type, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.movers.bhavcopy import MarketHoliday, load_closes  # noqa: E402
from app.movers.companies import board_map, company_names  # noqa: E402
from app.movers.scan import format_report, scan_closes, write_snapshot  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
BHAVCOPY_READY = time_type(16, 30)   # NSE publishes within about an hour of the close
THRESHOLD_PERCENT = 4.0
# A quiet session flags a few dozen moves; a busy one, several hundred, since
# the universe is every NSE equity series rather than a fixed index -- see
# app/movers/__init__.py. Filings, corporate actions and deals are cheap (a
# handful of requests cover the whole day), but news is one Google query per
# symbol, so it is bounded rather than run for every long-tail penny stock.
# The board's own companies always get it regardless of rank.
NEWS_LIMIT = int(os.environ.get("MOVERS_NEWS_LIMIT", "120"))
BACKEND_DIR = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = BACKEND_DIR / "data" / "movers"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                         stream=sys.stderr)
    logger = logging.getLogger(__name__)

    now = datetime.now(IST)
    trade_date = date.fromisoformat(args.date) if args.date else _default_date(now)
    if trade_date.weekday() >= 5:
        print(f"{trade_date.isoformat()} is a weekend; there is no closing data to scan.")
        return 0

    try:
        closes = load_closes(trade_date)
    except MarketHoliday as e:
        print(f"No movers scan for {trade_date.isoformat()}: {e}")
        return 0

    symbols = sorted(closes)
    if not symbols:
        print("The bhavcopy contained no equity rows; nothing to scan.")
        return 1

    threshold = THRESHOLD_PERCENT
    logger.info("Scanning %d symbols for closing moves of +/-%.1f%% on %s",
                len(symbols), threshold, trade_date.isoformat())

    adjustments = {}
    if not args.no_attribution:
        adjustments = _corporate_actions(trade_date, logger)
    moves, stats = scan_closes(closes, symbols, trade_date=trade_date, threshold=threshold, adjustments=adjustments)
    board = board_map()
    news_checked = None
    if not args.no_attribution and moves:
        news_checked = _attribute(moves, trade_date, adjustments, board, logger)

    names = company_names([m.symbol for m in moves])
    report = format_report(moves, stats, threshold, trade_date)
    path = write_snapshot(SNAPSHOT_DIR, moves, stats, threshold, trade_date,
                           names=names, board_codes=board, news_checked=news_checked)

    print()
    print(report)
    print(f"\nSnapshot written to {path}")
    return 0


def _corporate_actions(trade_date: date, logger: logging.Logger) -> dict:
    """Corporate actions going ex on this session, or nothing if NSE is unreachable."""
    from app.movers.corpactions import adjustments_for, load_actions
    from app.movers.nse import NseClient

    try:
        records = load_actions(NseClient(), trade_date)
        return adjustments_for(records, trade_date)
    except Exception:
        logger.exception("Could not load corporate actions; ex-date moves will not be adjusted")
        return {}


def _attribute(moves: list, trade_date: date, adjustments: dict, board: dict, logger: logging.Logger) -> set[str]:
    """Attach an explanation to every flagged move, in place.

    Filings, corporate actions and deals are fetched once for the whole day
    and applied to every move. News is looked up per symbol, so on a day with
    several hundred flagged moves it is capped at NEWS_LIMIT -- board
    companies first, then the largest moves by size -- and returns which
    symbols were actually checked, so the snapshot can say "not checked"
    rather than implying nothing was found.
    """
    from app.movers.announcements import filings_for_session
    from app.movers.attribution import explain, summarise
    from app.movers.companies import company_names as names_for
    from app.movers.deals import deals_for
    from app.movers.news import stories_for
    from app.movers.nse import NseClient

    symbols = [m.symbol for m in moves]
    names = names_for(symbols)

    try:
        filings = filings_for_session(NseClient(), trade_date)
    except Exception:
        logger.exception("Could not load announcements")
        filings = {}
    try:
        deals = deals_for(trade_date)
    except Exception:
        logger.exception("Could not load bulk and block deals")
        deals = {}

    priority = sorted(moves, key=lambda m: (m.symbol not in board, -abs(m.pct_change)))
    news_symbols = [m.symbol for m in priority[:NEWS_LIMIT]]
    if len(moves) > NEWS_LIMIT:
        logger.info("News: checking %d of %d flagged moves (board companies plus the largest by size)",
                    len(news_symbols), len(moves))
    try:
        stories = stories_for({s: names[s] for s in news_symbols}, trade_date)
    except Exception:
        logger.exception("Could not load news")
        stories = {}

    for index, move in enumerate(moves):
        explanation = explain(
            move.symbol,
            adjustment=adjustments.get(move.symbol),
            filings=filings.get(move.symbol, []),
            stories=stories.get(move.symbol, []),
            deals=deals.get(move.symbol, []),
        )
        moves[index] = dataclasses.replace(move, explanation=explanation)
    counts = summarise(m.explanation for m in moves)
    logger.info(
        "Attribution: %d high, %d medium, %d low, %d mechanical, %d with no disclosed reason",
        counts.get("high", 0), counts.get("medium", 0), counts.get("low", 0),
        counts.get("mechanical", 0), counts.get("none", 0),
    )
    return set(news_symbols)


def _default_date(now: datetime) -> date:
    """The most recently settled session as of now: today once the bhavcopy is
    out, otherwise the most recent weekday. Run at 02:00 IST -- always before
    16:30 -- this always resolves to yesterday's close, walked back over a
    weekend."""
    candidate = now.date()
    if now.time() < BHAVCOPY_READY:
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan NSE closes for large end-of-day moves.")
    parser.add_argument("--date", help="Trading date to scan as YYYY-MM-DD.")
    parser.add_argument("--no-attribution", action="store_true",
                         help="Skip corporate actions, filings, deals and news; scan prices only.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
