"""Older history for the Earnings drift (PEAD) strategy (app/pead.py): a one-off backfill, resumable.

screener.in keeps 12 quarters, and the strategy's profit surprise (SUE) needs 7 of them before the first
signal, so on screener data alone the backtest starts in mid-2025. This adds the three years before:

- quarterly net profit (Jun 2021 - Jun 2024) from each company's results XBRL on NSE, on the same basis
  (consolidated / standalone) screener uses, checked against screener's numbers where the two overlap;
- when each of those results was filed (NSE's broadcast time), which decides the reaction session;
- daily prices for Apr 2023 - Feb 2024 from the NSE bhavcopies (.bhav_cache), corporate-action safe
  (each day's change is close / NSE's previous close, which NSE adjusts on ex-dates);
- the Nifty 500 before Sector Strength's history starts (NSE's daily index file).

    cd backend
    python scripts/pead_history.py            # ~1,600 companies, a few hours; rerun to resume

Writes data/results_preview/pead_history.json.gz (committed: it never changes once built).
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
OUT = BACKEND_DIR / "data" / "results_preview" / "pead_history.json.gz"
BHAV = BACKEND_DIR / ".bhav_cache"
Q_FROM, Q_TO = date(2021, 6, 30), date(2024, 6, 30)       # quarters fetched from XBRL
PX_FROM, PX_TO = "2023-04-01", "2024-02-29"                # prices from the bhavcopies
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"}
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def load() -> dict:
    try:
        return json.loads(gzip.decompress(OUT.read_bytes()))
    except (OSError, ValueError, EOFError):
        return {"companies": {}, "prices": {}, "nifty500": {}, "failed": {}}


def save(d: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(d, separators=(",", ":")).encode(), mtime=0))
    tmp.replace(OUT)


def label(d: date) -> str:
    return f"{MON[d.month - 1]} {d.year}"


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(UA)
    s.get("https://www.nseindia.com/companies-listing/corporate-filings-financial-results", timeout=30)
    return s


NP_TAGS = ("ProfitLossForPeriod", "ProfitLossForThePeriod", "NetProfitLossForThePeriod", "ProfitLossAfterTaxesAndExtraordinaryItems")


def xbrl_np(text: str, qs: date, qe: date) -> float | None:
    """Net profit for the quarter (Rs crore) from a results XBRL: the context covering exactly that quarter."""
    ctx = {}
    for cid, body in re.findall(r'<xbrli:context id="([^"]+)">(.*?)</xbrli:context>', text, re.S):
        a = re.search(r"<xbrli:startDate>([\d-]+)<", body)
        b = re.search(r"<xbrli:endDate>([\d-]+)<", body)
        if a and b and "xbrldi:" not in body:
            ctx[cid] = (a.group(1), b.group(1))
    want = {c for c, (a, b) in ctx.items() if a == qs.isoformat() and b == qe.isoformat()}
    if not want:   # some filings carry the dates as facts instead
        st = dict(re.findall(r':DateOfStartOfReportingPeriod[^>]*contextRef="([^"]+)"[^>]*>([\d-]+)<', text))
        want = {c for c, v in st.items() if v == qs.isoformat()}
    for tag in NP_TAGS:
        for cid, v in re.findall(r':%s\b[^>]*contextRef="([^"]+)"[^>]*>\s*(-?[\d.]+)\s*<' % tag, text):
            if cid in want:
                return float(v) / 1e7
    return None


def fetch_company(s: requests.Session, sym: str, basis: str) -> dict | None:
    r = s.get(f"https://www.nseindia.com/api/corporates-financial-results?index=equities&symbol={sym}&period=Quarterly", timeout=40)
    if r.status_code in (401, 403):
        raise PermissionError
    if not r.ok:
        return None
    try:
        rows = r.json()
    except ValueError:
        return None
    want = "Consolidated" if basis == "consolidated" else "Non-Consolidated"
    picks = {}
    for x in rows if isinstance(rows, list) else []:
        try:
            qe = datetime.strptime(x["toDate"], "%d-%b-%Y").date()
            qs = datetime.strptime(x["fromDate"], "%d-%b-%Y").date()
        except (KeyError, ValueError, TypeError):
            continue
        if not (Q_FROM <= qe <= Q_TO) or (qe - qs).days > 95 or not x.get("xbrl") or x.get("cumulative") == "Cumulative":
            continue
        rank = 0 if x.get("consolidated") == want else 1
        cur = picks.get(qe)
        if cur is None or rank < cur[0]:
            picks[qe] = (rank, qs, x)
    out = {}

    def one(item):
        qe, (rank, qs, x) = item
        for _ in range(2):
            try:
                t = requests.get(x["xbrl"], headers=UA, timeout=40)
                if t.ok:
                    return qe, rank, xbrl_np(t.text, qs, qe), x
            except requests.RequestException:
                time.sleep(2)
        return qe, rank, None, x

    with ThreadPoolExecutor(4) as ex:
        for qe, rank, np_, x in ex.map(one, sorted(picks.items())):
            try:
                filed = datetime.strptime(x.get("broadCastDate") or x.get("filingDate"), "%d-%b-%Y %H:%M:%S").isoformat()
            except (TypeError, ValueError):
                filed = None
            out[label(qe)] = {"np": None if np_ is None else round(np_, 3), "filed": filed, "alt_basis": bool(rank)}
    return out


def prices(symbols: set[str]) -> dict:
    """{symbol: [[date, open, close], ...]} chained from close / previous close, scaled to the last raw close."""
    files = sorted(f for f in BHAV.glob("NSE-*.v2.csv.gz") if PX_FROM <= f"{f.name[4:8]}-{f.name[8:10]}-{f.name[10:12]}" <= PX_TO)
    series: dict[str, list] = {}
    for f in files:
        d = f"{f.name[4:8]}-{f.name[8:10]}-{f.name[10:12]}"
        for line in gzip.open(f, "rt", encoding="utf-8", errors="ignore"):
            p = line.rstrip("\n").split(",")
            if len(p) < 9 or p[0] not in symbols:
                continue
            try:
                o, c, pc = float(p[3]), float(p[6]), float(p[8])
            except ValueError:
                continue
            series.setdefault(p[0], []).append((d, o, c, pc))
    out = {}
    for sym, rows in series.items():
        # adjusted close: walk back from the last raw close using each day's close / previous close
        adj = [0.0] * len(rows)
        adj[-1] = rows[-1][2]
        for k in range(len(rows) - 1, 0, -1):
            d, o, c, pc = rows[k]
            adj[k - 1] = adj[k] * (pc / c) if c and pc else adj[k] * (rows[k - 1][2] / c if c else 1)
        out[sym] = [[d, round(o * adj[k] / c, 4) if c else None, round(adj[k], 4)] for k, (d, o, c, pc) in enumerate(rows)]
    return out


def nifty500(days: list[str]) -> dict:
    out = {}
    for d in days:
        dd = date.fromisoformat(d)
        url = f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{dd.strftime('%d%m%Y')}.csv"
        try:
            r = requests.get(url, headers=UA, timeout=30)
        except requests.RequestException:
            continue
        for line in r.text.splitlines():
            p = line.split(",")
            if p and p[0].strip().upper() == "NIFTY 500":
                try:
                    out[d] = float(p[5])
                except (ValueError, IndexError):
                    pass
        time.sleep(0.3)
    return out


def main() -> int:
    d = load()
    raw = json.loads((BACKEND_DIR / "data" / "companies_raw.json").read_text(encoding="utf-8"))
    board = json.loads((BACKEND_DIR / "data" / "results_in" / "latest.json").read_text(encoding="utf-8")).get("companies") or {}
    uni = json.loads(gzip.decompress((BACKEND_DIR / "data" / "results_universe" / "latest.json.gz").read_bytes())).get("companies") or {}
    basis, screener = {}, {}
    for c in raw:
        b = board.get(str(c.get("code") or ""))
        if b and c.get("nse_code") and not str(c["nse_code"]).isdigit():
            basis[str(c["nse_code"]).upper()] = b.get("basis") or "consolidated"
            screener[str(c["nse_code"]).upper()] = b.get("quarters") or {}
    for k, v in uni.items():
        if k.startswith("NSE:") and k[4:] not in basis:
            basis[k[4:]] = v.get("basis") or "consolidated"
            screener[k[4:]] = v.get("quarters") or {}
    from app.pead import EXCLUDE_SECTORS, EXCLUDE_INDUSTRIES
    sec = json.loads((BACKEND_DIR / "data" / "sector_strength" / "latest.json").read_text(encoding="utf-8")).get("sectors") or []
    skip = {s.get("symbol") for x in sec if x.get("group") == "nse" for s in x.get("stocks") or []
            if x["slug"] in EXCLUDE_SECTORS or s.get("industry") in EXCLUDE_INDUSTRIES}
    todo = sorted(s for s in basis if s not in skip and s not in d["companies"] and d["failed"].get(s, 0) < 2)
    print(f"pead history: {len(todo)} companies to fetch ({len(d['companies'])} done)", flush=True)
    s = session()
    for i, sym in enumerate(todo, 1):
        try:
            got = fetch_company(s, sym, basis[sym])
        except PermissionError:
            time.sleep(30)
            s = session()
            try:
                got = fetch_company(s, sym, basis[sym])
            except (PermissionError, requests.RequestException):
                got = None
        except requests.RequestException:
            got = None
        if got is None:
            d["failed"][sym] = d["failed"].get(sym, 0) + 1
        else:
            # check against screener where both have the quarter (Sep 2023 - Jun 2024)
            P = screener[sym].get("periods") or []
            NP = screener[sym].get("net_profit") or []
            ok = bad = 0
            for lab, v in got.items():
                if lab in P and v["np"] is not None and P.index(lab) < len(NP) and NP[P.index(lab)] is not None:
                    a, b = v["np"], NP[P.index(lab)]
                    if abs(a - b) <= max(1.0, 0.06 * abs(b)):
                        ok += 1
                    else:
                        bad += 1
            d["companies"][sym] = {"basis": basis[sym], "q": got, "check": [ok, bad]}
        if i % 25 == 0:
            save(d)
            print(f"  ...{i}/{len(todo)}", flush=True)
        time.sleep(0.4)
    if not d.get("prices"):
        d["prices"] = prices(set(basis) - skip)
        print(f"pead history: prices for {len(d['prices'])} symbols", flush=True)
    if not d.get("nifty500"):
        days = sorted({r[0] for v in d["prices"].values() for r in v if r[0] < "2023-09-14"})
        d["nifty500"] = nifty500(days)
        print(f"pead history: Nifty 500 for {len(d['nifty500'])} days", flush=True)
    save(d)
    good = sum(1 for v in d["companies"].values() if v["check"][0] >= 2 and v["check"][1] == 0)
    print(f"pead history: {len(d['companies'])} companies, {good} agree with screener on every overlapping quarter")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
