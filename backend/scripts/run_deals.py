"""
Bulk & block deals: NSE's own report (nseindia.com/report-detail/display-
bulk-and-block-deals), rebuilt for the dashboard.

    cd backend
    python scripts/run_deals.py

Runs inside .github/workflows/daily.yml (step 1b, right after the
fundamentals refresh), on that workflow's 02:00 IST schedule -- so this
needed no new cron of its own. Writes backend/data/deals/latest.json, which
app/routers/deals.py serves to the dashboard's Bulk & Block Deals page.

NSE's bulk.csv/block.csv turn out to serve only the LATEST settled session,
not a rolling multi-week window (checked by fetching them directly -- every
row came back dated the same day). So this script accumulates its own
history across runs: today's fetch is merged into whatever is already
saved, deduped, and anything older than RETENTION_DAYS is dropped -- the
same "merge in, prune the tail" shape as the exchange's own daily bhavcopy
cache elsewhere in this codebase, just for a much smaller file.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.movers.companies import board_map, name_lookup  # noqa: E402
from app.movers.deals import all_deals  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
OUT_FILE = BACKEND_DIR / "data" / "deals" / "latest.json"
RETENTION_DAYS = 120


def _key(r: dict) -> tuple:
    return (r["date"], r["symbol"], r["kind"], r["client"], r["side"], r["quantity"], r["price"])


def main() -> int:
    try:
        deals = all_deals()
    except Exception as e:  # noqa: BLE001 - NSE unreachable must not crash the workflow
        deals = []
        print(f"run_deals: could not fetch bulk/block deals ({e}); keeping the existing history")

    board, names = board_map(), name_lookup()
    fresh = [{
        "date": d.trade_date.isoformat(),
        "symbol": d.symbol,
        "name": names.get(d.symbol, d.symbol),
        "board_code": board.get(d.symbol),
        "kind": d.kind,
        "client": d.client,
        "side": d.side,
        "quantity": d.quantity,
        "price": d.price,
        "value_cr": round(d.value_crore, 2),
    } for d in deals]

    try:
        existing = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("deals") or []
    except (OSError, ValueError):
        existing = []

    merged = {_key(r): r for r in existing}
    added = sum(1 for r in fresh if _key(r) not in merged)
    merged.update({_key(r): r for r in fresh})

    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=RETENTION_DAYS)).isoformat()
    rows = sorted((r for r in merged.values() if r["date"] >= cutoff),
                  key=lambda r: (r["date"], r["value_cr"]), reverse=True)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "from_date": rows[-1]["date"] if rows else None,
        "to_date": rows[0]["date"] if rows else None,
        "count": len(rows),
        "board_count": sum(1 for r in rows if r["board_code"]),
        "deals": rows,
    }, separators=(",", ":")), encoding="utf-8")

    print(f"run_deals: {len(fresh)} fetched ({added} new), {len(rows)} kept after a {RETENTION_DAYS}-day "
          f"window ({sum(1 for r in rows if r['board_code'])} on board), "
          f"{rows[-1]['date'] if rows else '—'} to {rows[0]['date'] if rows else '—'} -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
