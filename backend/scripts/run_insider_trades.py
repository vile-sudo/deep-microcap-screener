"""
Insider trading & SAST disclosures -- promoters/directors/KMPs' own buy/sell/pledge transactions,
NSE's own reports (app/movers/insider_trading.py). Same dashboard section as Bulk & Block Deals
(scripts/run_deals.py): both are "who's doing what, disclosed" signals, just from different SEBI
regulations -- bulk/block names any large counterparty, this names the company's own insiders.

    cd backend
    python scripts/run_insider_trades.py               # last 14 days of NSE's summary feed
    python scripts/run_insider_trades.py --days 60      # a deeper one-off backfill

Runs inside .github/workflows/daily.yml (alongside deals/announcements/deal clusters). Cheap to run
often: NSE's summary list is one request regardless of window size, and every disclosure this script has
already seen (by its own unique appId) is skipped -- only genuinely new disclosures cost an extra
request, for their one XBRL XML file. A normal night sees roughly 5-15 new disclosures market-wide; the
default 14-day window is just a safety margin against a missed run, not the normal cost.

Writes backend/data/insider_trades/latest.json, which app/routers/deals.py serves to the dashboard's
Bulk & Block Deals page (its "Insider Trading" tab).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.movers.companies import board_map, name_lookup  # noqa: E402
from app.movers.insider_trading import fetch_detail, list_recent, open_session  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
OUT_FILE = BACKEND_DIR / "data" / "insider_trades" / "latest.json"
RETENTION_DAYS = 120


def _broadcast_dt(raw: str):
    try:
        return datetime.strptime(raw, "%d-%b-%Y %H:%M:%S")
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="how far back into NSE's summary feed to look for new disclosures")
    args = ap.parse_args()

    try:
        existing = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("trades") or []
    except (OSError, ValueError):
        existing = []
    known_ids = {str(r["app_id"]) for r in existing if r.get("app_id")}

    try:
        session = open_session()
        summary = list_recent(session)
    except Exception as e:  # noqa: BLE001 - NSE unreachable must not crash the workflow
        print(f"run_insider_trades: could not fetch the summary list ({e}); keeping the existing history")
        return 0

    cutoff_dt = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30) - timedelta(days=args.days)
    candidates = [m for m in summary if str(m.get("appId")) not in known_ids
                  and (_broadcast_dt(m.get("broadcastDateTime", "")) or cutoff_dt) >= cutoff_dt]

    board, names = board_map(), name_lookup()
    fresh, failed = [], 0
    for meta in candidates:
        trade = fetch_detail(session, meta)
        time.sleep(0.2)   # a little spacing, same courtesy run_deals.py's historical_deals() and
                          # news_channel.py's Google News fetches already show NSE/Google
        if trade is None:
            failed += 1
            continue
        fresh.append({
            "app_id": trade.app_id, "date": trade.date, "symbol": trade.symbol,
            "name": names.get(trade.symbol, trade.company_name or trade.symbol),
            "board_code": board.get(trade.symbol),
            "person": trade.person, "category": trade.category,
            "transaction_type": trade.transaction_type, "side": trade.side,
            "quantity": trade.quantity, "value_cr": round(trade.value_cr, 3), "mode": trade.mode,
            "pre_qty": trade.pre_qty, "pre_pct": trade.pre_pct,
            "post_qty": trade.post_qty, "post_pct": trade.post_pct,
            "broadcast_at": trade.broadcast_at, "regulation": trade.regulation,
        })

    merged = {r["app_id"]: r for r in existing}
    merged.update({r["app_id"]: r for r in fresh})
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=RETENTION_DAYS)).isoformat()
    rows = sorted((r for r in merged.values() if r["date"] >= cutoff), key=lambda r: r["date"], reverse=True)

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(rows), "board_count": sum(1 for r in rows if r.get("board_code")),
        "trades": rows,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"run_insider_trades: {len(candidates)} candidate(s) in the last {args.days}d, "
          f"{len(fresh)} new ({failed} could not be parsed), {len(rows)} kept after a {RETENTION_DAYS}-day window "
          f"({payload['board_count']} on the board)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
