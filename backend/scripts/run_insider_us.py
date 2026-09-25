"""US insider transactions (SEC Form 4) -- the US board's counterpart to
run_deals.py. See app/movers/insider_us.py's own docstring for why this is
a genuinely different feature from India's Bulk & Block Deals (insider
disclosure, not NSE's counterparty-named bulk/block deal disclosure) built
to the closest legitimate free US equivalent, not a straight port.

    cd backend
    python scripts/run_insider_us.py

Runs nightly via .github/workflows/board_disclosures_us.yml. Scoped to the US
board's own companies (data/companies_us_raw.json) -- unlike NSE's single
bulk.csv/block.csv covering the whole exchange, SEC has no "every insider
transaction, market-wide, today" file; each company's Form 4 history has
to be asked for individually. Accumulates across runs the same "merge in,
dedupe, prune the tail" way run_deals.py does, since each run only pulls
each company's most recent handful of Form 4 filings.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.movers.insider_us import cik_map, transactions_for  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW_US = BACKEND_DIR / "data" / "companies_us_raw.json"
OUT_FILE = BACKEND_DIR / "data" / "insider_us" / "latest.json"
RETENTION_DAYS = 120


def _key(r: dict) -> tuple:
    return (r["accession"], r["symbol"], r["insider"], r["code"], r["trade_date"], r["shares"])


def main() -> int:
    records = json.loads(RAW_US.read_text(encoding="utf-8")) if RAW_US.exists() else []
    codes = sorted({r["code"] for r in records if r.get("code")})
    names = {r["code"]: r.get("name") for r in records if r.get("code")}
    if not codes:
        print("run_insider_us: no US board companies yet; nothing to fetch")
        return 0

    try:
        ciks = cik_map(codes)
    except requests.RequestException as e:
        print(f"run_insider_us: could not fetch SEC's ticker->CIK map ({e}); keeping the existing history")
        ciks = {}

    session = requests.Session()
    fresh = []
    for code in codes:
        cik = ciks.get(code.upper())
        if not cik:
            continue
        try:
            found = transactions_for(code, cik, session)
        except requests.RequestException as e:
            print(f"  {code}: could not fetch Form 4 history ({e})")
            continue
        for t in found:
            fresh.append({
                "symbol": t.symbol, "name": names.get(t.symbol, t.symbol),
                "insider": t.insider, "relationship": t.relationship,
                "code": t.code, "label": t.label, "side": t.side, "is_market": t.is_market,
                "shares": t.shares, "price": t.price, "value_usd": round(t.value_usd, 2),
                "trade_date": t.trade_date.isoformat(), "filed_date": t.filed_date.isoformat(),
                "accession": t.accession,
            })

    try:
        existing = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("transactions") or []
    except (OSError, ValueError):
        existing = []

    merged = {_key(r): r for r in existing}
    added = sum(1 for r in fresh if _key(r) not in merged)
    merged.update({_key(r): r for r in fresh})

    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=RETENTION_DAYS)).isoformat()
    rows = sorted((r for r in merged.values() if r["trade_date"] >= cutoff),
                  key=lambda r: (r["trade_date"], r["value_usd"]), reverse=True)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "from_date": rows[-1]["trade_date"] if rows else None,
        "to_date": rows[0]["trade_date"] if rows else None,
        "count": len(rows),
        "companies_checked": len(codes),
        "companies_with_cik": len(ciks),
        "transactions": rows,
    }, separators=(",", ":")), encoding="utf-8")

    print(f"run_insider_us: {len(fresh)} fetched ({added} new) across {len(ciks)}/{len(codes)} board companies "
          f"(CIK found), {len(rows)} kept after a {RETENTION_DAYS}-day window "
          f"({rows[-1]['trade_date'] if rows else '-'} to {rows[0]['trade_date'] if rows else '-'}) -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
