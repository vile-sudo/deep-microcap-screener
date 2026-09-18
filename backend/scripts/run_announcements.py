"""
Corporate announcements: NSE's own filings feed, classified and rebuilt for
the dashboard's Bulk & Block Deals page (announcements sit alongside deals
there -- both are "who's doing what, disclosed" signals on the same page).

    cd backend
    python scripts/run_announcements.py                       # yesterday + today (the nightly run)
    python scripts/run_announcements.py --backfill-days 30    # a real date-range fetch, one-off

Runs inside .github/workflows/daily.yml (step 1c, right after the deals
scan), on that workflow's 02:00 IST schedule. Writes
backend/data/announcements/latest.json, which app/routers/deals.py serves
alongside the deals themselves.

Unlike bulk/block deals, NSE's corporate-announcements API genuinely
supports a date range (checked: a 31-day request returned filings spanning
all 31 calendar days, no silent cap) -- so a backfill is one request per
day, not a different endpoint. Still accumulated across nightly runs rather
than re-fetched in full each time, same "merge in, prune the tail" shape as
run_deals.py, deduped on NSE's own seq_id.

Filtered at write time to the categories app/movers/announcements.classify()
scores as more than routine: a market-wide feed easily runs into the
thousands a day (about 480 seen in one real day, roughly 30% of it flagged
"routine filing" -- newspaper notices, compliance certificates, transfer
agent housekeeping) and that volume would swamp anything worth seeing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.movers.announcements import range_announcements  # noqa: E402
from app.movers.companies import board_map, name_lookup  # noqa: E402
from app.movers.nse import NseClient  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
OUT_FILE = BACKEND_DIR / "data" / "announcements" / "latest.json"
RETENTION_DAYS = 45
SUMMARY_MAX = 320   # NSE's attachment-text summaries can run long; a feed row isn't the filing itself


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-days", type=int, help="fetch this many days of real history instead of just yesterday+today")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=args.backfill_days if args.backfill_days else 1)

    try:
        rows = range_announcements(NseClient(), start, today)
    except Exception as e:  # noqa: BLE001 - NSE unreachable must not crash the workflow
        rows = []
        print(f"run_announcements: could not fetch announcements ({e}); keeping the existing history")

    board, names = board_map(), name_lookup()
    fresh = [{
        "seq_id": r["seq_id"],
        "date": r["filed_at"][:10],
        "filed_at": r["filed_at"],
        "symbol": r["symbol"],
        "name": names.get(r["symbol"], r["company"] or r["symbol"]),
        "board_code": board.get(r["symbol"]),
        "category": r["category"],
        "kind": r["kind"],
        "weight": r["weight"],
        "summary": (r["summary"][:SUMMARY_MAX] + "…") if len(r["summary"]) > SUMMARY_MAX else r["summary"],
        "url": r["url"],
    } for r in rows if r["weight"] > 0 and r["seq_id"]]

    try:
        existing = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("announcements") or []
    except (OSError, ValueError):
        existing = []

    merged = {r["seq_id"]: r for r in existing}
    added = sum(1 for r in fresh if r["seq_id"] not in merged)
    merged.update({r["seq_id"]: r for r in fresh})

    cutoff = (today - timedelta(days=RETENTION_DAYS)).isoformat()
    kept = sorted((r for r in merged.values() if r["date"] >= cutoff),
                  key=lambda r: r["filed_at"], reverse=True)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "from_date": kept[-1]["date"] if kept else None,
        "to_date": kept[0]["date"] if kept else None,
        "count": len(kept),
        "board_count": sum(1 for r in kept if r["board_code"]),
        "announcements": kept,
    }, separators=(",", ":")), encoding="utf-8")

    print(f"run_announcements: {len(rows)} fetched, {len(fresh)} material ({added} new), {len(kept)} kept after a "
          f"{RETENTION_DAYS}-day window ({sum(1 for r in kept if r['board_code'])} on board), "
          f"{kept[-1]['date'] if kept else '—'} to {kept[0]['date'] if kept else '—'} -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
