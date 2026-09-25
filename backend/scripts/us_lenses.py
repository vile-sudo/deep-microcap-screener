"""
The US board's "lenses" -- the same signals India's auto_screen.py computes
beyond the moat-evidence path, built on the same rules and, where the rule
is text-shaped, on the very same regexes (imported from auto_screen.py, not
copied, so a change to what counts as "guidance" or a "capacity ramp"
changes both markets together):

  guidance          a self-referencing forward revenue-growth statement
                    (auto_screen.find_guidance), plus one US-specific
                    addition: a dollar revenue range ("expects net sales
                    of $410 to $430 million") turned into a percentage
                    against last fiscal year's revenue, marked derived --
                    the same "percentage derived from their own two
                    figures" rule the India board documents.
  PAT turnaround    latest quarter profitable after a loss in one of the
                    previous three (auto_screen.pat_turnaround), from SEC's
                    XBRL company facts instead of screener.in.
  capacity ramp     auto_screen.CAPACITY_* / NEAR_TERM_RX, with US fiscal-
                    year phrasing added to the near-term test.
  product pivot     auto_screen.PIVOT_VERB_RX + DEMAND_SHIFT_RX, unchanged.
  CWIP / capex      construction-in-progress vs net PP&E. Genuinely sparse
                    in the US: SEC's company-facts feed omits dimensional
                    facts and most filers report CIP only as one row of the
                    PP&E note (or not at all -- 2 of the 10 companies on
                    the board today show one), so this reads that row from
                    the 10-K text and leaves the field empty when there is
                    none, rather than guessing.

Every text-derived flag carries the matched sentence and is labelled
"Auto-detected, unverified", exactly like India's.
"""
from __future__ import annotations

import html as _html
import re
import time
from datetime import date, datetime

import requests

import auto_screen as india   # the shared rules -- see module docstring

SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
SEC_HEADERS = {"User-Agent": "Deep Sweep research contact@pkresearch.in"}
DELAY_SEC = 0.15

# US fiscal-year phrasing on top of India's near-term test ("from Q3 FY27"
# has no US equivalent; a 10-K says "in fiscal 2027" or "in the second half
# of fiscal 2026").
_NEAR_TERM_US = re.compile(
    india.NEAR_TERM_RX.pattern + r"|\bin (?:fiscal(?: year)?|FY)\s?'?(?:20)?\d{2}\b|"
    r"\b(?:first|second|third|fourth) (?:quarter|half) of (?:fiscal(?: year)?|FY)?\s?'?(?:20)?\d{2}\b|"
    r"\bbeginning in the (?:first|second|third|fourth) quarter\b|\bin the second half\b", india.NEAR_TERM_RX.flags)


# ------------------------------------------------------------------ SEC facts
def company_facts(cik: str) -> dict | None:
    r = requests.get(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{cik}.json", headers=SEC_HEADERS, timeout=60)
    time.sleep(DELAY_SEC)
    return r.json() if r.status_code == 200 else None


def _days(e: dict) -> int | None:
    try:
        return (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
    except (KeyError, ValueError):
        return None


def _latest_by_end(entries: list[dict]) -> list[dict]:
    """One entry per (start, end), the most recently filed -- restatements win."""
    best: dict[tuple, dict] = {}
    for e in entries:
        k = (e.get("start"), e.get("end"))
        if k not in best or (e.get("filed") or "") > (best[k].get("filed") or ""):
            best[k] = e
    return list(best.values())


def quarterly_net_income(facts: dict, n: int = 6) -> list[tuple[str, float]]:
    """The last n discrete quarters' net income, oldest first, as
    (period_end, USD). Q1-Q3 are the 10-Q three-month figures; Q4 is never
    reported on its own, so it's derived as the fiscal year minus the first
    three quarters (or minus the nine-month figure when that is what's on
    file)."""
    us = facts.get("facts", {}).get("us-gaap", {})
    entries = []
    for tag in ("NetIncomeLoss", "ProfitLoss"):
        entries = us.get(tag, {}).get("units", {}).get("USD", [])
        if entries:
            break
    entries = _latest_by_end([e for e in entries if e.get("start") and e.get("end")])
    quarters = {e["end"]: e["val"] for e in entries if 80 <= (_days(e) or 0) <= 100}
    for a in (e for e in entries if 350 <= (_days(e) or 0) <= 380):
        if a["end"] in quarters:
            continue
        start = date.fromisoformat(a["start"])
        end = date.fromisoformat(a["end"])
        nine = next((e for e in entries if e["start"] == a["start"] and 260 <= (_days(e) or 0) <= 285
                     and 60 <= (end - date.fromisoformat(e["end"])).days <= 120), None)
        if nine:
            quarters[a["end"]] = a["val"] - nine["val"]
            continue
        prior = [v for k, v in quarters.items() if start < date.fromisoformat(k) < end]
        if len(prior) == 3:
            quarters[a["end"]] = a["val"] - sum(prior)
    return sorted(quarters.items())[-n:]


def annual_revenue(facts: dict) -> tuple[int, float] | None:
    """(fiscal-year-end year, USD) of the most recent full-year revenue."""
    us = facts.get("facts", {}).get("us-gaap", {})
    for tag in ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"):
        entries = [e for e in us.get(tag, {}).get("units", {}).get("USD", [])
                   if e.get("start") and 350 <= (_days(e) or 0) <= 380 and e.get("form") in ("10-K", "10-K/A")]
        if entries:
            e = max(_latest_by_end(entries), key=lambda x: x["end"])
            return int(e["end"][:4]), float(e["val"])
    return None


def net_ppe(facts: dict) -> float | None:
    us = facts.get("facts", {}).get("us-gaap", {})
    for tag in ("PropertyPlantAndEquipmentNet",
                "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"):
        entries = [e for e in us.get(tag, {}).get("units", {}).get("USD", []) if e.get("end")]
        if entries:
            return float(max(entries, key=lambda x: (x["end"], x.get("filed") or ""))["val"])
    return None


# ------------------------------------------------------------------ CWIP
_NUM = r"\(?\$?\s?([\d]{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\)?"


def cwip_from_text(text: str, ppe_net_usd: float | None) -> dict | None:
    """Construction-in-progress from the PP&E note's table row in the 10-K
    text, as {"cip_usd", "pct_net_block"} or None. The row gives a number
    in whatever unit the table is in (thousands or millions, stated once in
    a heading far from the row), so the unit is inferred as the one that
    puts CIP at a sane fraction (0.05%-300%) of SEC's own net PP&E figure
    -- and if no unit fits, or there is no net PP&E to check against, the
    answer is None, not a guess."""
    if not ppe_net_usd:
        return None
    for line in text.split("\n"):
        m = re.match(r"\s*(?:Construction|Projects?)[- ]in[- ]progress[^\d$(]{0,40}" + _NUM, line, re.I)
        if not m:
            continue
        raw = float(m.group(1).replace(",", ""))
        if raw <= 0:
            continue
        fits = [(s, raw * s / ppe_net_usd) for s in (1, 1e3, 1e6) if 0.0005 <= raw * s / ppe_net_usd <= 3]
        if len(fits) == 1:
            s, ratio = fits[0]
            return {"cip_usd": raw * s, "pct_net_block": round(ratio * 100, 1)}
    return None


# ------------------------------------------------------------------ filings text
def _get(url: str, timeout: int = 60):
    r = requests.get(url, headers=SEC_HEADERS, timeout=timeout)
    time.sleep(DELAY_SEC)
    return r


def latest_earnings_release(cik: str) -> tuple[str, str] | None:
    """(text, label) of the latest earnings 8-K's press release exhibit
    (Item 2.02, EX-99.x) -- where US companies actually give guidance."""
    r = _get(f"{SEC_DATA}/submissions/CIK{cik}.json", 20)
    if r.status_code != 200:
        return None
    recent = (r.json().get("filings") or {}).get("recent") or {}
    forms, items = recent.get("form") or [], recent.get("items") or []
    idx = next((i for i, f in enumerate(forms) if f == "8-K" and "2.02" in (items[i] if i < len(items) else "")), None)
    if idx is None:
        return None
    acc = recent["accessionNumber"][idx].replace("-", "")
    base = f"{SEC_WWW}/Archives/edgar/data/{int(cik)}/{acc}"
    d = _get(f"{base}/index.json", 20)
    if d.status_code != 200:
        return None
    names = [x.get("name", "") for x in (d.json().get("directory", {}).get("item") or [])]
    ex = next((n for n in names if re.search(r"(?:ex|exhibit)[-_.]?0?99", n, re.I) and n.lower().endswith((".htm", ".html"))), None)
    if not ex:
        return None
    r = _get(f"{base}/{ex}")
    if r.status_code != 200:
        return None
    return india_html_to_text(r.text), f"earnings release ({recent['filingDate'][idx]})"


def india_html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<ix:header\b.*?</ix:header>", " ", html)
    html = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n\n", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    html = _html.unescape(html).replace("\xa0", " ")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", html)).strip()


# ------------------------------------------------------------------ text lenses
_RANGE = re.compile(r"\$\s?([\d.,]+)\s?(million|billion|M|B)?\s?(?:to|and|-|–|—)\s?\$?\s?([\d.,]+)\s?(million|billion|M|B)", re.I)
_REV_WORD = re.compile(r"\b(?:net sales|net revenues?|revenues?|sales)\b", re.I)
_GUIDE_WORD = re.compile(r"\b(?:expects?|guidance|outlook|anticipates?|forecasts?|projects?)\b", re.I)
_YEAR = re.compile(r"\b(?:fiscal(?: year)?|FY|full[- ]year)\s?'?((?:20)?\d{2})\b|\b(20\d{2})\b", re.I)


def _usd(v: str, unit: str | None) -> float:
    x = float(v.replace(",", ""))
    u = (unit or "").lower()
    return x * (1e9 if u in ("billion", "b") else 1e6 if u in ("million", "m") else 1)


# India's guidance regexes were written against short Indian annual-report
# blurbs. Run over a full US 10-K they also match sentences that merely
# contain "expects ... revenue ... N%": "the Company expects 66% of total
# deferred revenue to be realized in less than a year" (Apple), a risk
# factor saying any guidance "may turn out to be inaccurate" (IRMD), margins
# and tax rates. None of those is a growth guide.
_GUIDE_NOISE = re.compile(
    r"deferred revenue|remaining performance|to be (?:realized|recogni[sz]ed)|effective tax|tax rate|margin|"
    r"dividend|interest rate|may turn out|no assurance|cannot assure|could differ|actual results|"
    r"forward-looking|risk factor|failure to (?:meet|achieve)|if we (?:fail|are unable|do not)|"
    r"we (?:have|may|might) (?:given|give|provide)", re.I)
_GROWTHISH = re.compile(r"\b(?:grow\w*|growth|increase\w*|higher|expan\w+)\b", re.I)


def find_guidance(text: str, name: str | None, source: str, revenue: tuple[int, float] | None) -> dict | None:
    """India's find_guidance rules (self-referencing sentence, a guide/expect/
    target verb, a growth word and a percentage near it) minus the US
    false positives above; failing an explicit percentage, a dollar revenue
    range for the year after the last reported full year, converted to a
    growth percentage and marked derived. Skipped rather than guessed when
    the guided year isn't clearly the next one."""
    short = india._short_name(name)
    self_rx = re.compile(india.SELF.pattern + (r"|\b" + re.escape(short) + r"\b" if short else ""), re.I)
    unquantified = None
    sents = [x for x in india.sentences(text) if not _GUIDE_NOISE.search(x)]
    for sent in sents:
        if not self_rx.search(sent):
            continue
        vm = india.GUIDANCE_VERB_RX.search(sent)
        if vm:
            window = sent[max(0, vm.start() - 60):vm.end() + 80]
            if india.GUIDANCE_GROWTH_WORD_RX.search(window) and _GROWTHISH.search(window):
                pm = india.GUIDANCE_PCT_RX.search(window)
                if pm:
                    return {"pct": float(pm.group(1)), "text": sent, "source": source}
        if unquantified is None and india.GUIDANCE_FLAG_RX.search(sent):
            unquantified = sent
    if revenue:
        fy_year, prior = revenue
        for sent in sents:
            if not (_GUIDE_WORD.search(sent) and _REV_WORD.search(sent)):
                continue
            m = _RANGE.search(sent)
            if not m:
                continue
            years = set()
            for g1, g2 in _YEAR.findall(sent):
                y = g1 or g2
                if y:
                    years.add(int("20" + y) if len(y) == 2 else int(y))
            if fy_year + 1 not in years:
                continue
            lo, hi = _usd(m.group(1), m.group(2) or m.group(4)), _usd(m.group(3), m.group(4))
            if not (0.2 * prior < (lo + hi) / 2 < 5 * prior):
                continue
            return {"pct": round(((lo + hi) / 2 / prior - 1) * 100, 1), "derived": True, "text": sent, "source": source}
    return {"pct": None, "text": unquantified, "source": source} if unquantified else None


def find_capacity_util(text: str, name: str | None, source: str) -> dict | None:
    short = india._short_name(name)
    self_rx = re.compile(india.SELF.pattern + (r"|\b" + re.escape(short) + r"\b" if short else ""), re.I)
    for sent in india.sentences(text):
        if self_rx.search(sent) and india.CAPACITY_UTIL_RX.search(sent) and india.CAPACITY_RISE_RX.search(sent) \
                and _NEAR_TERM_US.search(sent):
            return {"text": sent, "source": source}
    return None


def find_product_pivot(text: str, name: str | None, source: str) -> dict | None:
    return india.find_product_pivot(text, name, source)


# ------------------------------------------------------------------ one company
def lens_fields(cik: str, name: str | None, pe: float | None, tenk: tuple[str, str] | None) -> dict:
    """Every lens field for one company, in the field names India's records
    and the dashboard already use. Network errors on any one source leave
    that lens empty, never raise -- a flaky SEC call must not cost a run."""
    out: dict = {"has_lens_data": False}
    facts = release = None
    try:
        facts = company_facts(cik)
    except requests.RequestException:
        pass
    try:
        release = latest_earnings_release(cik)
    except (requests.RequestException, ValueError):
        pass
    sources = [s for s in (release, tenk) if s]     # the release first: that's where US guidance lives
    revenue = annual_revenue(facts) if facts else None

    guidance = capacity = pivot = None
    if release:
        # Guidance is read from the earnings release only. US companies give
        # it there, not in the 10-K, and a 10-K is a wall of risk-factor
        # boilerplate that the shared guidance patterns misread ("we expect
        # to grow further through acquisitions" -- a real false positive).
        guidance = find_guidance(release[0], name, release[1], revenue)
    for text, label in sources:
        capacity = capacity or find_capacity_util(text, name, label)
        pivot = pivot or find_product_pivot(text, name, label)

    series = quarterly_net_income(facts) if facts else []
    turned = india.pat_turnaround([v for _, v in series])
    cwip = cwip_from_text(tenk[0], net_ppe(facts)) if (tenk and facts) else None
    cwip_pct = cwip["pct_net_block"] if cwip else None
    overhang, heavy = india.capex_flags(pe, cwip_pct)
    gpct = guidance.get("pct") if guidance else None

    out.update({
        "has_lens_data": bool(facts or sources),
        "cwip_usd_m": round(cwip["cip_usd"] / 1e6, 1) if cwip else None,
        "cwip_pct_net_block": cwip_pct,
        "capex_overhang": overhang,
        "capex_heavy": heavy,
        "pat_turnaround": turned,
        "pat_series_usd_m": [[e, round(v / 1e6, 2)] for e, v in series],
        "guidance_pct": gpct,
        "guidance_over15": gpct is not None and gpct > india.GUIDANCE_OVER_PCT,
        "guidance_flag": guidance is not None,
        "guidance_derived": bool(guidance and guidance.get("derived")),
        "guidance_note": (f"Auto-detected, unverified ({guidance['source']}"
                          f"{'; percentage derived from the guided dollar range vs last fiscal year' if guidance.get('derived') else ''}"
                          f"): “{guidance['text']}”") if guidance and guidance.get("text") else None,
        "capacity_util_flag": capacity is not None,
        "capacity_util_note": (f"Auto-detected, unverified ({capacity['source']}): “{capacity['text']}”" if capacity else None),
        "product_pivot_flag": pivot is not None,
        "product_pivot_note": (f"Auto-detected, unverified ({pivot['source']}): “{pivot['text']}”" if pivot else None),
    })
    return out
