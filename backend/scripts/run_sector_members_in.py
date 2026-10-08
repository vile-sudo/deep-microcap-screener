"""Who belongs to each Sector Strength sector -- every listed company, main board and SME.

    cd backend
    python scripts/run_sector_members_in.py

For each sector in app/sector_strength.py, every page of screener.in's listing of the NSE basic
industries it covers (/market/<...>/<basic industry>/, 50 companies a page): the company's code
(NSE symbol, or BSE scrip code for a BSE-only listing), name, sub-industry and market cap. Plus
NSE's SME list (to mark SME listings) and the Nifty 500 constituents (the comparison row).

Screener's industry listings leave out many small and newly listed companies, so a second pass reads
the company page of every recent listing (first traded within about a year, from the bhavcopy cache)
and, for a sector with name keywords (Jewellery), every traded company whose name matches, and adds
those NSE files under one of the sector's industries. Each company is classified once; an unclassified
new listing is looked at again a week later.

Writes data/sector_strength/members.json. The industry listings are re-read weekly, the second pass runs daily; a sector whose pages can't
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
from app.sector_strength import INDUSTRIES_FILE, MEMBERS_FILE, sectors  # noqa: E402

SCREENER = "https://www.screener.in/market/{path}/?limit=50&page={page}"
SCREENER_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-research-screener/1.0; +https://pkresearch.in)"}
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
NSE_SME_LIST = "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
NIFTY500_LIST = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"
DELAY = 1.5
SUPPLEMENT_BUDGET = 400   # company pages read per run (new listings, name matches); the rest next run
MAX_PAGES = 40


def _num(t: str):
    try:
        return float(t.replace(",", ""))
    except ValueError:
        return None


def industry_index(s: requests.Session) -> list | None:
    """Every NSE basic industry screener.in lists: [(path, label)], from its /market/ page."""
    r = s.get("https://www.screener.in/market/", headers=SCREENER_HEADERS, timeout=30)
    if r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    out = {}
    for a in soup.select("a[href^='/market/IN']"):
        path = a["href"].strip("/").split("/", 1)[-1]
        if path.count("/") == 3:
            out[path] = a.get_text(" ", strip=True)
    return sorted(out.items()) if len(out) >= 100 else None


_PAGES: dict = {}


def industry(s: requests.Session, path: str, label: str) -> list[dict] | None:
    """One basic industry's companies; read once per run even when several sectors include it."""
    if path not in _PAGES:
        _PAGES[path] = _industry(s, path, label)
    got = _PAGES[path]
    return None if got is None else [{**r, "industry": label} for r in got]


def _industry(s: requests.Session, path: str, label: str) -> list[dict] | None:
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


def _company_industry(s: requests.Session, code: str) -> dict | None:
    """The NSE basic industry screener.in files a company under (its deepest /market/ link), and its market cap."""
    r = s.get(f"https://www.screener.in/company/{code}/", headers=SCREENER_HEADERS, timeout=30)
    if r.status_code in (403, 429):
        raise RuntimeError("screener.in is refusing requests")
    if r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    links = [(a["href"].strip("/").split("/", 1)[-1], a.get_text(" ", strip=True)) for a in soup.select("a[href^='/market/IN']")]
    deepest = max(links, key=lambda x: x[0].count("/"), default=None)
    mcap = None
    for li in soup.select("#top-ratios li"):
        if li.get_text(" ", strip=True).lower().startswith("market cap"):
            v = li.select_one(".value")
            m = re.search(r"[\d,]+(?:\.\d+)?", v.get_text(" ", strip=True) if v else "")
            mcap = _num(m.group(0)) if m else None
    h1 = soup.select_one("h1")
    return {"path": deepest[0] if deepest and deepest[0].count("/") >= 3 else None, "label": deepest[1] if deepest else None,
            "mcap_cr": mcap, "name": h1.get_text(" ", strip=True) if h1 else code}


def _exchange_lists():
    """(latest NSE book, latest BSE book, ISINs that traded a year ago) from the bhavcopies update_charts.py keeps."""
    from app.charts import is_equity_isin, read_day
    cache = Path(__file__).resolve().parent.parent / ".bhav_cache"
    nse = sorted(cache.glob("NSE-*.v2.csv.gz"))
    bse = sorted(cache.glob("BSE-*.v2.csv.gz"))
    if not nse:
        return None
    latest_n = {r[0]: r for r in read_day(nse[-1]) if is_equity_isin(r[9]) and r[7] > 0}
    latest_b = {r[0]: r for r in read_day(bse[-1]) if is_equity_isin(r[9]) and r[7] > 0} if bse else {}
    old = set()
    for files in (nse, bse):
        if len(files) > 250:
            old |= {r[9] for r in read_day(files[-250]) if r[9]}
    return latest_n, latest_b, old


def supplement(s: requests.Session, listed: dict, classified: dict) -> dict:
    """Companies the industry listings miss: new listings (first traded within about a year) and, for a sector
    with keywords, every traded company whose name matches -- each classified once from its own screener.in page."""
    lists = _exchange_lists()
    if not lists:
        print("  supplement: no bhavcopy cache here; skipped")
        return {}
    nse, bse, old = lists
    nse_isins = {r[9] for r in nse.values() if r[9]}
    known = {m["code"] for v in listed.values() for m in v}
    path_to: dict = {}
    for sec in sectors():
        for path, label in sec["industries"]:
            path_to.setdefault(path, []).append((sec["slug"], label))
    kw = [(sec["slug"], re.compile(sec["keywords"], re.I)) for sec in sectors() if sec.get("keywords")]
    cands = {}
    for ex, book in (("NSE", nse), ("BSE", bse)):
        for key, r in book.items():
            isin = r[9]
            if ex == "BSE" and isin and isin in nse_isins:
                continue                                   # the NSE line stands for it
            name = r[2] or r[1]
            new = bool(isin) and isin not in old
            hit = any(rx.search(name) for _, rx in kw)
            if (new or hit) and key not in known:
                cands[isin or key] = (key, name, new, hit)
    # name matches first, then the newest listings
    cands = dict(sorted(cands.items(), key=lambda kv: (not kv[1][3], not kv[1][2])))
    now = datetime.now(timezone.utc)
    out, fetched = {}, 0
    for ident, (key, name, new, _hit) in cands.items():
        c = classified.get(ident)
        stale = not c or (not c.get("path") and (now - datetime.fromisoformat(c["at"])).days >= 7)
        if stale:
            if fetched >= SUPPLEMENT_BUDGET:
                continue
            try:
                info = _company_industry(s, key)
            except requests.RequestException:
                info = None
            fetched += 1
            time.sleep(DELAY)
            c = {"code": key, "at": now.isoformat(timespec="seconds"), **(info or {"path": None, "name": name, "mcap_cr": None})}
            classified[ident] = c
        for slug, label in path_to.get(c.get("path"), []):
            out.setdefault(slug, []).append({"code": key, "name": c.get("name") or name, "industry": label,
                                             "mcap_cr": c.get("mcap_cr"), "new_listing": new})
    print(f"  supplement: {len(cands)} candidates, {fetched} pages read, {sum(len(v) for v in out.values())} added")
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--if-older-than", type=float, default=0, help="days: skip the industry listings when fresher than this")
    args = ap.parse_args()
    prev = {}
    try:
        prev = json.loads(MEMBERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    listed = dict(prev.get("listed") or prev.get("sectors") or {})
    age = None
    if prev.get("updated"):
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(prev["updated"])).total_seconds() / 86400
    s = requests.Session()
    fresh = bool(args.if_older_than and age is not None and age < args.if_older_than)
    if not fresh or not INDUSTRIES_FILE.exists():
        inds = industry_index(s)
        if inds:
            INDUSTRIES_FILE.parent.mkdir(parents=True, exist_ok=True)
            INDUSTRIES_FILE.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                   "industries": inds}, ensure_ascii=False, indent=0), encoding="utf-8")
            print(f"  {len(inds)} NSE basic industries")
    SECTORS = sectors()
    missing = [sec for sec in SECTORS if sec["slug"] not in listed]
    for sec in (missing if fresh else SECTORS):     # weekly: every sector; otherwise only a sector just added
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
        if uniq and (ok or len(uniq) >= 0.8 * len(listed.get(sec["slug"], []))):
            listed[sec["slug"]] = sorted(uniq, key=lambda r: -(r["mcap_cr"] or 0))
        print(f"  {sec['name']}: {len(uniq)} companies listed by industry")
    classified = dict(prev.get("classified") or {})
    try:
        extra = supplement(s, listed, classified)
    except RuntimeError as e:
        print(f"  supplement: {e}; skipped")
        extra = prev.get("extra") or {}
    by_slug = {}
    for sec in SECTORS:
        rows = list(listed.get(sec["slug"], []))
        have = {r["code"] for r in rows}
        rows += [r for r in extra.get(sec["slug"], []) if r["code"] not in have]
        by_slug[sec["slug"]] = sorted(rows, key=lambda r: -(r.get("mcap_cr") or 0))
    sme = nse_list(NSE_SME_LIST, "SYMBOL") or prev.get("sme") or []
    n500 = nse_list(NIFTY500_LIST, "Symbol") or prev.get("nifty500") or []
    out = {"updated": prev.get("updated") if fresh and not missing else datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "sectors": by_slug, "listed": listed, "extra": extra, "classified": classified, "sme": sme, "nifty500": n500}
    MEMBERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMBERS_FILE.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"run_sector_members_in: {sum(len(v) for v in by_slug.values())} members across {len(by_slug)} sectors "
          f"({sum(len(v) for v in extra.values())} from new listings / name matches); {len(sme)} NSE SME symbols; {len(n500)} Nifty 500")
    return 0


if __name__ == "__main__":
    sys.exit(main())
