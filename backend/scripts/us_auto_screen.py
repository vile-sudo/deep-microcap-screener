"""
Daily US auto-screen: find US-listed companies that describe their own
moat in an SEC filing, and add the best of them to the dashboard's US
board, marked "Auto-added" -- the US-market counterpart to
auto_screen.py, same "any one of the evidence paths is enough" spirit,
adapted to what SEC EDGAR (free, unlimited) and Finnhub's free tier
(60 calls/min) can actually see. Read auto_screen.py's own docstring for
the full rules; this one documents only what's genuinely different.

    cd backend
    python scripts/us_auto_screen.py                 # screen and add (defaults below)
    python scripts/us_auto_screen.py --dry-run
    python scripts/us_auto_screen.py --max-add 10 --budget 300

Needs FINNHUB_API_KEY (free, finnhub.io/register) as an env var.

Differences from the India version, and why
---------------------------------------------
- Universe: SEC's own company_tickers.json (ticker -> CIK for every
  US-listed company, one free file, no auth) instead of BSE's active-scrip
  list. Unlike BSE's file, it carries no market cap, so every surviving
  candidate still needs a live Finnhub /stock/profile2 call before the
  size-band filter can apply at all -- size the --budget accordingly.
- Moat evidence: SEC EDGAR's own submissions API finds the latest 10-K,
  fetched and stripped the same way auto_screen.py handles an annual
  report PDF, regexed for US filing language ("we believe we are the
  leading/only/one of a limited number of providers of...", a stated
  market-share %, "we were the first to..."), niche + a US regulatory-
  approval body (FDA, USDA, FAA, EPA, ISO, UL, NRC, FCC, ...) in place of
  RDSO/DRDO/ISRO/NABL/ASME.
- Import substitution has no US analogue; repurposed as "reshoring /
  reduced China-supplier dependence" -- a real, common post-2020 10-K
  theme, not dropped outright.
- Promoter % (India: 40-75% typical) has no US equivalent. The closest
  substitute is insider ownership (officers+directors), which runs
  5-20% on a US small/microcap -- a different scale, gated from scratch,
  not ported. Computed from Finnhub's free /stock/insider-transactions
  (most recent per-insider share count, summed, over shares outstanding).
  /stock/ownership (13F institutional %) is confirmed NOT on the free
  tier ({"error":"You don't have access to this resource."}) -- so
  institutional ownership is left OUT of the score below, not faked.
- ROCE isn't a standard US-reported ratio; Finnhub's /stock/metric
  exposes roeTTM and roiTTM/roiAnnual directly (confirmed live), used in
  ROE's and ROCE's place respectively.
- Shareholder-count "hidden-ness" proxy: a 10-K's "approximately N
  holders of record" line is extracted the same way, but is a weaker
  signal here given pervasive US street-name/DTC nominee holding --
  kept, documented as lower-confidence.

This is a first-pass calibration, not a final one -- the thresholds below
are a starting point, meant to be revisited once real admissions show
whether they're too loose or too strict (see the Phase 1/2 plan).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

import us_lenses as lenses   # the guidance / PAT / capacity / pivot / CWIP lenses (shares India's rules)

SCRIPTS = Path(__file__).resolve().parent
BACKEND = SCRIPTS.parent
ROOT = BACKEND.parent
COMPANIES_US = BACKEND / "data" / "companies_us_raw.json"
STATE = ROOT / "automation" / "data" / "us-auto-screen.json"

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
FINNHUB = "https://finnhub.io/api/v1"
SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
SEC_HEADERS = {"User-Agent": "Deep Sweep research contact@pkresearch.in"}
SEC_TICKERS_URL = f"{SEC_WWW}/files/company_tickers.json"

DELAY_FINNHUB = 1.1     # 60/min free tier -- stay comfortably under
DELAY_SEC = 0.15        # SEC asks for a fair-use rate under ~10 req/s
RECHECK_DAYS = 90
MAX_CONSECUTIVE_ERRORS = 8   # Finnhub failing this many times in a row = down/throttling; stop, don't grind

# First-pass thresholds -- $m market cap band (US microcap convention runs
# higher than India's Rs equivalent), ROE floor, holders-of-record ceiling.
CAP_MIN, CAP_MAX = 30, 2000
ROE_MIN = 5
HOLDERS_MAX = 25000
PE_PENALTY_MIN = 60

# SPACs/blank-check, REITs, banks, insurers, ETFs/trusts -- not the target
# "hidden operating-company moat" type, mirrors auto_screen.py's SKIP_NAME.
SKIP_NAME = re.compile(r"\b(acquisition corp|capital corp|holdings? trust|\breit\b|bancorp|bancshares|"
                       r"financial group|insurance|reinsurance|\betf\b|index fund|blank check)\b", re.I)
# Ticker-suffix noise: warrants, units, rights, preferred share classes.
SKIP_TICKER = re.compile(r"[.\-](WT|WS|U|R|RT|W)$")

# Deliberately narrower than a "company"/"firm"/"entity" catch-all: a full
# 10-K (unlike screener.in's short, curated "About"/"Key Points" blurb that
# auto_screen.py searches first) is mostly legal/compensation/risk-factor
# boilerplate, where generic words like "only if... the Company" (an
# executive-comp vesting clause) false-match a too-permissive _MAKER --
# confirmed live against a real 10-K during testing. Requiring a specific
# business-descriptor noun avoids that whole false-positive class.
_MAKER = r"(?:manufacturer|player|players|maker|makers|producer|producers|supplier|suppliers|exporter|exporters|provider|providers)"
EVIDENCE = {
    # "onshor(e|ing)" deliberately excludes bare "onshore" -- confirmed live
    # that oil-and-gas 10-Ks use "onshore and offshore production" to mean
    # drilling location, nothing to do with reshoring manufacturing; only
    # the "-ing" gerund form (the reshoring-as-a-strategy usage) counts.
    "reshoring": re.compile(r"reshor(?:e|ing)\w*|onshoring\b|reduc\w* (?:our |the )?(?:reliance|dependence) on (?:China|Chinese|overseas|foreign) suppl\w*|"
                            r"domestic(?:ally)? (?:sourced|manufactured|produced) supply chain|near-?shoring\b", re.I),
    "leading_provider": re.compile(r"\bwe believe (?:we are|the Company is)\s+(?:a |the )?(?:leading|one of the leading)\b|"
                                   r"\b(?:the |a )?(?:leading|largest)\s+(?:\w+\s+){0,4}" + _MAKER + r"\b", re.I),
    "market_share": re.compile(r"\b\d{1,2}(?:\.\d+)?\s?%\s+(?:\w+\s+){0,3}market share|"
                               r"market share of\s+(?:about|around|over|approximately|~)?\s*\d{1,2}(?:\.\d+)?\s?%", re.I),
    "sole_provider": re.compile(r"\b(?:only|sole|one of a (?:limited|small) number of|among a (?:limited|small) number of)\b"
                                r"\s+(?:[\w-]+\s+){0,4}" + _MAKER + r"\b", re.I),
    "first_mover": re.compile(r"\b(?:first company|first to|pioneer(?:ed|s|ing)?)\b\s+(?:[\w-]+\s+){0,6}"
                              r"to (?:develop|introduce|offer|commercialize)", re.I),
}
STRONG = ("leading_provider", "market_share", "sole_provider", "first_mover")
NICHE = re.compile(r"\bniche\b", re.I)
APPROVAL = re.compile(r"\b(FDA|USDA|FAA|EPA|ISO ?\d*|UL|NRC|FCC|USP|cGMP|ITAR|AS ?9100|NADCAP)\b")
SELF = re.compile(r"\b(?:the company|we|our|us|the corporation)\b", re.I)
LABEL = {"reshoring": "reshoring / reduced China-supplier dependence", "leading_provider": "a leading US provider",
         "market_share": "a stated market share", "sole_provider": "only / one of a limited number of providers",
         "first_mover": "first to / pioneer", "niche": "a niche segment with a regulatory approval"}
HOLDERS_RX = re.compile(r"(?:approximately|about)\s+([\d,]{2,7})\s+(?:holders of record|shareholders of record|stockholders of record)", re.I)


def _html_to_text(html: str) -> str:
    """Insert paragraph breaks at block-level tags before stripping the
    rest, so sentences() below (which splits on blank lines the way a
    PDF-extracted annual report naturally has them) has something to
    split on -- a naive tag-strip collapses an EDGAR filing into one
    unbroken blob."""
    html = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n\n", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"&nbsp;|&#160;", " ", html)
    html = re.sub(r"&amp;", "&", html)
    html = re.sub(r"&#8217;|&rsquo;", "'", html)
    html = re.sub(r"&#8220;|&#8221;|&ldquo;|&rdquo;", '"', html)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def sentences(text: str) -> list[str]:
    """Sentences, never running across a blank line -- same rule
    auto_screen.py uses for PDF-extracted text."""
    out = []
    for block in re.split(r"\n\s*\n", text or ""):
        block = re.sub(r"\s+", " ", block).strip()
        out += [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z(\"“])", block) if 25 < len(x.strip()) < 450]
    return out


def _short_name(name: str | None) -> str | None:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z&-]+", name or "")
             if w.lower() not in ("inc", "incorporated", "corp", "corporation", "company", "co",
                                  "ltd", "the", "group", "holdings", "technologies")]
    return words[0] if words and (len(words[0]) >= 4 or (len(words[0]) == 3 and words[0].isupper())) else None



# A sentence naming an acquisition is ambiguous about who a moat claim in
# it actually describes -- "we acquired TerraSource, a market-leading
# manufacturer of..." is a real claim about the ACQUIRED company, not the
# filer, and self_rx alone can't tell the difference (it only checks that
# "we"/the company is mentioned somewhere in the sentence, not who the
# claim's subject is). Confirmed live against two real 10-Ks (ASTEC,
# Standex) where this exact pattern produced a false positive. Skipping
# any acquisition-narrative sentence outright is blunt -- it also throws
# away genuine claims that happen to co-occur with one -- but matches this
# screen's own stated bias throughout: prefer a missed real claim over an
# admitted false one.
ACQUISITION_NARRATIVE = re.compile(r"\b(?:acqui(?:re|red|ring|sitions?)|merger|combined with|subsidiary)\b", re.I)
# Same problem, different direction: "we purchase raw materials from
# leading suppliers" describes the filer's OWN VENDORS, not the filer --
# confirmed live (Astec Industries). self_rx alone can't tell "we ARE a
# leading X" from "we buy FROM leading X" since both contain "we".
#
# Widened after the first real (non-dry-run) run admitted two false
# positives: Pioneer Power ("Our LARGEST suppliers ... included Taylor
# Power Systems") and Beta Bionics ("Unomedical, PMC and Maxon are our ONLY
# suppliers of infusion sets") -- both are supplier disclosures, and the
# old "our suppliers" pattern missed any modifier between "our" and
# "suppliers". Any sentence whose subject is the filer's supply chain is
# out, whatever the adjective.
VENDOR_NARRATIVE = re.compile(r"\b(?:purchase|source|sourc(?:e|ed|ing)|buy|bought|procure[ds]?|vendors?)\b.{0,120}\bfrom\b|"
                              r"\b(?:our|its|their)\s+(?:[\w-]+\s+){0,3}?(?:suppliers?|vendors?|contract manufacturers?|"
                              r"third[- ]party manufacturers?)\b", re.I)


def find_evidence(text: str, name: str | None, need_self: bool) -> dict[str, dict]:
    short = _short_name(name)
    self_rx = re.compile(SELF.pattern + (r"|\b" + re.escape(short) + r"\b" if short else ""), re.I)
    found: dict[str, dict] = {}
    niche = approval = None
    for sent in sentences(text):
        if need_self and not self_rx.search(sent):
            continue
        if need_self and (ACQUISITION_NARRATIVE.search(sent) or VENDOR_NARRATIVE.search(sent)):
            continue
        for key, rx in EVIDENCE.items():
            if key in found:
                continue
            m = rx.search(sent)
            if m and (not need_self or self_rx.search(sent[max(0, m.start() - 100):m.end()])):
                found[key] = {"text": sent, "source": "10-K"}
        if niche is None and NICHE.search(sent) and (not need_self or self_rx.search(sent)):
            niche = sent
        if approval is None and APPROVAL.search(sent) and (not need_self or self_rx.search(sent)):
            approval = sent
    if niche and approval:
        found["niche"] = {"text": niche if niche == approval else f"{niche} … {approval}", "source": "10-K"}
    return found


def find_holders(text: str) -> int | None:
    m = HOLDERS_RX.search(text)
    return int(m.group(1).replace(",", "")) if m else None


# ------------------------------------------------------------------ universe
def universe() -> list[dict]:
    """Every US-listed ticker with its CIK, from SEC's own free map. Unlike
    BSE's active-scrip list (which hands auto_screen.py market cap for
    free), this carries no size info, so every surviving candidate still
    needs a live Finnhub call before the size-band filter applies."""
    r = requests.get(SEC_TICKERS_URL, headers=SEC_HEADERS, timeout=30)
    r.raise_for_status()
    out = []
    for row in r.json().values():
        ticker, name = row.get("ticker", ""), row.get("title", "")
        if not ticker or SKIP_TICKER.search(ticker) or SKIP_NAME.search(name):
            continue
        out.append({"ticker": ticker, "name": name, "cik": str(row["cik_str"]).zfill(10)})
    return out


# ------------------------------------------------------------------ Finnhub
def finnhub_profile(ticker: str) -> dict | None:
    r = requests.get(f"{FINNHUB}/stock/profile2", params={"symbol": ticker, "token": FINNHUB_KEY}, timeout=20)
    time.sleep(DELAY_FINNHUB)
    if r.status_code != 200:
        return None
    d = r.json()
    return d if d.get("marketCapitalization") else None


def finnhub_quote(ticker: str) -> dict:
    r = requests.get(f"{FINNHUB}/quote", params={"symbol": ticker, "token": FINNHUB_KEY}, timeout=20)
    time.sleep(DELAY_FINNHUB)
    return r.json() if r.status_code == 200 else {}


def finnhub_metrics(ticker: str) -> dict:
    r = requests.get(f"{FINNHUB}/stock/metric", params={"symbol": ticker, "metric": "all", "token": FINNHUB_KEY}, timeout=20)
    time.sleep(DELAY_FINNHUB)
    return (r.json() or {}).get("metric") or {} if r.status_code == 200 else {}


def finnhub_insider_pct(ticker: str, shares_out_m: float | None) -> float | None:
    """Approximate current insider ownership from free /stock/insider-
    transactions: each row's `share` is that insider's total holding AFTER
    that transaction, so the most recent row per name is a reasonable
    current estimate. This is the closest free substitute for India's
    promoter-% gate, NOT institutional % -- see the module docstring."""
    if not shares_out_m:
        return None
    r = requests.get(f"{FINNHUB}/stock/insider-transactions", params={"symbol": ticker, "token": FINNHUB_KEY}, timeout=20)
    time.sleep(DELAY_FINNHUB)
    if r.status_code != 200:
        return None
    rows = (r.json() or {}).get("data") or []
    latest: dict[str, tuple[str, float]] = {}
    for row in rows:
        name, d = row.get("name"), row.get("filingDate") or ""
        if name and (name not in latest or d > latest[name][0]):
            latest[name] = (d, row.get("share") or 0)
    total = sum(v[1] for v in latest.values())
    return round(100 * total / (shares_out_m * 1_000_000), 2) if total else None


# ------------------------------------------------------------------ SEC EDGAR
def latest_10k_text(cik: str) -> tuple[str, str] | None:
    """(text, source_label) for the latest 10-K's primary document, or None."""
    r = requests.get(f"{SEC_DATA}/submissions/CIK{cik}.json", headers=SEC_HEADERS, timeout=20)
    time.sleep(DELAY_SEC)
    if r.status_code != 200:
        return None
    recent = (r.json().get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    idx = next((i for i, f in enumerate(forms) if f in ("10-K", "10-K/A")), None)
    if idx is None:
        return None
    accession = recent["accessionNumber"][idx].replace("-", "")
    doc = recent["primaryDocument"][idx]
    year = (recent["filingDate"][idx] or "")[:4]
    cik_int = str(int(cik))
    url = f"{SEC_WWW}/Archives/edgar/data/{cik_int}/{accession}/{doc}"
    r = requests.get(url, headers=SEC_HEADERS, timeout=60)
    time.sleep(DELAY_SEC)
    if r.status_code != 200 or len(r.content) > 30_000_000:
        return None
    return _html_to_text(r.text), f"10-K ({year})"


# ------------------------------------------------------------------ rules
def gates(cap_m: float | None, roe: float | None, holders: int | None) -> list[str]:
    fails = []
    if cap_m is None or not (CAP_MIN <= cap_m <= CAP_MAX):
        fails.append(f"market cap ${cap_m}m outside ${CAP_MIN}-{CAP_MAX}m")
    if roe is not None and roe <= ROE_MIN:
        fails.append(f"ROE {roe}% not above {ROE_MIN}%")
    if holders is not None and holders >= HOLDERS_MAX:
        fails.append(f"{holders:,} holders of record -- over the {HOLDERS_MAX:,} limit")
    return fails


def score(cap_m, roe, roi, insider_pct, holders, ev, pe=None) -> tuple[float, dict, list[str]]:
    """Five pillars, not six -- institutional ownership is left out rather
    than faked (see module docstring: not on Finnhub's free tier). Scored
    out of a possible 85, not rescaled to 100, so the gap is honest rather
    than implying a precision this pass doesn't have."""
    roe, roi = roe or 0, roi or 0
    strong = sum(1 for k in STRONG if k in ev)
    s_moat = min(25, 8 * strong + (4 if "niche" in ev else 0)
                 + (5 if roi >= 20 else 4 if roi >= 12 else 3 if roi >= 8 else 2))
    s_reshoring = 20 if "reshoring" in ev else 0
    s_insider = (15 if insider_pct is not None and 5 <= insider_pct <= 25 else
                 10 if insider_pct is not None and insider_pct > 25 else
                 6 if insider_pct is not None else 4)
    s_cov = ((8 if (cap_m or 0) <= 300 else 6 if (cap_m or 0) <= 800 else 3)
             + (7 if holders is None else 7 if holders < 3000 else 5 if holders < 10000 else 3))
    s_fin = min(10, (5 if roe >= 15 else 3 if roe >= 8 else 1) + (3 if roi >= 10 else 1) + (2 if roe >= 15 else 0))

    penalties = []
    if holders is not None and holders < 1000:
        penalties.append((f"only {holders:,} holders of record", 5))
    if pe is not None and pe > PE_PENALTY_MIN:
        penalties.append((f"P/E {pe} above {PE_PENALTY_MIN}", 3))
    raw = s_moat + s_reshoring + s_insider + s_cov + s_fin
    pen = sum(v for _, v in penalties)
    pillars = {"s_moat": s_moat, "s_reshoring": s_reshoring, "s_insider": s_insider,
               "s_undercovered": s_cov, "s_financials": s_fin}
    return round(raw - pen, 1), {**pillars, "score": raw, "risk_penalty": pen}, [f"{t} (-{v})" for t, v in penalties]


def qualifying_paths(ev: dict, lens: dict) -> list[str]:
    """Which of India's five independent paths this company clears -- any one
    is enough (see auto_screen.py's docstring): moat evidence, guidance above
    GUIDANCE_OVER_PCT, a PAT turnaround, a capacity-utilisation ramp, a
    product-mix pivot."""
    paths = []
    if ev:
        paths.append("moat evidence")
    if lens.get("guidance_over15"):
        paths.append("management guidance")
    if lens.get("pat_turnaround"):
        paths.append("PAT turnaround")
    if lens.get("capacity_util_flag"):
        paths.append("capacity utilisation")
    if lens.get("product_pivot_flag"):
        paths.append("product-mix pivot")
    return paths


def record(cand: dict, profile: dict, quote: dict, metrics: dict, ev: dict, holders: int | None,
           insider_pct: float | None, today: str, fails: list[str], source_label: str,
           lens: dict | None = None) -> dict:
    lens = lens or {}
    cap_m = profile.get("marketCapitalization")
    roe = metrics.get("roeTTM")
    roi = metrics.get("roiTTM") or metrics.get("roiAnnual")
    pe = quote.get("c") / metrics["epsTTM"] if quote.get("c") and metrics.get("epsTTM") else None
    pe = round(pe, 1) if pe else None
    # the CWIP flags need the P/E, which is only known once Finnhub has answered
    lens = {**lens, **dict(zip(("capex_overhang", "capex_heavy"), lenses.india.capex_flags(pe, lens.get("cwip_pct_net_block"))))}
    final, parts, penalty_detail = score(cap_m, roe, roi, insider_pct, holders, ev, pe)
    paths = qualifying_paths(ev, lens)

    evidence_lines = [f"{LABEL[k]} ({v['source']}): “{v['text']}”" for k, v in ev.items()]
    if evidence_lines:
        moat_note = "Auto-screen found the company describing itself with: " + " · ".join(evidence_lines)
    elif "PAT turnaround" in paths:
        moat_note = "No moat evidence found -- added on the PAT-turnaround gate alone (see pat_series_usd_m)."
    elif "capacity utilisation" in paths:
        moat_note = "No moat evidence found -- added on the capacity-utilisation gate alone (see capacity_util_note below)."
    elif "product-mix pivot" in paths:
        moat_note = "No moat evidence found -- added on the product-pivot gate alone (see product_pivot_note below)."
    else:
        moat_note = "No moat evidence found -- added on the management-guidance gate alone (see guidance_note below)."
    warn = [f"Auto-added by the daily US screen: numbers are from Finnhub and SEC EDGAR, the evidence is the "
            f"company's own words in its {source_label} and latest earnings release matched by rules. "
            f"Nobody has researched this company yet."]
    if fails:
        warn.append("Added despite failing the fundamentals gates below -- it cleared one of the five paths "
                    "(moat, guidance, PAT turnaround, capacity utilisation, product pivot) on its own, which this "
                    "screen treats as sufficient by itself; financials are not a blocker for that path.")

    return {
        "code": cand["ticker"],
        "market": "US",
        "name": profile.get("name") or cand["name"],
        "sector": profile.get("finnhubIndustry"),
        "theme": profile.get("finnhubIndustry"),
        "screen": "us-auto",
        "source": "us-auto",
        "rubric": "us-auto",
        "claim_grade": "company-stated",
        "added_on": today,
        "cleared_paths": paths,
        "market_cap_usd": cap_m,
        "price": quote.get("c"),
        "pe": pe,
        "roe_pct": roe,
        "insider_pct": insider_pct,
        "num_shareholders": holders,
        "business": (profile.get("finnhubIndustry") or "") + " -- " + (cand.get("name") or ""),
        "moat_note": moat_note,
        "evidence_sources": [f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cand['cik']}&type=10-K"],
        "final_score": final,
        "adj_score": final,
        "penalty_detail": penalty_detail,
        "gate_failures": fails,
        "warnings": warn,
        "score_rationale": "Automated five-pillar score (out of 85 -- no institutional-ownership pillar on "
                           "Finnhub's free tier) from Finnhub/SEC numbers and matched statements "
                           "(see backend/scripts/us_auto_screen.py and the How this board is built page).",
        **lens,
        **parts,
    }


# ------------------------------------------------------------------ run
def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def enrich_board(companies: list[dict], today: str) -> int:
    """Refresh the lens fields (guidance, PAT turnaround, capacity ramp, product
    pivot, CWIP) on every company already on the US board, the way India's
    nightly refresh keeps its board current -- without this the columns
    would only ever be filled for a company on the day it was added.
    Only the lens fields are touched: scores, evidence and anything hand-
    edited stay as they are. A failed fetch leaves that company's old
    values in place."""
    try:
        listing = requests.get(SEC_TICKERS_URL, headers=SEC_HEADERS, timeout=30).json()
    except (requests.RequestException, ValueError) as e:
        print(f"us-auto-screen: could not fetch SEC's ticker map ({e}); board lenses not refreshed")
        return 0
    ciks = {r["ticker"].upper(): str(r["cik_str"]).zfill(10) for r in listing.values()}
    n = 0
    for c in companies:
        cik = ciks.get(str(c.get("code", "")).upper())
        if not cik:
            continue
        try:
            lens = lenses.lens_fields(cik, c.get("name"), c.get("pe"), latest_10k_text(cik))
        except requests.RequestException as e:
            print(f"  enrich {c.get('code')}: {str(e)[:70]}")
            continue
        c.update(lens)
        c["lens_updated"] = today
        n += 1
    print(f"us-auto-screen: refreshed lenses on {n} of {len(companies)} board companies")
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-add", type=int, default=10)
    ap.add_argument("--budget", type=int, default=300, help="most candidates to fetch Finnhub profiles for in one run")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--enrich-only", action="store_true", help="only refresh the lens fields on the current board; add nothing")
    ap.add_argument("--no-enrich", action="store_true", help="skip refreshing the current board's lens fields")
    args = ap.parse_args()

    if not FINNHUB_KEY:
        print("us-auto-screen: FINNHUB_API_KEY is not set", file=sys.stderr)
        return 2

    today = datetime.now(timezone.utc).date().isoformat()
    companies = load_json(COMPANIES_US, [])
    on_board = {str(c.get("code", "")).upper() for c in companies}
    state = load_json(STATE, {"checked": {}, "runs": []})
    recheck_before = (date.fromisoformat(today) - timedelta(days=RECHECK_DAYS)).isoformat()

    enriched = 0 if args.no_enrich else enrich_board(companies, today)
    if args.enrich_only:
        if enriched and not args.dry_run:
            COMPANIES_US.write_text(json.dumps(companies, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return 0

    pool = []
    for cand in universe():
        if cand["ticker"].upper() in on_board:
            continue
        seen = state["checked"].get(cand["cik"])
        if seen and (seen["result"] == "added" or seen["on"] >= recheck_before):
            continue
        pool.append(cand)
    # SEC's own company_tickers.json is sorted by market cap descending
    # (confirmed live: NVDA, AAPL, GOOGL, MSFT, AMZN, ... first) -- a
    # budget-limited run that kept that order would burn its whole budget
    # on mega-caps that can never pass CAP_MIN/CAP_MAX and never reach a
    # real microcap candidate at all (confirmed: 1,200 fetched, 0 in the
    # size band). Shuffled before the stable priority sort below, so
    # "unchecked first, then oldest-checked first" still holds, just no
    # longer correlated with market-cap rank.
    random.shuffle(pool)
    pool.sort(key=lambda c: (c["cik"] in state["checked"], state["checked"].get(c["cik"], {}).get("on", "")))
    print(f"us-auto-screen: {len(pool)} tickers eligible to check today; budget {args.budget} Finnhub profiles, "
          f"adding at most {args.max_add}")

    passes, fetched, outcomes = [], 0, {}
    consecutive_errors = 0
    for cand in pool:
        if fetched >= args.budget or len(passes) >= args.max_add * 3:
            break
        try:
            profile = finnhub_profile(cand["ticker"])
        except requests.RequestException as e:
            outcomes[cand["cik"]] = ("error", str(e)[:80])
            consecutive_errors += 1
            # A real run spent ~2.5 hours here: 370 of 787 calls were 20-second
            # Finnhub read timeouts back to back. When it's failing this
            # consistently it's down or throttling us, and grinding on only
            # wastes the run -- stop and let the next scheduled run retry.
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"us-auto-screen: {consecutive_errors} Finnhub failures in a row ({str(e)[:60]}); "
                      f"stopping early, the next run will pick these up again")
                break
            continue
        consecutive_errors = 0
        fetched += 1
        if not profile:
            outcomes[cand["cik"]] = ("no Finnhub profile", "")
            continue
        cap_m = profile.get("marketCapitalization")
        if cap_m is None or not (CAP_MIN <= cap_m <= CAP_MAX):
            outcomes[cand["cik"]] = ("outside size band", f"${cap_m}m")
            continue
        # India skips finance, real estate, hospitality, media, software,
        # retail, utilities... by sector before fetching anything -- same
        # list, applied to Finnhub's industry (the first real run admitted
        # an insurer and a financial-services firm without it).
        industry = profile.get("finnhubIndustry") or ""
        if lenses.india.EXCLUDED_SECTORS.search(industry):
            outcomes[cand["cik"]] = ("excluded sector", industry)
            continue

        try:
            filing = latest_10k_text(cand["cik"])
        except requests.RequestException as e:
            outcomes[cand["cik"]] = ("error fetching 10-K", str(e)[:80])
            continue
        if not filing:
            outcomes[cand["cik"]] = ("no 10-K found", "")
            continue
        text, source_label = filing
        # India's known_to_public(): a hidden-ness test that no path can skip.
        holders = find_holders(text)
        if holders is not None and holders >= HOLDERS_MAX:
            outcomes[cand["cik"]] = ("known to the public", f"{holders:,} holders of record")
            continue
        name = profile.get("name") or cand["name"]
        ev = find_evidence(text, name, need_self=True)
        try:
            lens = lenses.lens_fields(cand["cik"], name, None, filing)
        except requests.RequestException as e:
            outcomes[cand["cik"]] = ("error fetching SEC facts", str(e)[:80])
            continue
        # any ONE of the five paths is enough -- see qualifying_paths()
        if not qualifying_paths(ev, lens):
            outcomes[cand["cik"]] = ("no qualifying evidence", "")
            continue

        try:
            quote = finnhub_quote(cand["ticker"])
            metrics = finnhub_metrics(cand["ticker"])
            insider_pct = finnhub_insider_pct(cand["ticker"], profile.get("shareOutstanding"))
        except requests.RequestException as e:
            # A transient Finnhub timeout here used to crash the whole run
            # (confirmed live: one flaky /quote call on candidate #13 lost
            # all 12 already-found passes and every already-spent Finnhub
            # call, dry-run or not -- state is only written at the very
            # end). Every other per-candidate fetch above already treats a
            # network error as "skip this one candidate", so this matches.
            outcomes[cand["cik"]] = ("error fetching Finnhub metrics", str(e)[:80])
            continue
        fails = gates(cap_m, metrics.get("roeTTM"), holders)

        rec = record(cand, profile, quote, metrics, ev, holders, insider_pct, today, fails, source_label, lens)
        passes.append((rec, cand))
        print(f"  PASS {cand['ticker']:<8} {rec['name'][:40]:<40} score {rec['final_score']:>5}  [{', '.join(rec['cleared_paths'])}]"
              + (f"  GATES FAILED: {'; '.join(fails)}" if fails else ""))

    passes.sort(key=lambda x: -x[0]["final_score"])
    added = passes[:args.max_add]
    for rec, cand in added:
        outcomes[cand["cik"]] = ("added", rec["code"])
    for rec, cand in passes[args.max_add:]:
        outcomes[cand["cik"]] = ("passed, not added today", f"score {rec['final_score']}")

    counts: dict[str, int] = {}
    for result, _ in outcomes.values():
        counts[result] = counts.get(result, 0) + 1
    print(f"us-auto-screen: fetched {fetched} profiles; " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"us-auto-screen: adding {len(added)}: " + (", ".join(f"{r['name']} ({r['final_score']})" for r, _ in added) or "none passed today"))

    if args.dry_run:
        return 0
    for cik, (result, detail) in outcomes.items():
        # A transient network failure is not a verdict on the company --
        # persisting it marked 400 tickers "checked" for RECHECK_DAYS (90)
        # after one bad Finnhub afternoon, so they'd never be retried.
        if result == "passed, not added today" or result.startswith("error"):
            continue
        state["checked"][cik] = {"on": today, "result": result, **({"detail": detail} if detail else {})}
    state["runs"] = (state.get("runs", []) + [{"on": today, "fetched": fetched, **counts,
                                                "added_codes": [r["code"] for r, _ in added]}])[-60:]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if added or enriched:
        companies.extend(r for r, _ in added)
        COMPANIES_US.write_text(json.dumps(companies, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
