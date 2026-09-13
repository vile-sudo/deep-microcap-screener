"""
Live numbers for the listed companies in each sector report.

    cd backend && python scripts/sector_numbers.py            # every sector
    python scripts/sector_numbers.py --sector oil-exploration

For every company named in a sector's latest edition, reads its public
screener.in page (consolidated when available) and writes
backend/sectors/<slug>/numbers.json: price, market cap, P/E, ROCE, ROE,
operating margin, 3-year sales and profit growth, debt/equity, promoter
holding and the latest quarter's sales growth. Runs daily with the 7 AM job,
so the research text can stay monthly while the numbers stay current.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep_report import fetch, model  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
SECTORS = BACKEND / "sectors"
BOARD = BACKEND / "data" / "companies_raw.json"
IST = timezone(timedelta(hours=5, minutes=30))


def latest_edition(slug: str) -> Path | None:
    eds = sorted(p for p in (SECTORS / slug).glob("????-??.json"))
    return eds[-1] if eds else None


def numbers_for(company: dict) -> dict | None:
    c = fetch.fetch_company([company.get("nse"), company.get("bse")])
    if not c:
        return None
    q = model.build(c)
    y, d, v = q["annual"], q["derived"], q["valuation"]
    last = len(y["columns"]) - 1
    qs = q["quarterly"]
    sh = q["shareholding"]["rows"].get("Promoters") or []
    pick = lambda arr: arr[last] if arr and last >= 0 and last < len(arr) else None  # noqa: E731
    return {
        "screener_url": c["url"], "basis": q["basis"], "price": v.get("price"), "market_cap": v.get("market_cap"),
        "pe": v.get("pe_ttm"), "pb": v.get("pb"), "ev_ebitda": v.get("ev_ebitda_ttm"),
        "roce": pick(y["roce"]), "roe": pick(d["roe"]), "opm": pick(y["opm"]),
        "sales": (y.get("ttm") or {}).get("sales") or pick(y["sales"]), "pat": (y.get("ttm") or {}).get("pat") or pick(y["pat"]),
        "sales_cagr_3y": d["growth"].get("sales_3y"), "profit_cagr_3y": d["growth"].get("pat_3y"),
        "debt_equity": pick(d["debt_equity"]), "promoter": sh[-1] if sh else None,
        "latest_quarter": q["latest_quarter"], "quarter_sales_yoy": (qs["sales_yoy"] or [None])[-1],
        "from_52w_high": v.get("from_high_pct"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sector")
    args = ap.parse_args()
    board = {}
    for r in json.loads(BOARD.read_text(encoding="utf-8")):
        for k in ("code", "nse_code", "bse_code"):
            if r.get(k):
                board[str(r[k]).upper()] = r["code"]
    slugs = [args.sector] if args.sector else [p.name for p in SECTORS.iterdir() if p.is_dir()]
    failed = 0
    for slug in slugs:
        ed = latest_edition(slug)
        if not ed:
            continue
        report = json.loads(ed.read_text(encoding="utf-8"))
        out = {"as_of": datetime.now(IST).isoformat(timespec="minutes"), "edition": ed.stem, "companies": {}}
        for co in report.get("companies", []):
            key = co.get("nse") or co.get("bse")
            if not key:
                continue
            try:
                n = numbers_for(co)
            except Exception as e:  # noqa: BLE001 - one page failing must not stop the rest
                print(f"  {slug}/{key}: {str(e)[:100]}")
                n, failed = None, failed + 1
            if n:
                n["board_code"] = board.get(str(co.get("nse") or "").upper()) or board.get(str(co.get("bse") or "").upper())
                out["companies"][key] = n
        (SECTORS / slug / "numbers.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"sector numbers: {slug} {len(out['companies'])} of {len(report.get('companies', []))} companies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
