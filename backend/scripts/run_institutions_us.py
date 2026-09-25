"""Institutional ownership (SEC Form 13F) for the US board's companies.

    cd backend
    python scripts/run_institutions_us.py            # skips when nothing new to do
    python scripts/run_institutions_us.py --force

Finnhub's free tier has no institutional-ownership endpoint, so this fills the gap
from the source it comes from: the SEC's quarterly "Form 13F data sets" -- every
holding reported by every institution managing more than $100m, as free bulk files
(sec.gov/data-research/sec-markets-data/form-13f-data-sets). The newest file is
~100 MB zipped / ~400 MB of holdings; it is streamed, never unpacked to disk.

For each board company it works out how many institutions hold it, how many shares
they hold in total, that as a share of shares outstanding (market cap / price, both
from the board's own record), and the five biggest holders. Written to
data/institutions_us/latest.json, served at GET /api/institutions-us, shown in the
company scorecard's "Institutions" cell and holders table.

Matching. 13F names issuers by name and CUSIP, not by ticker, and there is no free
ticker->CUSIP table. So a board company is matched on its name with punctuation,
spacing and legal suffixes (Inc, Corp, Holdings, ...) removed; the CUSIP of the
common stock under that name is then used to add the holdings up. A company whose
legal name differs from the name 13F filers use simply gets no entry -- shown as
"not available", never a guess. Only 13F-HR filings are used (amendments can be
partial), and each institution counts once, at its latest reported quarter.

13F is a quarterly, 45-days-late snapshot, and small positions (under 10,000 shares
and $200k) are not reported: read the percentage as "at least", not exact.

Cadence: a new file appears about quarterly. The weekly workflow calls this script;
it does nothing unless there is a newer file than the one already used, or a board
company that has not been looked up yet.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW_US = BACKEND_DIR / "data" / "companies_us_raw.json"
OUT_FILE = BACKEND_DIR / "data" / "institutions_us" / "latest.json"

PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
HEADERS = {"User-Agent": "Deep Sweep research contact@pkresearch.in"}
TOP_HOLDERS = 5

SUFFIXES = ("INCORPORATED", "CORPORATION", "HOLDINGS", "HOLDING", "HLDGS", "HLDG", "LIMITED", "COMPANY", "INC", "CORP",
            "LTD", "LLC", "PLC", "LP", "CO", "THE")
BAD_CLASS = re.compile(r"PFD|PREF|WT|WARRANT|NOTE|DEBT|RIGHT|UNIT|BOND|DEBENT|CV ", re.I)
csv.field_size_limit(1 << 24)


def name_key(name: str) -> str:
    """Upper-case, letters and digits only, legal suffixes stripped from the end."""
    k = re.sub(r"[^A-Z0-9 ]", "", (name or "").upper().replace("&", " AND "))
    k = re.sub(r"\bTHE\b", " ", k)          # "THE" anywhere, before spaces are squashed
    k = k.replace(" ", "")
    changed = True
    while changed:
        changed = False
        for s in SUFFIXES:
            if k.endswith(s) and len(k) > len(s) + 2:
                k, changed = k[: -len(s)], True
    return k


def latest_zip_url(session: requests.Session) -> str:
    html = session.get(PAGE, headers=HEADERS, timeout=60).text
    found = re.findall(r'href="([^"]*?(\d{2})([a-z]{3})(\d{4})-(\d{2})([a-z]{3})(\d{4})_form13f\.zip)"', html, re.I)
    if not found:
        raise RuntimeError("no Form 13F data set links on the SEC page")
    months = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

    def end(f):   # (year, month, day) the file's window ends
        return (int(f[6]), months[f[5].lower()], int(f[4]))
    href = max(found, key=end)[0]
    return href if href.startswith("http") else "https://www.sec.gov" + href


def _tsv(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline=""), delimiter="\t")


def _date(s: str):
    try:
        return datetime.strptime(s, "%d-%b-%Y").date().isoformat()
    except ValueError:
        return ""


def analyse(zf: zipfile.ZipFile, board: dict) -> dict:
    """board: code -> {name, shares_out}. Returns code -> entry, only for matched companies."""
    keys = {}
    for code, b in board.items():
        k = name_key(b["name"])
        if len(k) >= 4:
            keys.setdefault(k, []).append(code)

    # pass over the holdings: keep only rows whose issuer matches a board company
    rows = defaultdict(list)       # name key -> [(accession, cusip, title, shares)]
    for r in _tsv(zf, "INFOTABLE.tsv"):
        k = name_key(r["NAMEOFISSUER"])
        if k not in keys or r["SSHPRNAMTTYPE"] != "SH" or r["PUTCALL"] or BAD_CLASS.search(r["TITLEOFCLASS"] or ""):
            continue
        try:
            rows[k].append((r["ACCESSION_NUMBER"], r["CUSIP"], int(float(r["SSHPRNAMT"]))))
        except ValueError:
            continue

    # each institution once, at its latest quarter: 13F-HR only, newest period then newest filing date
    latest = {}
    for r in _tsv(zf, "SUBMISSION.tsv"):
        if r["SUBMISSIONTYPE"] != "13F-HR":
            continue
        rank = (_date(r["PERIODOFREPORT"]), _date(r["FILING_DATE"]))
        if r["CIK"] not in latest or rank > latest[r["CIK"]][0]:
            latest[r["CIK"]] = (rank, r["ACCESSION_NUMBER"])
    accession_cik = {acc: cik for cik, (_, acc) in latest.items()}
    period_of = {acc: rank[0] for _, (rank, acc) in latest.items()}

    wanted = {a for v in rows.values() for a, _, _ in v if a in accession_cik}
    manager = {}
    for r in _tsv(zf, "COVERPAGE.tsv"):
        if r["ACCESSION_NUMBER"] in wanted:
            manager[r["ACCESSION_NUMBER"]] = (r["FILINGMANAGER_NAME"] or "").strip()

    out = {}
    for k, codes in keys.items():
        held = [(a, c, s) for a, c, s in rows.get(k, []) if a in accession_cik]
        if not held:
            continue
        by_cusip = defaultdict(int)
        for _, c, s in held:
            by_cusip[c] += s
        cusip = max(by_cusip, key=by_cusip.get)                     # the common stock under this name
        per_holder = defaultdict(int)                                # an institution can hold under several lines
        for a, c, s in held:
            if c == cusip:
                per_holder[a] += s
        total = sum(per_holder.values())
        periods = sorted(period_of[a] for a in per_holder)
        top = sorted(per_holder.items(), key=lambda kv: kv[1], reverse=True)[:TOP_HOLDERS]
        for code in codes:
            so = board[code].get("shares_out")
            pct = round(total / so * 100, 2) if so else None
            out[code] = {
                "cusip": cusip, "institutions": len(per_holder), "shares_held": total,
                # Over 100% happens when several entities of one manager report the same shares (parent
                # and subsidiaries); show it as "100%+" rather than a number that cannot be right.
                "inst_pct": None if pct is None else min(pct, 100.0), "over_100": bool(pct and pct > 100),
                "period": periods[len(periods) // 2] if periods else None,        # the typical reported quarter
                "top": [{"name": manager.get(a) or "—", "shares": s,
                         "pct": round(s / so * 100, 2) if so else None} for a, s in top],
            }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    records = json.loads(RAW_US.read_text(encoding="utf-8")) if RAW_US.exists() else []
    board = {}
    for r in records:
        if not r.get("code"):
            continue
        cap, px = r.get("market_cap_usd"), r.get("price")
        board[r["code"]] = {"name": r.get("name") or r["code"],
                            "shares_out": (cap * 1e6 / px) if cap and px else None}
    if not board:
        print("run_institutions_us: no US board companies yet; nothing to do")
        return 0

    try:
        prev = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}

    session = requests.Session()
    try:
        url = latest_zip_url(session)
    except (requests.RequestException, RuntimeError) as e:
        print(f"run_institutions_us: could not find the newest 13F file ({e}); keeping what we have")
        return 0
    source = url.rsplit("/", 1)[-1]
    if not args.force and prev.get("source_file") == source and set(board) <= set(prev.get("attempted") or []):
        print(f"run_institutions_us: {source} already used and every board company looked up; nothing to do")
        return 0

    print(f"run_institutions_us: downloading {url}")
    r = session.get(url, headers=HEADERS, timeout=600, stream=True)
    r.raise_for_status()
    buf = io.BytesIO()
    for chunk in r.iter_content(1 << 20):
        buf.write(chunk)
    print(f"run_institutions_us: {buf.tell() / 1e6:.0f} MB downloaded; reading holdings")
    with zipfile.ZipFile(buf) as zf:
        companies = analyse(zf, board)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": source,
        "attempted": sorted(board),
        "companies_checked": len(board),
        "companies_matched": len(companies),
        "companies": companies,
    }, separators=(",", ":")), encoding="utf-8")
    missing = [c for c in sorted(board) if c not in companies]
    print(f"run_institutions_us: matched {len(companies)}/{len(board)} board companies from {source}"
          + (f"; no 13F match for: {', '.join(missing)}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
