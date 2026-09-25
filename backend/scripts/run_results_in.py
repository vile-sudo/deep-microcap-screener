"""Quarterly and annual results, and the concall list, for every company on the India board.

    cd backend
    python scripts/run_results_in.py                    # every board company
    python scripts/run_results_in.py --codes BEL,ACCENTMIC

Reads each company's public screener.in page (the consolidated one when it exists, else standalone)
and keeps, into data/results_in/latest.json (served at GET /api/results-in):

  * quarters  the last 12 quarters: sales, operating profit, OPM %, other income, interest, PBT, net
              profit, EPS  (the scorecard shows eight, with year-on-year change, which needs twelve)
  * annual    the last 8 fiscal years of the same lines -- what management's full-year targets are
              checked against (scripts/run_guidance_in.py)
  * concalls  the earnings-call list: month, transcript PDF, presentation, recording

Figures are screener.in's own, in Rs crore. A company whose page has no quarterly table (a fresh
listing) simply has no entry. A page that cannot be fetched keeps its previous entry, so one bad
night never blanks the scorecard. Personal-use scraper of publicly rendered pages, paced 1.2 s apart;
if screener.in starts refusing (403/429) the run stops and keeps what it has.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW = BACKEND_DIR / "data" / "companies_raw.json"
OUT_FILE = BACKEND_DIR / "data" / "results_in" / "latest.json"
BASE = "https://www.screener.in/company/{code}/{suffix}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-research-screener/1.0; +https://pkresearch.in)"}
DELAY = 1.2
KEEP_QUARTERS, KEEP_YEARS, KEEP_CALLS = 12, 8, 8
MAX_CONSECUTIVE_BLOCKS = 4

LINES = {                       # screener row label (prefix, lower case) -> our key
    "sales": "sales", "expenses": "expenses", "operating profit": "operating_profit", "opm %": "opm",
    "other income": "other_income", "interest": "interest", "depreciation": "depreciation",
    "profit before tax": "pbt", "tax %": "tax_pct", "net profit": "net_profit", "eps in rs": "eps",
    "dividend payout %": "payout_pct",
}


def _num(text: str):
    t = (text or "").replace(",", "").replace("%", "").strip()
    if not t or t in ("-", "—"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _table(section) -> dict | None:
    """{'periods': [...], key: [...]} from a screener data-table (quarters or profit-loss)."""
    if section is None:
        return None
    t = section.select_one("table.data-table")
    if t is None:
        return None
    periods = [th.get_text(strip=True) for th in t.select("thead th")][1:]
    out = {"periods": periods}
    for tr in t.select("tbody tr"):
        cells = tr.select("td")
        if not cells:
            continue
        label = re.sub(r"[+\s]+$", "", cells[0].get_text(" ", strip=True)).lower()
        key = next((v for k, v in LINES.items() if label == k or label.startswith(k)), None)
        if key and key not in out:
            out[key] = [_num(c.get_text(strip=True)) for c in cells[1:]]
    return out if len(periods) >= 2 and "sales" in out else None


def parse(html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    q = _table(soup.select_one("#quarters"))
    if not q:
        return None
    a = _table(soup.select_one("#profit-loss"))
    if a:                                              # drop the TTM column: not a fiscal year
        keep = [i for i, p in enumerate(a["periods"]) if p.upper() != "TTM"]
        a = {k: [v[i] for i in keep if i < len(v)] for k, v in a.items()}
    n = KEEP_QUARTERS
    quarters = {k: v[-n:] for k, v in q.items()}
    annual = {k: v[-KEEP_YEARS:] for k, v in (a or {}).items()}
    calls = []
    for li in soup.select("#documents .documents.concalls li"):
        date = re.match(r"\s*([A-Z][a-z]{2}\s+\d{4})", li.get_text(" ", strip=True))
        links = {a_.get_text(strip=True).lower(): a_["href"] for a_ in li.select("a[href]")}
        if date and links.get("transcript"):
            calls.append({"date": date.group(1), "transcript": links["transcript"], "ppt": links.get("ppt")})
    return {"quarters": quarters, "annual": annual, "concalls": calls[:KEEP_CALLS]}


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _last_quarter(entry: dict) -> tuple:
    """(year, month) of the newest quarter in an entry, (0, 0) if unreadable."""
    p = (entry["quarters"].get("periods") or [""])[-1]
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", p)
    return (int(m.group(2)), MONTHS.get(m.group(1).lower(), 0)) if m else (0, 0)


def fetch(rec: dict, session: requests.Session):
    """(entry, blocked). Tries the codes screener may file the company under. Consolidated figures are
    preferred, but a company whose consolidated page stopped updating (its newest quarter more than ~200
    days old) is read from the standalone page too, and whichever is newer wins."""
    now = datetime.now(timezone.utc)
    stale = (now.year, now.month - 7) if now.month > 7 else (now.year - 1, now.month + 5)
    seen = []
    for cand in (rec.get("nse_code"), rec.get("bse_code"), rec.get("code")):
        cand = str(cand or "").strip()
        if not cand or cand in seen:
            continue
        seen.append(cand)
        found = None
        for suffix, basis in (("consolidated/", "consolidated"), ("", "standalone")):
            r = session.get(BASE.format(code=cand, suffix=suffix), timeout=30)
            if r.status_code in (403, 429):
                return None, True
            entry = parse(r.text) if r.status_code == 200 else None
            if entry:
                entry["basis"], entry["screener_code"] = basis, cand
                if found is None or _last_quarter(entry) > _last_quarter(found):
                    found = entry
                if _last_quarter(found) >= stale:
                    break                                   # fresh enough: no need for the other basis
            time.sleep(0.6)
        if found:
            return found, False
    return None, False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    args = ap.parse_args()

    companies = json.loads(RAW.read_text(encoding="utf-8"))
    if args.codes:
        want = {c.strip().upper() for c in args.codes.split(",") if c.strip()}
        companies = [c for c in companies if str(c.get("code", "")).upper() in want]
    try:
        prev = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("companies") or {}
    except (OSError, ValueError):
        prev = {}

    session = requests.Session()
    session.headers.update(HEADERS)
    out = dict(prev)
    ok = failed = blocks = 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i, rec in enumerate(companies, 1):
        code = rec["code"]
        try:
            entry, blocked = fetch(rec, session)
        except requests.RequestException as e:
            print(f"  {code}: {e}")
            entry, blocked = None, False
        if blocked:
            blocks += 1
            time.sleep(20)
            if blocks >= MAX_CONSECUTIVE_BLOCKS:
                print("run_results_in: screener.in is refusing requests; stopping and keeping what we have")
                break
            continue
        blocks = 0
        if entry:
            out[code] = entry
            ok += 1
        else:
            failed += 1
        time.sleep(DELAY)
        if i % 100 == 0:
            print(f"  ...{i}/{len(companies)}")

    board = {str(c["code"]) for c in json.loads(RAW.read_text(encoding="utf-8"))}
    out = {k: v for k, v in out.items() if k in board}
    if out == prev:
        print(f"run_results_in: {ok} checked, nothing changed since the last run; {len(out)} companies on file")
        return 0
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({"fetched_at": now, "companies": out}, separators=(",", ":")), encoding="utf-8")
    print(f"run_results_in: {ok} refreshed, {failed} without a quarterly table; {len(out)} companies on file (file updated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
