"""FII/FPI & DII daily cash-market flow -- NSE's own combined report (app/movers/fii_dii.py).

    cd backend
    python scripts/run_fii_dii.py

NSE's own API only ever serves the latest session's two rows (like bulk.csv/block.csv), so this
accumulates its own rolling history across runs, same "merge in, dedupe by date, prune the tail" shape as
run_deals.py. Writes backend/data/fii_dii/latest.json: {"fetched_at", "count", "rows": [{"date",
"fii_buy_cr", "fii_sell_cr", "fii_net_cr", "dii_buy_cr", "dii_sell_cr", "dii_net_cr"}, ...]}, newest first.

Runs in .github/workflows/daily.yml (the permanent nightly record) and deals_evening.yml (NSE's own
"provisional" figure is end-of-day data, same timing profile as bulk/block deals, so the same-evening
re-run gets it to the dashboard same-day instead of the next morning).
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
from app.movers.fii_dii import fetch_latest, open_session  # noqa: E402

OUT_FILE = BACKEND_DIR / "data" / "fii_dii" / "latest.json"
RETENTION_DAYS = 120


def main() -> int:
    try:
        session = open_session()
        row = fetch_latest(session)
    except Exception as e:  # noqa: BLE001 - NSE unreachable must not crash the workflow
        print(f"run_fii_dii: could not fetch FII/DII flow ({e}); keeping the existing history")
        return 0
    if row is None:
        print("run_fii_dii: NSE's response did not parse; keeping the existing history")
        return 0

    try:
        existing = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("rows") or []
    except (OSError, ValueError):
        existing = []

    fresh = {"date": row.trade_date.isoformat(), **{k: round(v, 2) for k, v in asdict(row).items() if k != "trade_date"}}
    merged = {r["date"]: r for r in existing}
    added = fresh["date"] not in merged
    merged[fresh["date"]] = fresh

    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=RETENTION_DAYS)).isoformat()
    rows = sorted((r for r in merged.values() if r["date"] >= cutoff), key=lambda r: r["date"], reverse=True)

    payload = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "count": len(rows), "rows": rows}
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"run_fii_dii: {fresh['date']} ({'new' if added else 'already on file'}): "
          f"FII net {fresh['fii_net_cr']:+.0f} cr, DII net {fresh['dii_net_cr']:+.0f} cr; "
          f"{len(rows)} sessions kept after a {RETENTION_DAYS}-day window")
    return 0


if __name__ == "__main__":
    sys.exit(main())
