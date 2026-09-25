"""What every listed Indian company actually makes -- the index behind "Indian beneficiaries" in the News Channel.

    cd backend
    python scripts/run_product_index.py                 # add/refresh up to --budget companies
    python scripts/run_product_index.py --budget 1200
    python scripts/run_product_index.py --symbols BODALCHEM,KIRIINDUS,BHAGERIA,524336

A news item like "China hydrochloric acid price up 500% as plants shut" only becomes useful once
you know which listed Indian companies sit on that product. The board's own 455 companies are too
few for that (none of Bodal Chemicals, Shree Hari Chemicals Export, Kiri Industries or Bhageria
Industries is on it), so this builds a wider index from each company's public screener.in page:
the "About" text, the sector / industry classification (sector > industry group > industry >
sub-industry, e.g. Chemicals & Petrochemicals > Dyes And Pigments), market cap and price.
Written to data/product_index/companies.json; app/beneficiaries.py matches news against it.

The universe is every NSE/BSE company the chart job already tracks (chart_data/universe.json,
~4,300 names). A company is looked up once, then refreshed every REFRESH_DAYS; each run does at most
--budget lookups (about 1.2 s apart, screener.in asks for politeness), so the first fill takes a few
daily runs and afterwards each run is a handful of pages. A page that 404s is not retried for
RETRY_DAYS. If screener.in starts refusing (403/429) the run stops and keeps what it has -- an
interrupted run loses nothing, progress is saved as it goes.

Personal-use scraper of publicly rendered pages, like scripts/refresh_data.py; it can break if
screener.in changes its markup.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BACKEND_DIR = Path(__file__).resolve().parent.parent
UNIVERSE = BACKEND_DIR / "chart_data" / "universe.json"
OUT_FILE = BACKEND_DIR / "data" / "product_index" / "companies.json"
BASE = "https://www.screener.in/company/{sym}/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-research-screener/1.0; +https://pkresearch.in)"}
DELAY = 1.2
REFRESH_DAYS = 90
RETRY_DAYS = 30
REFRESH_BATCH = 150
ABOUT_MAX = 900
MAX_CONSECUTIVE_BLOCKS = 4


def universe_symbols() -> list[str]:
    """What screener.in's URL takes for each company the chart job tracks: the NSE symbol, or the BSE
    scrip code for BSE-only names (the key of a chart-universe entry is "NSE-<symbol>" or "BSE-<code>";
    board companies are "BOARD-<code>", the code being whichever of the two the board uses)."""
    try:
        stocks = json.loads(UNIVERSE.read_text(encoding="utf-8")).get("stocks") or {}
    except (OSError, ValueError):
        return []
    out, seen = [], set()
    for k in stocks:
        code = k.split("-", 1)[1] if "-" in k else k
        if code and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def parse(html: str, sym: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    name_el = soup.select_one("h1")
    if not name_el:
        return None
    about_el = soup.select_one(".company-profile .about") or soup.select_one("div.about")
    about = re.sub(r"\s*\[\d+\]", "", about_el.get_text(" ", strip=True)) if about_el else ""
    # sector > industry group > industry > sub-industry, from the classification links above the peers table
    path = []
    for a in soup.select('#peers a[href*="/market/"]'):
        t = a.get_text(" ", strip=True)
        if t and t not in path:
            path.append(t)
    ratios = {}
    for li in soup.select(".company-ratios li"):
        nm = li.select_one(".name")
        val = li.select_one(".value")
        if nm and val:
            ratios[nm.get_text(strip=True)] = val.get_text(" ", strip=True)

    def num(key):
        m = re.search(r"[\d,]+\.?\d*", ratios.get(key, ""))
        return float(m.group(0).replace(",", "")) if m else None

    if not about and not path:
        return None
    return {
        "symbol": sym, "name": name_el.get_text(" ", strip=True), "industry": path[:4], "about": about[:ABOUT_MAX],
        "mcap_cr": num("Market Cap"), "price": num("Current Price"),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=1200)
    ap.add_argument("--symbols", default="", help="comma-separated symbols/codes to (re)fetch now, ignoring freshness")
    args = ap.parse_args()

    try:
        data = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    companies = data.get("companies") or {}
    missing = data.get("missing") or {}

    now = datetime.now(timezone.utc)
    stale = (now - timedelta(days=REFRESH_DAYS)).isoformat(timespec="seconds")
    retry_before = (now - timedelta(days=RETRY_DAYS)).isoformat(timespec="seconds")
    if args.symbols:
        todo = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        uni = universe_symbols()
        new = [s for s in uni if s not in companies and (missing.get(s) or "") < retry_before]
        old = sorted((s for s in uni if s in companies and (companies[s].get("fetched_at") or "") < stale),
                     key=lambda s: companies[s].get("fetched_at") or "")
        # Refreshing is done in batches (a company's About text rarely changes, and every run that writes
        # rewrites the whole ~5 MB file into git): once the index is complete, wait until enough have gone stale.
        if not new and len(old) < REFRESH_BATCH:
            old = []
        todo = (new + old)[: args.budget]
    if not todo:
        print("run_product_index: nothing to fetch; the index is complete and fresh")
        return 0

    session = requests.Session()
    session.headers.update(HEADERS)
    ok = bad = blocks = 0

    def save():
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUT_FILE.write_text(json.dumps({"fetched_at": now.isoformat(timespec="seconds"), "count": len(companies),
                                        "companies": companies, "missing": missing}, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    for i, sym in enumerate(todo, 1):
        try:
            for suffix in ("", "consolidated/"):          # consolidated pages carry the fuller About/ratios when they exist
                r = session.get(BASE.format(sym=sym) + suffix, timeout=25)
                if r.status_code == 200:
                    break
            if r.status_code in (403, 429):
                blocks += 1
                print(f"  {sym}: HTTP {r.status_code}")
                if blocks >= MAX_CONSECUTIVE_BLOCKS:
                    print("run_product_index: screener.in is refusing requests; stopping and keeping what we have")
                    break
                time.sleep(20)
                continue
            blocks = 0
            entry = parse(r.text, sym) if r.status_code == 200 else None
            if entry:
                companies[sym] = entry
                missing.pop(sym, None)
                ok += 1
            else:
                missing[sym] = now.isoformat(timespec="seconds")
                bad += 1
        except requests.RequestException as e:
            print(f"  {sym}: {e}")
        if i % 50 == 0:
            save()
        time.sleep(DELAY)
    save()
    print(f"run_product_index: {ok} fetched, {bad} without a usable page; index now holds {len(companies)} companies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
