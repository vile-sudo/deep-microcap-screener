"""
Everything a deep-dive report is built from, fetched from public sources only:

  screener.in company page  12 years of P&L / balance sheet / cash flow,
                            12 quarters, working-capital ratios, quarterly
                            shareholding, peers, and the document list
  exchange filings          the latest annual report, earnings-call
                            transcript and investor presentation (BSE/NSE
                            PDFs linked from that page), read with pdftotext
  credit-rating rationale   the latest one (CRISIL / ICRA / CARE ... page)

Nothing here needs a login. Numbers are Rs crore as screener shows them.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time

import requests
from bs4 import BeautifulSoup

SCREENER_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-research-screener/1.0; research script, not a bulk crawler)"}
FILING_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                  "Referer": "https://www.bseindia.com/", "Accept": "application/pdf,text/html,*/*"}
DELAY = 2.0
PDFTOTEXT = shutil.which("pdftotext")
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def num(text):
    if text is None:
        return None
    t = str(text).strip().replace(",", "").replace("%", "")
    if t in ("", "-", "--", "NA"):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def quarter_label(col: str) -> str | None:
    """'Jun 2026' -> 'Q1 FY27' (Indian fiscal year, April-March)."""
    m = re.match(r"([A-Z][a-z]{2})\s+(\d{4})", col or "")
    if not m or m.group(1) not in MONTHS:
        return None
    month, year = MONTHS[m.group(1)], int(m.group(2))
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(month)
    if not q:
        return None
    fy = year + 1 if month >= 4 else year
    return f"Q{q} FY{str(fy)[-2:]}"


def period_id(col: str) -> str | None:
    """'Jun 2026' -> 'FY27-Q1' (sorts correctly as a string)."""
    lab = quarter_label(col)
    return f"FY{lab.split('FY')[1]}-{lab.split()[0]}" if lab else None


# ------------------------------------------------------------------ screener page
def _table(section) -> dict:
    """A screener data table -> {"columns": [...], "rows": {label: [values]}}."""
    out = {"columns": [], "rows": {}}
    if section is None:
        return out
    t = section.select_one("table")
    if t is None:
        return out
    out["columns"] = [th.get_text(" ", strip=True) for th in t.select("thead th")][1:]
    for tr in t.select("tbody tr"):
        tds = tr.select("td")
        if len(tds) < 2:
            continue
        label = re.sub(r"\s*\+\s*$", "", tds[0].get_text(" ", strip=True)).strip()
        if not label or label.lower().startswith("raw pdf"):
            continue
        out["rows"][label] = [num(td.get_text(" ", strip=True)) for td in tds[1:]]
    return out


def fetch_company(code_candidates) -> dict | None:
    """The consolidated page when the company reports one, else standalone."""
    for code in [c for c in code_candidates if c]:
        for suffix in ("consolidated/", ""):
            url = f"https://www.screener.in/company/{code}/{suffix}"
            r = requests.get(url, headers=SCREENER_HEADERS, timeout=30)
            time.sleep(DELAY)
            if r.status_code == 404:
                break
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            pl = _table(soup.select_one("#profit-loss"))
            # screener serves an empty consolidated view for standalone-only companies
            if suffix and not any(v is not None for v in (pl["rows"].get("Sales") or pl["rows"].get("Revenue") or [])):
                continue
            return parse_company(soup, url, consolidated=bool(suffix))
    return None


def parse_company(soup: BeautifulSoup, url: str, consolidated: bool) -> dict:
    ratios = {}
    for li in soup.select("#top-ratios li"):
        n, v = li.select_one(".name"), li.select_one(".number")
        if n and v:
            ratios[n.get_text(" ", strip=True)] = num(v.get_text(strip=True))
    hi_lo = soup.select_one("#top-ratios li:has(.name:-soup-contains('High / Low'))")
    if hi_lo:
        nums = [num(x.get_text(strip=True)) for x in hi_lo.select(".number")]
        if len(nums) == 2:
            ratios["High"], ratios["Low"] = nums
    shareholding = soup.select_one("#shareholding")
    quarterly_sh = _table(shareholding.select_one("#quarterly-shp")) if shareholding else {"columns": [], "rows": {}}
    if not quarterly_sh["columns"] and shareholding:
        quarterly_sh = _table(shareholding)
    about = soup.select_one(".company-profile .about") or soup.select_one(".about")
    points = soup.select_one(".company-profile .commentary") or soup.select_one(".commentary")
    peers = soup.select_one("#peers")
    sector_path = []
    if peers:
        sector_path = [a.get_text(strip=True) for a in peers.select('a[href^="/market/"]')]
    cid = soup.select_one("[data-company-id]")
    return {
        "url": url,
        "consolidated": consolidated,
        "company_id": cid.get("data-company-id") if cid else None,
        "name": (soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None),
        "about": re.sub(r"\s*\[\d+\]", "", about.get_text(" ", strip=True)) if about else "",
        "key_points": re.sub(r"\s*\[\d+\]", "", points.get_text(" ", strip=True)) if points else "",
        "sector_path": [p for i, p in enumerate(sector_path) if p not in sector_path[:i]],
        "top_ratios": ratios,
        "quarters": _table(soup.select_one("#quarters")),
        "profit_loss": _table(soup.select_one("#profit-loss")),
        "balance_sheet": _table(soup.select_one("#balance-sheet")),
        "cash_flow": _table(soup.select_one("#cash-flow")),
        "ratios": _table(soup.select_one("#ratios")),
        "shareholding": quarterly_sh,
        "growth": _growth_boxes(soup),
        "documents": _documents(soup),
    }


def _growth_boxes(soup) -> dict:
    """The 'Compounded Sales Growth / Profit Growth / Stock Price CAGR / ROE' boxes."""
    out = {}
    for t in soup.select("#profit-loss table.ranges-table"):
        head = t.select_one("th")
        if not head:
            continue
        key = head.get_text(" ", strip=True)
        out[key] = {tds[0].get_text(" ", strip=True).rstrip(":"): tds[1].get_text(" ", strip=True)
                    for tds in (tr.select("td") for tr in t.select("tr")) if len(tds) == 2}
    return out


def _documents(soup) -> dict:
    docs = {"annual_reports": [], "concalls": [], "credit_ratings": [], "announcements": []}
    for blk in soup.select("#documents .documents"):
        h = blk.select_one("h3")
        title = (h.get_text(" ", strip=True) if h else "").lower()
        for li in blk.select("li"):
            links = [(a.get_text(" ", strip=True), a.get("href")) for a in li.select("a") if a.get("href")]
            text = li.get_text(" | ", strip=True)
            if "annual" in title and links:
                year = re.search(r"20\d\d", text)
                docs["annual_reports"].append({"title": links[0][0][:60], "year": int(year.group(0)) if year else None, "url": links[0][1]})
            elif "concall" in title:
                date = li.select_one(".ink-600, .nowrap") or li
                label = re.match(r"([A-Z][a-z]{2} \d{4})", text)
                entry = {"date": label.group(1) if label else text[:10]}
                for name, href in links:
                    if name.lower() in ("transcript", "ppt", "rec", "notes"):
                        entry[name.lower()] = href
                docs["concalls"].append(entry)
            elif "credit" in title and links:
                docs["credit_ratings"].append({"title": text[:80], "url": links[0][1]})
            elif "announcement" in title and links:
                docs["announcements"].append({"title": re.sub(r"\s+", " ", links[0][0])[:220], "url": links[0][1]})
    return docs


# ------------------------------------------------------------------ documents
def pdf_pages(url: str, max_pages: int = 400, max_bytes: int = 80_000_000) -> list[str]:
    """Text of a filing PDF, one string per page ([] when unreadable)."""
    if not PDFTOTEXT or not url:
        return []
    r = requests.get(url, headers=FILING_HEADERS, timeout=120)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF") or len(r.content) > max_bytes:
        return []
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        os.write(fd, r.content)
        os.close(fd)
        out = subprocess.run([PDFTOTEXT, "-q", "-layout", "-l", str(max_pages), path, "-"], capture_output=True, timeout=240)
        return [re.sub(r"[ \t]+", " ", p) for p in out.stdout.decode("utf-8", "ignore").split("\f")]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def html_text(url: str) -> str:
    r = requests.get(url, headers={**FILING_HEADERS, "Accept": "text/html,*/*"}, timeout=60)
    r.raise_for_status()
    if r.content.startswith(b"%PDF"):
        return "\n".join(pdf_pages(url, max_pages=40))
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return re.sub(r"\n\s*\n+", "\n\n", soup.get_text("\n", strip=True))


# Pages of an annual report worth an analyst's time, by what they contain.
AR_TOPICS = {
    "business": r"management discussion|business overview|our business|segment|capacity|order book|products?|customers?|exports?|market share",
    "governance": r"related party|transactions with related|key managerial|remuneration|contingent liabilit|guarantee|pledge",
    "audit": r"independent auditor|key audit matter|emphasis of matter|qualified opinion|CARO|companies \(auditor'?s report\) order|audit trail",
    "risk": r"risks? and concerns|threats|litigation|show cause|penalt",
    "numbers": r"trade receivables|inventories|borrowings|capital work.in.progress|revenue from operations|segment information",
}


def select_pages(pages: list[str], budget_chars: int) -> list[tuple[int, str]]:
    """The highest-value pages that fit the budget, returned in page order."""
    scored = []
    for i, p in enumerate(pages):
        text = p.strip()
        if len(text) < 200:
            continue
        score = sum(len(re.findall(rx, text, re.I)) * w for rx, w in
                    zip(AR_TOPICS.values(), (3, 3, 4, 2, 1)))
        # notices, proxy forms, e-voting instructions and CSR annexures read like filler
        if re.search(r"e-voting|proxy form|attendance slip|remote e-voting|book closure|NSDL e-Voting|notice is hereby given|"
                     r"explanatory statement pursuant|special business|route map|ordinary resolution|special resolution", text, re.I):
            score -= 20
        if score > 0:
            scored.append((score, i, text))
    scored.sort(key=lambda x: -x[0])
    chosen, used = [], 0
    for score, i, text in scored:
        if used + len(text) > budget_chars:
            continue
        chosen.append((i + 1, text))
        used += len(text)
    return sorted(chosen)
