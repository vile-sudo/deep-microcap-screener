"""US corporate announcements (SEC Form 8-K) -- the US board's counterpart
to run_announcements.py. See app/movers/announcements_us.py's docstring:
Form 8-K is genuinely closer to a straight port of NSE's corporate-
announcements feed than Form 4 was for Bulk & Block Deals, since 8-K IS
the US "material corporate event" disclosure and SEC gives structured
item codes rather than free text to classify.

    cd backend
    python scripts/run_announcements_us.py

Runs nightly via .github/workflows/board_disclosures_us.yml (same
workflow as the insider-transactions scan -- both are lightweight SEC
EDGAR pulls scoped to the board, no reason to duplicate the cron/commit/
deploy boilerplate across two workflows). Scoped to the US board's own companies
(data/companies_us_raw.json), same reason as run_insider_us.py: SEC has
no single "every 8-K, market-wide, today" file the way NSE gives one
corporate-announcements API call for the whole exchange. Accumulates
across runs the same "merge in, dedupe, prune the tail" way
run_announcements.py does.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.movers.announcements_us import filings_for  # noqa: E402
from app.movers.insider_us import cik_map  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW_US = BACKEND_DIR / "data" / "companies_us_raw.json"
OUT_FILE = BACKEND_DIR / "data" / "announcements_us" / "latest.json"
RETENTION_DAYS = 60   # "last two months" -- longer than India's 45-day window (a much lighter,
                      # per-company fetch here, so keeping more history costs nothing extra)


def _key(r: dict) -> tuple:
    return (r["accession"], r["symbol"])


def main() -> int:
    records = json.loads(RAW_US.read_text(encoding="utf-8")) if RAW_US.exists() else []
    codes = sorted({r["code"] for r in records if r.get("code")})
    names = {r["code"]: r.get("name") for r in records if r.get("code")}
    if not codes:
        print("run_announcements_us: no US board companies yet; nothing to fetch")
        return 0

    try:
        ciks = cik_map(codes)
    except requests.RequestException as e:
        print(f"run_announcements_us: could not fetch SEC's ticker->CIK map ({e}); keeping the existing history")
        ciks = {}

    session = requests.Session()
    fresh = []
    for code in codes:
        cik = ciks.get(code.upper())
        if not cik:
            continue
        try:
            found = filings_for(code, names.get(code, code), cik, session)
        except requests.RequestException as e:
            print(f"  {code}: could not fetch 8-K history ({e})")
            continue
        for f in found:
            fresh.append({
                "symbol": f.symbol, "name": f.company, "items": f.items,
                "category": f.category, "weight": f.weight, "summary": f.summary,
                "date": f.filed_at.isoformat(), "url": f.url, "accession": f.accession,
            })

    try:
        existing = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("announcements") or []
    except (OSError, ValueError):
        existing = []

    merged = {_key(r): r for r in existing}
    added = sum(1 for r in fresh if _key(r) not in merged)
    merged.update({_key(r): r for r in fresh})

    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=RETENTION_DAYS)).isoformat()
    rows = sorted((r for r in merged.values() if r["date"] >= cutoff),
                  key=lambda r: (r["date"], r["weight"]), reverse=True)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "from_date": rows[-1]["date"] if rows else None,
        "to_date": rows[0]["date"] if rows else None,
        "count": len(rows),
        "companies_checked": len(codes),
        "companies_with_cik": len(ciks),
        "announcements": rows,
    }, separators=(",", ":")), encoding="utf-8")

    print(f"run_announcements_us: {len(fresh)} fetched ({added} new) across {len(ciks)}/{len(codes)} board companies "
          f"(CIK found), {len(rows)} kept after a {RETENTION_DAYS}-day window "
          f"({rows[-1]['date'] if rows else '-'} to {rows[0]['date'] if rows else '-'}) -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
