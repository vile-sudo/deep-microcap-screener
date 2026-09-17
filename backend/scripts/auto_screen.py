"""
Daily auto-screen: find listed companies that pass the board's own rules and
add the best of them to the dashboard, marked "Auto-added".

    cd backend
    python scripts/auto_screen.py                 # screen and add (defaults below)
    python scripts/auto_screen.py --dry-run       # screen, print, write nothing
    python scripts/auto_screen.py --max-add 10 --budget 400

Runs in .github/workflows/daily.yml every night, before the charts are
rebuilt, so a company added today gets its chart and Market view stage today.

All the thresholds and keyword lists below are defaults only. The live
values -- editable on the dashboard, account menu -> Admin -> Logic Gates,
with no code change or deploy -- are fetched from /api/meta/logic-gates at
the start of every run (see load_gates() / apply_gates()) and override these.

Where candidates come from
--------------------------
BSE's active-scrip list (which carries market cap) narrowed to the board's
size band, plus NSE's main-board and SME lists for listings BSE doesn't have,
minus anything already on the board, already ruled out in the discovery
queue, or checked here in the last RECHECK_DAYS. New listings go first, then
the names checked longest ago. Finance, realty, hospitality, media and
software names are skipped by name before any page is fetched.

The rules (the board's own, applied to screener.in's public company page)
-------------------------------------------------------------------------
Gates -- all must pass:
  market cap  Rs 120-10,000 cr  (Rs 7,500 cr for Tier 1) -- micro and small cap
  promoter    >= 40%
  public      <= 60%
  ROCE        >= 12%
  ROE         > 5%
  shareholders < 25,000 (Tier 1) / 40,000 (Tier 2), when screener shows it
  Tier 1 = FII + DII >= 1%; Tier 2 = less (allowed, penalised)
  sector      must map onto one of the board's themes
Moat evidence -- the company describing itself, first in its "About" and
"Key Points" text on screener.in and, when that says nothing, in its latest
annual report (the public BSE / NSE filing screener links to). Only sentences
that are about the company count ("the Company", "we", "our", its name) --
"India's defence indigenisation" in an industry overview is not evidence.
At least one of:
  import substitution                 ("import substitute", "replace imports",
                                       "indigenously developed", "indigenised")
  India's leading / largest maker     ("India's largest manufacturer of ...")
  a stated India market share         ("35% market share", "market share of about 40%")
  only / one of few Indian makers     ("only manufacturer in India", "among the few ...")
  first / pioneer in India            ("first company in India to ...", "pioneer in ...")
  a niche segment WITH a qualification (the company calls its segment "niche", plus RDSO,
                                        DRDO, ISRO, USFDA, EU-GMP, NABL, AS9100, ASME...)
Score out of 100 on the board's six pillars, from the page's numbers and the
evidence found (see score() below), minus the board's penalties.

What gets added
---------------
The highest-scoring passes, at most --max-add a day (default 10). Some days
fewer than five pass -- the rules are not loosened to hit a number. Each
record says plainly that it was auto-screened: the numbers are screener.in's,
the moat text is the company describing itself (claim_grade
"company-stated"), and nobody has researched it yet.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
COMPANIES = BACKEND / "data" / "companies_raw.json"
QUEUE = ROOT / "automation" / "data" / "candidates-queue.json"
STATE = ROOT / "automation" / "data" / "auto-screen.json"
GATES_DEFAULTS_FILE = BACKEND / "data" / "logic_gates_defaults.json"
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://deep-microcap-screener.onrender.com")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-research-screener/1.0; research script, not a bulk crawler)"}
EXCHANGE_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                    "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com", "Accept": "application/json,text/csv,*/*"}
DELAY = 2.0
RECHECK_DAYS = 90

# Defaults below; overridden at startup by load_gates() from the dashboard's
# /api/meta/logic-gates (Logic Gates, in the account menu's Admin section),
# so an admin's edit there takes effect on the next run with no code change.
CAP_MIN, CAP_MAX_T1, CAP_MAX_T2 = 120, 7500, 10000
PROMOTER_MIN, PUBLIC_MAX, ROCE_MIN, ROE_MIN = 40, 60, 12, 5
INST_TIER1_MIN = 1
HOLDERS_MAX_T1, HOLDERS_MAX_T2 = 25000, 40000
PE_OVERHANG_MIN, CWIP_OVERHANG_MIN, CWIP_HEAVY_MIN = 40, 15, 25
GUIDANCE_OVER_PCT, PAT_LOOKBACK, PE_PENALTY_MIN = 15, 3, 60
IMPORT_MATERIALS: list[str] = []

SKIP_NAME = re.compile(r"\b(finance|financial|fincorp|finserv|capital|credit|leasing|investments?|holdings?|securities|"
                       r"broking|bank|insurance|realty|real estate|properties|developers|estates?|hotels?|resorts?|"
                       r"hospitality|entertainment|media|films?|broadcast|software|infotech|infosystems|trading|"
                       r"exports? & imports?|commodities)\b", re.I)

# screener.in's sector path -> the board's theme names. First match wins.
THEMES = [
    (r"aerospace|defen[cs]e", "Defence & Aerospace"),
    (r"pharmaceutical|pharma|drug", "Pharma API & Fine Chemicals"),
    (r"medical equipment|healthcare equipment|medical device", "Medical Devices & Instruments"),
    (r"hospital|healthcare service|diagnostic|testing|laborator", "Healthcare & Testing Services"),
    (r"automobile|auto component|auto parts|tyre|tractor|two wheeler|commercial vehicle", "Auto & Mobility"),
    (r"electronic|semiconductor|it hardware|computer hardware", "Electronics & Semiconductors"),
    (r"electrical|cable|wire|power equipment|transformer|renewable|solar|batter|energy equipment", "Electricals & Energy Hardware"),
    (r"chemical|agrochemical|pesticide|fertili|pigment|dye", "Specialty Chemicals"),
    (r"textile|apparel|yarn|fibre|fiber|fabric|garment", "Textiles & Technical Textiles"),
    (r"packag|plastic|paper|printing|laminat", "Packaging, Plastics & Printing"),
    (r"cement|building|construction material|ceramic|tile|sanitary|plywood|glass", "Building Products & Systems"),
    (r"food|agri|dairy|sugar|tea|coffee|edible oil|feed|seed|beverage", "Agri & Food Processing"),
    (r"household|consumer durable|appliance|personal product|furniture|kitchen", "Consumer & Appliances"),
    (r"logistic|shipping|transport|railway|marine|port|courier", "Transport & Logistics"),
    (r"water|waste|environment", "Water & Environment"),
    (r"steel|iron|metal|alumin|copper|zinc|mining|mineral|refractor|graphite|alloy", "Advanced Materials & Minerals"),
    (r"industrial machinery|capital goods|engineering|machine|casting|forging|bearing|pump|compressor|valve|"
     r"industrial product|heavy electrical|construction machinery|tools", "Machinery & Precision Manufacturing"),
    (r"gas|petrochemical|industrial gases|carbon black", "Process Industries"),
]
EXCLUDED_SECTORS = re.compile(r"financial|bank|insurance|realty|real estate|hotel|restaurant|media|entertainment|"
                              r"software|it services|telecom service|retail|trading|education|utilities", re.I)

_MAKER = r"(?:manufacturer|company|companies|player|players|maker|makers|producer|producers|supplier|suppliers|exporter|exporters|provider|providers|firm|firms|entity|entities)"
EVIDENCE = {
    "import_substitution": re.compile(r"import[- ]substitut\w*|substitut\w* (?:for |of )?imports?|replac\w* (?:the )?imports?|"
                                      r"reduc\w* (?:the )?(?:country'?s |india'?s )?(?:dependence|reliance) on imports?|"
                                      r"indigenously (?:developed|designed|manufactured|built|made)|indigeni[sz](?:ed|ing|ation of)\b", re.I),
    "leading_maker": re.compile(r"(?:india'?s|in india'?s?|asia'?s|world'?s|the world'?s|global(?:ly)?)\s+(?:single\s+)?(?:largest|leading|biggest|foremost)\b|"
                                r"\b(?:the |a )?market leader(?:s|ship)? (?:in|of|for) (?!the future|its\b|our\b|the industry|the market)[\w-]+|"
                                r"\b(?:largest|leading|biggest|foremost)\s+(?:\w+\s+){0,5}" + _MAKER + r"\b"
                                r"(?:\s+of\s+[\w\s,&/()-]{1,70}?)?\s+(?:in india|in the country|in the indian|in asia|in the world|globally)", re.I),
    "market_share": re.compile(r"\b\d{1,2}(?:\.\d+)?\s?%\s+(?:\w+\s+){0,3}market share|"
                               r"market share of\s+(?:about|around|over|nearly|approximately|~)?\s*\d{1,2}(?:\.\d+)?\s?%", re.I),
    "sole_maker": re.compile(r"\b(?:only|sole|one of (?:the )?(?:only|few|two|three|four|handful of)|among (?:the )?(?:few|only|handful))\b"
                             r"\s+(?:[\w-]+\s+){0,7}" + _MAKER + r"\b(?:\s+[\w-]+){0,8}?\s+(?:in india|in the country|indian|domestic)", re.I),
    "first_mover": re.compile(r"\b(?:first|pioneer(?:ed|s|ing)?)\b\s+(?:[\w-]+\s+){0,6}(?:in india|in the country|indian (?:company|manufacturer))", re.I),
}
STRONG = ("leading_maker", "market_share", "sole_maker", "first_mover")
NICHE = re.compile(r"\bniche\b", re.I)
APPROVAL = re.compile(r"\b(RDSO|DRDO|ISRO|HAL|BEL|BHEL|NPCIL|BARC|DGQA|CEMILAC|DGAQA|DGCA|USFDA|US ?FDA|EU[- ]GMP|NABL|AS ?9100|"
                      r"ASME|API monogram|IATF|NADCAP|Lloyd'?s Register|DNV|Indian (?:Navy|Army|Air Force|Railways)|"
                      r"railway approvals?|[Aa]pproved (?:vendor|supplier|source)|[Qq]ualified (?:vendor|supplier))\b")
# a sentence has to be about the company, not the industry
SELF = re.compile(r"\b(?:the company|your company|our company|we|our|us|the group)\b", re.I)
LABEL = {"import_substitution": "import substitution", "leading_maker": "India's leading/largest maker",
         "market_share": "a stated India market share", "sole_maker": "only / one of few Indian makers",
         "first_mover": "first / pioneer in India", "niche": "a niche segment with a qualification"}
PDFTOTEXT = shutil.which("pdftotext")
AR_PAGES = 90
# a guidance statement has to be self-referencing too, and either give a number
# or use unmistakably forward-looking language -- an order book or a capacity
# expansion is not guidance (see frontend/index.html's own "Guide %" caveat)
GUIDANCE_VERB_RX = re.compile(r"\b(?:guid(?:e|ance|ing)|expects?|targets?|aims?|anticipates?)\b", re.I)
GUIDANCE_GROWTH_WORD_RX = re.compile(r"\b(?:growth|grow(?:s|ing)?|increase|revenue|sales|turnover)\b", re.I)
GUIDANCE_PCT_RX = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?%")
GUIDANCE_FLAG_RX = re.compile(r"\b(?:revenue|sales|turnover)\b[^.;]{0,40}?\bguid(?:e|ance|ing)\b|"
                              r"\bguid(?:e|ance|ing)\b[^.;]{0,40}?\b(?:revenue|sales|turnover)\b|"
                              r"\bexpects? (?:revenue|sales|turnover) (?:to grow|growth)\b|"
                              r"\bexpects? to grow(?: (?:revenue|sales|turnover))?\b|"
                              r"\btargets? (?:revenue |sales |turnover )?growth of\b|\baims? to (?:grow|double|triple)\b", re.I)


def load_gates() -> dict:
    """The effective Logic Gates: the dashboard's live override merged over
    the built-in defaults, fetched over plain HTTP (this script has no
    database access, running in GitHub Actions) -- or the local defaults
    file alone if the site can't be reached, so a run never hard-fails on it."""
    try:
        defaults = json.loads(GATES_DEFAULTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        defaults = {"hard_gates": {}, "flags": {}, "moat_keywords": {}, "import_substitution_materials": []}
    try:
        r = requests.get(f"{DASHBOARD_URL}/api/meta/logic-gates", timeout=15)
        r.raise_for_status()
        live = r.json()
        return {k: live.get(k, defaults.get(k)) for k in ("hard_gates", "flags", "moat_keywords", "import_substitution_materials")}
    except (requests.RequestException, ValueError) as e:
        print(f"auto-screen: could not fetch live Logic Gates ({e}); using the built-in defaults")
        return defaults


def apply_gates(cfg: dict) -> None:
    """Push a loaded gates config into the module's globals: the numeric
    thresholds gates()/score() read, plus extra moat keywords and import-
    substitution materials layered onto the built-in evidence patterns."""
    global CAP_MIN, CAP_MAX_T1, CAP_MAX_T2, PROMOTER_MIN, PUBLIC_MAX, ROCE_MIN, ROE_MIN, INST_TIER1_MIN
    global HOLDERS_MAX_T1, HOLDERS_MAX_T2, PE_OVERHANG_MIN, CWIP_OVERHANG_MIN, CWIP_HEAVY_MIN
    global GUIDANCE_OVER_PCT, PAT_LOOKBACK, PE_PENALTY_MIN, IMPORT_MATERIALS
    hg, fl = cfg.get("hard_gates") or {}, cfg.get("flags") or {}
    CAP_MIN = hg.get("market_cap_min_cr", CAP_MIN)
    CAP_MAX_T1 = hg.get("market_cap_max_tier1_cr", CAP_MAX_T1)
    CAP_MAX_T2 = hg.get("market_cap_max_tier2_cr", CAP_MAX_T2)
    PROMOTER_MIN = hg.get("promoter_min_pct", PROMOTER_MIN)
    PUBLIC_MAX = hg.get("public_max_pct", PUBLIC_MAX)
    ROCE_MIN = hg.get("roce_min_pct", ROCE_MIN)
    ROE_MIN = hg.get("roe_min_pct", ROE_MIN)
    INST_TIER1_MIN = hg.get("institutional_tier1_min_pct", INST_TIER1_MIN)
    HOLDERS_MAX_T1 = hg.get("shareholders_max_tier1", HOLDERS_MAX_T1)
    HOLDERS_MAX_T2 = hg.get("shareholders_max_tier2", HOLDERS_MAX_T2)
    PE_OVERHANG_MIN = fl.get("pe_overhang_min", PE_OVERHANG_MIN)
    CWIP_OVERHANG_MIN = fl.get("cwip_overhang_min_pct", CWIP_OVERHANG_MIN)
    CWIP_HEAVY_MIN = fl.get("cwip_heavy_min_pct", CWIP_HEAVY_MIN)
    GUIDANCE_OVER_PCT = fl.get("guidance_over_pct", GUIDANCE_OVER_PCT)
    PAT_LOOKBACK = int(fl.get("pat_turnaround_lookback_periods", PAT_LOOKBACK))
    PE_PENALTY_MIN = fl.get("pe_penalty_min", PE_PENALTY_MIN)
    IMPORT_MATERIALS = [m.strip() for m in (cfg.get("import_substitution_materials") or []) if m and m.strip()]
    for cat, extra in (cfg.get("moat_keywords") or {}).items():
        phrases = [p.strip() for p in (extra or []) if p and p.strip()]
        if phrases and cat in EVIDENCE:
            rx = EVIDENCE[cat]
            EVIDENCE[cat] = re.compile(rx.pattern + "|" + "|".join(re.escape(p) for p in phrases), rx.flags)


# ------------------------------------------------------------------ universe
def _get(url: str, headers=EXCHANGE_HEADERS, timeout=60):
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r


def universe() -> list[dict]:
    """Listed companies in the size band (BSE) plus NSE-only listings, keyed by ISIN."""
    by_isin: dict[str, dict] = {}
    for row in _get("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Active").json():
        isin = (row.get("ISIN_NUMBER") or "").strip()
        try:
            cap = float(row.get("Mktcap") or 0)
        except ValueError:
            cap = 0
        if not isin or not (CAP_MIN <= cap <= CAP_MAX_T2):
            continue
        by_isin[isin] = {"isin": isin, "name": row.get("Issuer_Name") or row.get("Scrip_Name") or "",
                         "bse_code": str(row.get("SCRIP_CD") or ""), "nse_code": None, "mcap": cap}
    nse_urls = [("NSE", "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"),
                ("NSE-SME", "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv")]
    for board, url in nse_urls:
        lines = _get(url, headers={**EXCHANGE_HEADERS, "Referer": "https://www.nseindia.com/"}).text.splitlines()
        head = [h.strip().upper() for h in lines[0].split(",")]
        i_sym, i_name = 0, 1
        i_isin = next((i for i, h in enumerate(head) if "ISIN" in h), None)
        i_date = next((i for i, h in enumerate(head) if "LISTING" in h), None)
        for line in lines[1:]:
            c = [x.strip() for x in line.split(",")]
            if i_isin is None or len(c) <= i_isin:
                continue
            isin, sym = c[i_isin], c[i_sym]
            if isin in by_isin:
                by_isin[isin]["nse_code"] = sym
            elif board == "NSE-SME":
                # SME names often aren't on BSE: their size is checked on screener
                by_isin[isin] = {"isin": isin, "name": c[i_name], "bse_code": None, "nse_code": sym, "mcap": None,
                                 "listed": c[i_date] if i_date is not None and len(c) > i_date else None}
    return list(by_isin.values())


# ------------------------------------------------------------------ screener page
def _num(text):
    if text is None:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text)
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except ValueError:
        return None


def fetch_page(cand: dict) -> tuple[str, BeautifulSoup] | None:
    for code in (cand.get("nse_code"), cand.get("bse_code")):
        if not code:
            continue
        r = requests.get(f"https://www.screener.in/company/{code}/", headers=HEADERS, timeout=25)
        time.sleep(DELAY)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        return code, BeautifulSoup(r.text, "html.parser")
    return None


def parse(soup: BeautifulSoup) -> dict:
    out: dict = {}
    for li in soup.select("#top-ratios li"):
        n, v = li.select_one(".name"), li.select_one(".number")
        if not n or not v:
            continue
        label, value = n.get_text(strip=True).lower(), _num(v.get_text(strip=True))
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
    table = soup.select_one("#shareholding table")
    if table:
        for row in table.select("tbody tr"):
            cells = row.select("td")
            if len(cells) < 2:
                continue
            label, value = cells[0].get_text(" ", strip=True).lower(), _num(cells[-1].get_text(strip=True))
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
            elif "shareholders" in label:
                out["num_shareholders"] = int(value)
    bs = soup.select_one("#balance-sheet")
    if bs:
        for row in bs.select("table tbody tr"):
            cells = row.select("td, th")
            if len(cells) < 2:
                continue
            label = cells[0].get_text(" ", strip=True).lower()
            value = _num(cells[-1].get_text(strip=True))   # latest (rightmost) column
            if value is None:
                continue
            if label.startswith("fixed assets"):
                out["net_block_cr"] = value
            elif label == "cwip":
                out["cwip_cr"] = value
    if out.get("net_block_cr"):
        out["cwip_pct_net_block"] = round(100 * (out.get("cwip_cr") or 0) / out["net_block_cr"], 1)
    pl = soup.select_one("#profit-loss")
    if pl:
        head = pl.select_one("table thead tr")
        has_ttm = bool(head) and head.select("th")[-1].get_text(strip=True) == "TTM"
        row = next((r for r in pl.select("table tbody tr") if r.select("td")[0].get_text(" ", strip=True).lower().startswith("net profit")), None)
        if row:
            vals = [_num(c.get_text(strip=True)) for c in row.select("td")[1:]]   # skip the row label, itself a td here
            out["pat_series_cr"] = vals[:-1] if has_ttm else vals   # drop the trailing TTM column, not a fiscal period
    title = soup.select_one("h1")
    out["name"] = title.get_text(" ", strip=True) if title else None
    about = soup.select_one(".company-profile .about") or soup.select_one(".about")
    points = soup.select_one(".company-profile .commentary") or soup.select_one(".commentary")
    out["about"] = re.sub(r"\s*\[\d+\]", "", about.get_text(" ", strip=True)) if about else ""
    out["key_points"] = re.sub(r"\s*\[\d+\]", "", points.get_text(" ", strip=True)) if points else ""
    peers = soup.select_one("#peers")
    path = [a.get_text(strip=True) for a in peers.select('a[href^="/market/"]')] if peers else []
    out["sector_path"] = [p for i, p in enumerate(path) if p and p not in path[:i]]
    return out


# ------------------------------------------------------------------ rules
def gates(p: dict) -> tuple[list[str], int]:
    fails = []
    inst = (p.get("fii_pct") or 0) + (p.get("dii_pct") or 0)
    tier = 1 if inst >= INST_TIER1_MIN else 2
    cap = p.get("market_cap_cr")
    if cap is None or not (CAP_MIN <= cap <= (CAP_MAX_T1 if tier == 1 else CAP_MAX_T2)):
        fails.append(f"market cap {cap} cr outside Rs {CAP_MIN}-{CAP_MAX_T1 if tier == 1 else CAP_MAX_T2} cr")
    if (p.get("promoter_pct") or 0) < PROMOTER_MIN:
        fails.append(f"promoter {p.get('promoter_pct')}% under {PROMOTER_MIN}%")
    if p.get("public_pct") is not None and p["public_pct"] > PUBLIC_MAX:
        fails.append(f"public {p['public_pct']}% over {PUBLIC_MAX}%")
    if (p.get("roce_pct") or 0) < ROCE_MIN:
        fails.append(f"ROCE {p.get('roce_pct')}% under {ROCE_MIN}%")
    if (p.get("roe_pct") or 0) <= ROE_MIN:
        fails.append(f"ROE {p.get('roe_pct')}% not above {ROE_MIN}%")
    holders = p.get("num_shareholders")
    if holders is not None and holders >= (HOLDERS_MAX_T1 if tier == 1 else HOLDERS_MAX_T2):
        fails.append(f"{holders:,} shareholders over the limit")
    return fails, tier


def theme_for(p: dict) -> str | None:
    path = " / ".join(p.get("sector_path") or [])
    if not path or EXCLUDED_SECTORS.search(path):
        return None
    for pattern, theme in THEMES:
        if re.search(pattern, path, re.I):
            return theme
    return None


def sentences(text: str) -> list[str]:
    """Sentences, never running across a blank line (a PDF's paragraphs,
    headings and table cells are separated by those)."""
    out = []
    for block in re.split(r"\n\s*\n|\f", text or ""):
        block = re.sub(r"\s+", " ", block).strip()
        out += [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z(\"“])", block) if 25 < len(x.strip()) < 450]
    return out


def _short_name(name: str | None) -> str | None:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z&-]+", name or "")
             if w.lower() not in ("limited", "ltd", "india", "industries", "the", "and", "private", "pvt", "company")]
    # "Bharat" or an acronym like "IVP"; not a two-letter fragment
    return words[0] if words and (len(words[0]) >= 4 or (len(words[0]) == 3 and words[0].isupper())) else None


_PRODUCE_VERB = re.compile(r"\b(?:manufactur\w*|produc\w*|makes?|making|process\w*|develops?|supplies|supplied|"
                           r"exports?)\b", re.I)


def find_evidence(text: str, name: str | None, source: str, need_self: bool) -> dict[str, dict]:
    """Moat statements in `text`. Profile text is the company describing itself
    already; annual-report sentences must also name the company."""
    short = _short_name(name)
    self_rx = re.compile(SELF.pattern + (r"|\b" + re.escape(short) + r"\b" if short else ""), re.I)
    found: dict[str, dict] = {}
    niche = approval = None
    for sent in sentences(text):
        if need_self and not self_rx.search(sent):
            continue
        for key, rx in EVIDENCE.items():
            if key in found:
                continue
            m = rx.search(sent)
            # in a report the company has to be the subject: named just before the claim
            if m and (not need_self or self_rx.search(sent[max(0, m.start() - 100):m.end()])):
                found[key] = {"text": sent, "source": source}
        # a named heavily-imported material (Logic Gates -> import substitution materials),
        # alongside a make/produce verb and, for annual-report text, the company as subject
        if "import_substitution" not in found and IMPORT_MATERIALS:
            low = sent.lower()
            hit = next((m for m in IMPORT_MATERIALS if m.lower() in low), None)
            if hit and _PRODUCE_VERB.search(sent) and (not need_self or self_rx.search(sent)):
                found["import_substitution"] = {"text": sent, "source": source, "material": hit}
        if niche is None and NICHE.search(sent) and (not need_self or self_rx.search(sent)):
            niche = sent
        if approval is None and APPROVAL.search(sent) and (not need_self or self_rx.search(sent)):
            approval = sent
    # a niche claim and an approval may sit anywhere in the same document
    if niche and approval:
        found["niche"] = {"text": niche if niche == approval else f"{niche} … {approval}", "source": source}
    return found


def find_guidance(text: str, name: str | None, source: str) -> dict | None:
    """A self-referencing, forward-looking growth statement (Logic Gates ->
    guidance). An order book or a capacity expansion is not guidance -- this
    only matches an explicit guide/expect/target/aim verb. Low confidence by
    nature (a paraphrase in an annual report, not a verified transcript), so
    the matched sentence always travels with the flag for manual checking."""
    short = _short_name(name)
    self_rx = re.compile(SELF.pattern + (r"|\b" + re.escape(short) + r"\b" if short else ""), re.I)
    unquantified = None
    for sent in sentences(text):
        if not self_rx.search(sent):
            continue
        vm = GUIDANCE_VERB_RX.search(sent)
        if vm:
            # the growth word and the percentage must sit near the guidance verb,
            # not just somewhere else in a long, unrelated sentence
            window = sent[max(0, vm.start() - 60):vm.end() + 80]
            if GUIDANCE_GROWTH_WORD_RX.search(window):
                pm = GUIDANCE_PCT_RX.search(window)
                if pm:
                    return {"pct": float(pm.group(1)), "text": sent, "source": source}
        if unquantified is None and GUIDANCE_FLAG_RX.search(sent):
            unquantified = sent
    return {"pct": None, "text": unquantified, "source": source} if unquantified else None


def annual_report_link(soup: BeautifulSoup) -> tuple[str, str] | None:
    for a in soup.select("#documents .annual-reports a, .annual-reports a"):
        href = a.get("href") or ""
        label = a.get_text(" ", strip=True)
        if href.lower().split("?")[0].endswith(".pdf") and "annual report" in label.lower():
            year = re.search(r"20\d\d", label)
            return href, f"Annual Report {year.group(0)}" if year else "latest annual report"
    return None


def annual_report_text(url: str) -> str:
    if not PDFTOTEXT:
        return ""
    r = requests.get(url, headers=EXCHANGE_HEADERS | {"Accept": "application/pdf,*/*"}, timeout=90)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF") or len(r.content) > 60_000_000:
        return ""
    fd, pdf = tempfile.mkstemp(suffix=".pdf")
    try:
        os.write(fd, r.content)
        os.close(fd)
        out = subprocess.run([PDFTOTEXT, "-q", "-l", str(AR_PAGES), pdf, "-"], capture_output=True, timeout=120)
        return out.stdout.decode("utf-8", "ignore")
    finally:
        try:
            os.remove(pdf)
        except OSError:
            pass


def moat_evidence(p: dict, soup: BeautifulSoup | None = None) -> tuple[dict[str, dict], dict | None]:
    found = find_evidence(p.get("about", "") + " " + p.get("key_points", ""), p.get("name"),
                          "screener.in company profile", need_self=False)
    if any(k in found for k in STRONG) or soup is None:
        return found, None
    link = annual_report_link(soup)
    if not link:
        return found, None
    try:
        text = annual_report_text(link[0])
    except (requests.RequestException, subprocess.SubprocessError, OSError):
        return found, None
    for key, val in find_evidence(text, p.get("name"), link[1], need_self=True).items():
        found.setdefault(key, {**val, "url": link[0]})
    # guidance can only be read from the annual report -- the profile/key-points
    # blurb is too short to carry a genuine forward-looking statement
    guidance = find_guidance(text, p.get("name"), link[1])
    if guidance:
        guidance["url"] = link[0]
    return found, guidance


def pat_turnaround(series: list) -> bool:
    """Latest reported period profitable after a loss in at least one of the
    previous PAT_LOOKBACK periods (Logic Gates -> PAT turnaround lookback)."""
    vals = [v for v in (series or []) if v is not None]
    if len(vals) < 2:
        return False
    latest, prior = vals[-1], vals[max(0, len(vals) - 1 - PAT_LOOKBACK):-1]
    return latest > 0 and any(v < 0 for v in prior)


def capex_flags(pe, cwip_pct_net_block) -> tuple[bool, bool]:
    """(capex_overhang, capex_heavy) -- see frontend/index.html's own definitions."""
    if cwip_pct_net_block is None:
        return False, False
    heavy = cwip_pct_net_block >= CWIP_HEAVY_MIN
    overhang = pe is not None and pe > PE_OVERHANG_MIN and cwip_pct_net_block >= CWIP_OVERHANG_MIN
    return overhang, heavy


def score(p: dict, ev: dict, tier: int) -> tuple[float, dict, list[str]]:
    """The board's six pillars, from what an automated screen can see."""
    roce, roe, pe = p.get("roce_pct") or 0, p.get("roe_pct") or 0, p.get("pe")
    prom = p.get("promoter_pct") or 0
    inst = (p.get("fii_pct") or 0) + (p.get("dii_pct") or 0)
    cap, holders = p.get("market_cap_cr") or 0, p.get("num_shareholders")

    strong = sum(1 for k in STRONG if k in ev)
    s_moat = min(25, 8 * strong + (4 if "niche" in ev else 0) + (5 if roce >= 30 else 4 if roce >= 20 else 3 if roce >= 15 else 2))
    s_import = 0
    if "import_substitution" in ev:
        s_import = 14 + (6 if re.search(r"replac\w* imports? of|substitute for|import[- ]substitut\w* (?:of|for|in)", ev["import_substitution"]["text"], re.I) else 0)
    s_prom = 15 if 55 <= prom <= 70 else 12 if 50 <= prom < 55 or 70 < prom <= 75 else 9 if 45 <= prom < 50 or 75 < prom <= 80 else 7 if prom > 80 else 6
    has_both = (p.get("fii_pct") or 0) > 0 and (p.get("dii_pct") or 0) > 0
    s_inst = (15 if has_both else 12) if 2 <= inst <= 15 else 9 if 1 <= inst < 2 else 8 if 15 < inst <= 25 else 4 if inst > 25 else 3
    s_cov = (8 if cap <= 1000 else 6 if cap <= 2000 else 3) + (3 if holders is None else 7 if holders < 10000 else 5 if holders < 25000 else 3)
    s_fin = min(10, (5 if roe >= 20 else 3 if roe >= 12 else 1) + (3 if pe is not None and pe <= 40 else 1 if pe is not None and pe <= 60 else 0) + (2 if roce >= 20 else 0))

    penalties = []
    if tier == 2:
        penalties.append(("Tier 2: FII + DII under 1%", 3))
    if holders is not None and holders < 1500:
        penalties.append((f"only {holders:,} shareholders", 7))
    elif holders is not None and holders < 3000:
        penalties.append((f"only {holders:,} shareholders", 5))
    if pe is not None and pe > PE_PENALTY_MIN:
        penalties.append((f"P/E {pe} above {PE_PENALTY_MIN}", 3))
    raw = s_moat + s_import + s_prom + s_inst + s_cov + s_fin
    pen = sum(v for _, v in penalties)
    pillars = {"s_moat": s_moat, "s_import_sub": s_import, "s_promoter": s_prom, "s_institutional": s_inst,
               "s_undercovered": s_cov, "s_financials": s_fin}
    return round(raw - pen, 1), {**pillars, "score": raw, "risk_penalty": pen}, [f"{t} (-{v})" for t, v in penalties]


def record(code: str, cand: dict, p: dict, ev: dict, tier: int, theme: str, today: str, guidance: dict | None = None) -> dict:
    final, parts, penalty_detail = score(p, ev, tier)
    path = p.get("sector_path") or []
    evidence_lines = [f"{LABEL[k]} ({v['source']}): “{v['text']}”" for k, v in ev.items()]
    imp = ev.get("import_substitution")
    imp_note = (f"Company-stated ({imp['source']}{', names ' + imp['material'] if imp.get('material') else ''}): “{imp['text']}”"
               if imp else "None found in the company's profile or annual report.")
    cwip_pct = p.get("cwip_pct_net_block")
    capex_overhang, capex_heavy = capex_flags(p.get("pe"), cwip_pct)
    turned = pat_turnaround(p.get("pat_series_cr") or [])
    guidance_pct = guidance.get("pct") if guidance else None
    return {
        "code": code,
        "name": p.get("name") or cand.get("name"),
        "nse_code": cand.get("nse_code"),
        "bse_code": cand.get("bse_code"),
        "sector": path[-2] if len(path) >= 2 else (path[-1] if path else None),
        "industry": path[-1] if path else None,
        "theme": theme,
        "theme_detail": " > ".join(path),
        "screen": "auto",
        "source": "auto",
        "rubric": "auto",
        "claim_grade": "company-stated",
        "added_on": today,
        "tier": tier,
        "has_lens_data": False,
        "market_cap_cr": p.get("market_cap_cr"), "price": p.get("price"), "pe": p.get("pe"),
        "roce_pct": p.get("roce_pct"), "roe_pct": p.get("roe_pct"),
        "promoter_pct": p.get("promoter_pct"), "fii_pct": p.get("fii_pct"), "dii_pct": p.get("dii_pct"),
        "public_pct": p.get("public_pct"), "num_shareholders": p.get("num_shareholders"),
        "business": " ".join(x for x in (p.get("about"), p.get("key_points")) if x)[:1200],
        "moat_note": "Auto-screen found the company describing itself with: " + " · ".join(evidence_lines),
        "import_substitution": imp_note,
        "cwip_pct_net_block": cwip_pct,
        "capex_overhang": capex_overhang,
        "capex_heavy": capex_heavy,
        "pat_turnaround": turned,
        "guidance_pct": guidance_pct,
        "guidance_over15": guidance_pct is not None and guidance_pct > GUIDANCE_OVER_PCT,
        "guidance_flag": guidance is not None,
        "guidance_note": (f"Auto-detected, unverified ({guidance['source']}): “{guidance['text']}”" if guidance and guidance.get("text") else None),
        "evidence_sources": sorted({v.get("url") or "https://www.screener.in/company/" + code + "/" for v in ev.values()}),
        "final_score": final,
        "adj_score": final,
        "penalty_detail": penalty_detail,
        "gate_failures": [],
        "warnings": ["Auto-added by the daily screen: numbers are from screener.in and the moat evidence is the "
                     "company's own description (profile or annual report) matched by rules. Nobody has researched this company yet."],
        "score_rationale": "Automated six-pillar score from screener.in numbers and matched moat statements (see backend/scripts/auto_screen.py).",
        **parts,
    }


# ------------------------------------------------------------------ run
def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-add", type=int, default=10)
    ap.add_argument("--budget", type=int, default=400, help="most company pages to fetch in one run")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    gates_cfg = load_gates()
    apply_gates(gates_cfg)
    print(f"auto-screen: gates -- cap Rs {CAP_MIN}-{CAP_MAX_T1}/{CAP_MAX_T2} cr, promoter >= {PROMOTER_MIN}%, "
          f"public <= {PUBLIC_MAX}%, ROCE >= {ROCE_MIN}%, ROE > {ROE_MIN}%, shareholders < {HOLDERS_MAX_T1:,}/{HOLDERS_MAX_T2:,}"
          + (f"; {sum(len(v) for v in (gates_cfg.get('moat_keywords') or {}).values())} extra moat keyword(s), "
             f"{len(IMPORT_MATERIALS)} import-substitution material(s)" if gates_cfg.get("moat_keywords") or IMPORT_MATERIALS else ""))

    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()
    companies = json.loads(COMPANIES.read_text(encoding="utf-8"))
    on_board = set()
    for c in companies:
        for k in ("code", "nse_code", "bse_code"):
            if c.get(k):
                on_board.add(str(c[k]).upper())
    ruled_out = {str(q.get("sym")).upper() for q in load_json(QUEUE, []) if q.get("verdict") == "ruled_out"}
    state = load_json(STATE, {"checked": {}, "runs": []})
    recheck_before = (date.fromisoformat(today) - timedelta(days=RECHECK_DAYS)).isoformat()

    pool = []
    for cand in universe():
        keys = {str(x).upper() for x in (cand.get("nse_code"), cand.get("bse_code")) if x}
        if keys & on_board or keys & ruled_out or SKIP_NAME.search(cand["name"] or ""):
            continue
        seen = state["checked"].get(cand["isin"])
        if seen and (seen["result"] == "added" or seen["on"] >= recheck_before):
            continue
        pool.append(cand)
    # never-checked first, recent SME listings before the rest, then the longest-unchecked
    pool.sort(key=lambda c: (c["isin"] in state["checked"], state["checked"].get(c["isin"], {}).get("on", ""),
                             -(int((c.get("listed") or "0")[-4:]) if (c.get("listed") or "")[-4:].isdigit() else 0)))
    print(f"auto-screen: {len(pool)} companies eligible to check today; budget {args.budget} pages, adding at most {args.max_add}")

    passes, fetched, outcomes = [], 0, {}
    for cand in pool:
        if fetched >= args.budget or len(passes) >= args.max_add * 3:
            break
        try:
            got = fetch_page(cand)
        except requests.RequestException as e:
            outcomes[cand["isin"]] = ("error", str(e)[:80])
            continue
        fetched += 1
        if not got:
            outcomes[cand["isin"]] = ("not on screener", "")
            continue
        code, soup = got
        p = parse(soup)
        fails, tier = gates(p)
        if fails:
            outcomes[cand["isin"]] = ("gates", "; ".join(fails))
            continue
        theme = theme_for(p)
        if not theme:
            outcomes[cand["isin"]] = ("sector", " / ".join(p.get("sector_path") or []) or "no sector")
            continue
        ev, guidance = moat_evidence(p, soup)
        if not ev:
            outcomes[cand["isin"]] = ("no moat evidence", "")
            continue
        rec = record(code, cand, p, ev, tier, theme, today, guidance)
        passes.append((rec, cand))
        print(f"  PASS {code:<12} {rec['name'][:40]:<40} score {rec['final_score']:>5}  {theme}  [{', '.join(ev)}]")

    passes.sort(key=lambda x: -x[0]["final_score"])
    added = passes[:args.max_add]
    for rec, cand in added:
        outcomes[cand["isin"]] = ("added", rec["code"])
    for rec, cand in passes[args.max_add:]:
        outcomes[cand["isin"]] = ("passed, not added today", f"score {rec['final_score']}")

    counts: dict[str, int] = {}
    for result, _ in outcomes.values():
        counts[result] = counts.get(result, 0) + 1
    print(f"auto-screen: fetched {fetched} pages; " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"auto-screen: adding {len(added)}: " + (", ".join(f"{r['name']} ({r['final_score']})" for r, _ in added) or "none passed today"))

    if args.dry_run:
        return 0
    # a run where nearly every fetch failed is screener.in blocking, not a quiet day
    errors = counts.get("error", 0)
    if fetched == 0 and errors:
        print("auto-screen: every fetch failed; nothing written", file=sys.stderr)
        return 2
    for isin, (result, detail) in outcomes.items():
        if result == "passed, not added today":
            continue   # not recorded, so it is first in line tomorrow
        state["checked"][isin] = {"on": today, "result": result, **({"detail": detail} if detail else {})}
    state["runs"] = (state.get("runs", []) + [{"on": today, "fetched": fetched, **counts, "added_codes": [r["code"] for r, _ in added]}])[-60:]
    STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if added:
        companies.extend(r for r, _ in added)
        COMPANIES.write_text(json.dumps(companies, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
