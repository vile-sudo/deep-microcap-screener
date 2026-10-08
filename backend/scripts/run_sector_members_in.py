"""Who belongs to each Sector Strength sector -- every listed company, main board and SME.

    cd backend
    python scripts/run_sector_members_in.py

For each sector in app/sector_strength.py, every page of screener.in's listing of the NSE basic
industries it covers (/market/<...>/<basic industry>/, 50 companies a page): the company's code
(NSE symbol, or BSE scrip code for a BSE-only listing), name, sub-industry and market cap. Plus
NSE's SME list (to mark SME listings) and the Nifty 500 constituents (the comparison row).

Writes data/sector_strength/members.json. Run weekly by the charts workflow; a sector whose pages can't
be read keeps its previous members, so a bad night never empties a sector. Paced 1.5 s per request.
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.sector_strength import MEMBERS_FILE, SECTORS  # noqa: E402

SCREENER = "https://www.screener.in/market/{path}/?limit=50&page={page}"
SCREENER_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-research-screener/1.0; +https://pkresearch.in)"}
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
NSE_SME_LIST = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
NIFTY500_LIST = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"
DELAY = 1.5
MAX_PAGES = 40


def _num(t: str):
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


def industry(s: requests.Session, path: str, label: str) -> list[dict] | None:
    out = []
    for page in range(1, MAX_PAGES + 1):
        r = s.get(SCREENER.format(path=path, page=page), headers=SCREENER_HEADERS, timeout=30)
        if r.status_code in (403, 429):
            raise RuntimeError("screener.in is refusing requests")
        if r.status_code != 200:
            return None if page == 1 else out
        soup = BeautifulSoup(r.text, "html.parser")
        t = soup.select_one("table.data-table")
        heads = [th.get_text(" ", strip=True) for th in t.select("tr th")] if t else []
        mcap_i = next((i for i, h in enumerate(heads) if h.lower().startswith("mar cap")), None)
        got = 0
        for tr in (t.select("tr") if t else []):
            a = tr.select_one("a[href^='/company/']")
            tds = tr.select("td")
            if not a or not tds:
                continue
            code = a["href"].split("/")[2]
            mcap = _num(tds[mcap_i].get_text(strip=True)) if mcap_i is not None and mcap_i < len(tds) else None
            out.append({"code": code, "name": a.get_text(" ", strip=True), "industry": label, "mcap_cr": mcap})
            got += 1
        time.sleep(DELAY)
        if not got or not soup.select_one(f"a[href*='page={page + 1}']"):
            break
    return out


def nse_list(url: str, col: str) -> list[str]:
    try:
        r = requests.get(url, headers=BROWSER, timeout=30)
        r.raise_for_status()
        rows = csv.DictReader(io.StringIO(r.content.decode("utf-8-sig", "replace")))
        return sorted({(row.get(col) or "").strip() for row in rows if (row.get(col) or "").strip()})
    except (requests.RequestException, csv.Error) as e:
        print(f"  could not read {url}: {e}")
        return []


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--if-older-than", type=float, default=0, help="days: skip when the list is fresher than this")
    args = ap.parse_args()
    prev = {}
    try:
        prev = json.loads(MEMBERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if args.if_older_than and prev.get("updated"):
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(prev["updated"])).total_seconds() / 86400
        if age < args.if_older_than:
            print(f"run_sector_members_in: list is {age:.1f} days old; nothing to do")
            return 0
    sectors = dict(prev.get("sectors") or {})
    s = requests.Session()
    for sec in SECTORS:
        rows, ok = [], True
        try:
            for path, label in sec["industries"]:
                got = industry(s, path, label)
                if got is None:
                    ok = False
                    print(f"  {sec['name']}: {label} could not be read")
                    continue
                rows += got
        except RuntimeError as e:
            print(f"run_sector_members_in: {e}; keeping the previous lists for the rest")
            break
        seen, uniq = set(), []
        for r in rows:                      # a company listed under two of the sector's industries counts once
            if r["code"] not in seen:
                seen.add(r["code"])
                uniq.append(r)
        if uniq and (ok or len(uniq) >= 0.8 * len(sectors.get(sec["slug"], []))):
            sectors[sec["slug"]] = sorted(uniq, key=lambda r: -(r["mcap_cr"] or 0))
        print(f"  {sec['name']}: {len(uniq)} companies")
    sme = nse_list(NSE_SME_LIST, "SYMBOL") or prev.get("sme") or []
    n500 = nse_list(NIFTY500_LIST, "Symbol") or prev.get("nifty500") or []
    out = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sectors": sectors, "sme": sme, "nifty500": n500}
    MEMBERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMBERS_FILE.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"run_sector_members_in: {sum(len(v) for v in sectors.values())} members; {len(sme)} NSE SME symbols; {len(n500)} Nifty 500")
    return 0


if __name__ == "__main__":
    sys.exit(main())
