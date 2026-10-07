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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.movers.announcements import classify, range_announcements  # noqa: E402
from app.movers.companies import board_map, name_lookup  # noqa: E402
from app.movers.nse import NseClient  # noqa: E402
from app.movers.order_size import (FilingMoves, SalesLookup, execution_months, not_an_order,  # noqa: E402
                                   order_value_cr, pdf_text)

BACKEND_DIR = Path(__file__).resolve().parent.parent
OUT_FILE = BACKEND_DIR / "data" / "announcements" / "latest.json"
SALES_CACHE = BACKEND_DIR / "data" / "announcements" / "sales_cache.json"
RETENTION_DAYS = 45
SUMMARY_MAX = 320   # NSE's attachment-text summaries can run long; a feed row isn't the filing itself
MAX_ORDER_PDFS = 60  # per run, newest first -- bounds the first run's backlog; the rest are sized on later runs
ORDER_V = 2          # bump to re-read every order PDF once (2: adds the execution period)
KEPT_PREFIXES = ("order_", "sales_", "not_order", "move_")   # fields earned on an earlier run, kept across re-fetches


def size_orders(rows: list[dict]) -> int:
    """Read each order-win filing's PDF once per ORDER_V: value, execution period, share of last
    full-year sales (whole order and per year), and whether it is really an order (arbitration
    awards and tax orders get filed under the order category too)."""
    sales = SalesLookup(SALES_CACHE)
    done = 0
    for r in rows:
        if r["kind"] != "order win" or r.get("order_v") == ORDER_V or done >= MAX_ORDER_PDFS:
            continue
        done += 1
        for k in [k for k in r if k.startswith(("order_", "sales_"))]:
            del r[k]
        r["order_checked"], r["order_v"] = True, ORDER_V
        text = pdf_text(r.get("url") or "")
        if not text.strip():
            continue
        if not_an_order(text):
            r["not_order"] = True
            r["kind"], r["weight"] = "regulatory or legal", 4
            continue
        value = order_value_cr(text)
        if value is None:
            continue
        r["order_cr"] = round(value, 2)
        months = execution_months(text, date.fromisoformat(r["date"]))
        if months:
            r["order_months"] = round(months, 1)
        hit = sales.last_full_year(r["symbol"], r["date"])
        if hit:
            r["sales_cr"], r["sales_fy"] = round(hit[0], 1), hit[1]
            r["order_pct"] = round(100 * value / hit[0], 1)
            # an order delivered over 4 years is a quarter of its size per year; one inside a year is all of it
            r["order_annual_pct"] = round(r["order_pct"] * 12 / max(12.0, months or 12.0), 1)
    sales.save()
    return done


def fill_moves(rows: list[dict]) -> int:
    """Filing-session move for each order win whose session has settled."""
    ist_today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    moves, filled = FilingMoves(ist_today), 0
    for r in rows:
        if r["kind"] != "order win" or "move_date" in r:
            continue
        m = moves.move(r["symbol"], r["filed_at"])
        if m:
            r.update(m)
            filled += 1
    return filled


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
    for r in fresh:   # keep the order-size fields a filing already earned on an earlier run
        old = merged.get(r["seq_id"]) or {}
        merged[r["seq_id"]] = {**{k: v for k, v in old.items() if k.startswith(KEPT_PREFIXES)}, **r}

    # Re-label the whole history with the current classifier, so a rule change doesn't leave weeks of
    # old labels behind; a PDF that showed a filing isn't really an order outranks its category.
    for r in merged.values():
        r["kind"], r["weight"] = classify(r["category"], r["summary"])
        if r.get("not_order"):
            r["kind"], r["weight"] = "regulatory or legal", 4

    cutoff = (today - timedelta(days=RETENTION_DAYS)).isoformat()
    kept = sorted((r for r in merged.values() if r["date"] >= cutoff and r["weight"] > 0),
                  key=lambda r: r["filed_at"], reverse=True)
    sized = size_orders(kept)
    moved = fill_moves(kept)

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
          f"{kept[-1]['date'] if kept else '—'} to {kept[0]['date'] if kept else '—'} -> {OUT_FILE}; "
          f"{sized} order filing(s) read for size, {moved} filing-day move(s) filled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
