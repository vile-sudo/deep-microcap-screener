"""
Refreshes the quantitative fields in data/companies_raw.json from public
screener.in company pages, then leaves the file ready for `python -m
app.seed` to reload into the database.

WHY THIS EXISTS
----------------
The qualitative research on this board (business description, moat notes,
risk notes, import-substitution analysis, ...) came from manual research —
including, in the original project, Claude sessions using the Screener and
Trendlyne MCP servers interactively. MCP tools only run inside an
Claude/Claude Code session; a deployed web app has no access to them. This
script is the standalone equivalent for the *quantitative* fields that
change every quarter (price, P/E, ROCE, ROE, promoter/FII/DII holding,
market cap, shareholder count) -- the numbers you'd otherwise re-pull by
hand from screener.in.

It deliberately does NOT touch the qualitative fields (business, moat_note,
pricing_power_note, risk_note, import_substitution, score breakdowns, ...).
Re-scoring a company from scratch is research work, not a data refresh --
do that the same way the original board was built (see PROJECT_NOTES.md),
then hand-edit that company's record or add a new one.

WHAT IT PULLS
--------------
For each company code in companies_raw.json, it fetches the public
screener.in company page (https://www.screener.in/company/<code>/) and
reads figures out of the "Quick ratios" panel and the "Shareholding
pattern" table that page renders without login. It does not use
screener.in's paid export/API, and it is not affiliated with screener.in --
this is a personal-use scraper against publicly rendered HTML, which can
break if screener.in changes its page structure. Treat it as a starting
point, and check a handful of results by hand after the first run.

USAGE
------
    python scripts/refresh_data.py                  # refresh every company
    python scripts/refresh_data.py --codes BEL,VIMTALABS   # just these
    python scripts/refresh_data.py --dry-run         # print, don't write
    python -m app.seed                               # reload into the DB

TRENDLYNE
----------
Trendlyne's data requires an authenticated API/subscription -- there is no
public, unauthenticated page to scrape responsibly. `fetch_trendlyne()`
below is a stub: plug in your own API key and endpoint if you have
Trendlyne API access, following their published API documentation.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "companies_raw.json"
BASE_URL = "https://www.screener.in/company/{code}/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; personal-research-screener/1.0; "
                  "+https://github.com/) research script, not a bulk crawler"
}
REQUEST_DELAY_SECONDS = 2.0  # be polite -- this is someone else's server


def _num(text):
    """'₹1,234.5 Cr.' / '12.3%' / '—' -> float, or None."""
    if text is None:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text)
    if not cleaned or cleaned in ("-", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_screener(code: str) -> dict:
    """Best-effort pull of the public quick-ratios + shareholding numbers
    for one company. Returns a dict with only the keys it could find --
    caller is responsible for merging into the existing record.
    """
    url = BASE_URL.format(code=code)
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    out = {}

    # "Quick ratios" -- a <ul> of <li><span class="name">Label</span><span class="number">Value</span></li>
    for li in soup.select("#top-ratios li"):
        name_el, val_el = li.select_one(".name"), li.select_one(".number")
        if not name_el or not val_el:
            continue
        label = name_el.get_text(strip=True).lower()
        value = _num(val_el.get_text(strip=True))
        if value is None:
            continue
        if "market cap" in label:
            out["market_cap_cr"] = value
        elif label == "current price":
            out["price"] = value
        elif "stock p/e" in label:
            out["pe"] = value
        elif "roce" in label:
            out["roce_pct"] = value
        elif "roe" in label:
            out["roe_pct"] = value

    # Shareholding pattern -- the most recent (rightmost) quarter column.
    sh_table = soup.select_one("#shareholding table")
    if sh_table:
        for row in sh_table.select("tbody tr"):
            cells = row.select("td")
            if len(cells) < 2:
                continue
            label = cells[0].get_text(strip=True).lower()
            value = _num(cells[-1].get_text(strip=True))
            if value is None:
                continue
            if label.startswith("promoter"):
                out["promoter_pct"] = value
            elif "fii" in label:
                out["fii_pct"] = value
            elif "dii" in label:
                out["dii_pct"] = value
            elif "public" in label:
                out["public_pct"] = value

    num_el = soup.select_one("#num-shareholders .number, .shareholders .number")
    if num_el:
        n = _num(num_el.get_text(strip=True))
        if n is not None:
            out["num_shareholders"] = int(n)

    return out


def fetch_trendlyne(code: str) -> dict:
    """Stub -- plug in Trendlyne API access here if you have it.

    Trendlyne has no public unauthenticated data endpoint, so this returns
    {} until you fill in your own API key + request, e.g.:

        resp = requests.get(
            "https://trendlyne.com/api/...",
            headers={"Authorization": f"Bearer {TRENDLYNE_API_KEY}"},
            params={"symbol": code},
        )
    """
    return {}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codes", help="Comma-separated company codes to refresh (default: all)")
    ap.add_argument("--dry-run", action="store_true", help="Print what would change, write nothing")
    ap.add_argument("--limit", type=int, default=None, help="Stop after N companies (useful for testing)")
    args = ap.parse_args()

    companies = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    by_code = {c["code"]: c for c in companies if c.get("code")}

    targets = list(by_code.keys())
    if args.codes:
        wanted = {c.strip() for c in args.codes.split(",") if c.strip()}
        targets = [c for c in targets if c in wanted]
    if args.limit:
        targets = targets[: args.limit]

    print(f"Refreshing {len(targets)} of {len(companies)} companies from screener.in ...")
    changed = 0
    for i, code in enumerate(targets, 1):
        try:
            fresh = fetch_screener(code)
            fresh.update(fetch_trendlyne(code))
        except requests.RequestException as e:
            print(f"  [{i}/{len(targets)}] {code}: FAILED ({e})", file=sys.stderr)
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        rec = by_code[code]
        diffs = {k: (rec.get(k), v) for k, v in fresh.items() if rec.get(k) != v}
        if diffs:
            changed += 1
            print(f"  [{i}/{len(targets)}] {code}: {len(diffs)} field(s) changed")
            if not args.dry_run:
                rec.update(fresh)
        else:
            print(f"  [{i}/{len(targets)}] {code}: no change")

        time.sleep(REQUEST_DELAY_SECONDS)

    if args.dry_run:
        print(f"\nDry run: {changed} companies would change. Nothing written.")
        return

    if changed:
        DATA_FILE.write_text(json.dumps(companies, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote {changed} updated companies to {DATA_FILE}.")
        print("Now run: python -m app.seed")
    else:
        print("\nNo changes -- nothing written.")


if __name__ == "__main__":
    main()
