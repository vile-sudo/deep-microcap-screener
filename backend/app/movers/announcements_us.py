"""US corporate announcements (SEC Form 8-K) -- the US board's counterpart
to NSE's corporate-announcements feed (app/movers/announcements.py).

Genuinely closer to a straight port than Form 4 was for Bulk & Block Deals:
Form 8-K IS the US "disclose a material corporate event" filing, and SEC
gives the reported item numbers as structured data (submissions.json's own
`items` field) rather than free text to classify. There's also no NSE-style
~30% "routine filing" noise to strip out -- Item 8-K itself already only
covers events SEC deems material by rule; the one routine case is a filing
whose only item is 9.01 (Financial Statements and Exhibits), which never
appears alone as a substantive announcement.

Verified against real AAPL 8-K filings before writing this (items=
"2.02,9.01" for an earnings release, items="5.02" for an officer
departure, items="5.07,9.01" for an annual-meeting vote) -- not guessed
from SEC's Form 8-K instructions.
"""
from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import PurePosixPath

import requests

SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
HEADERS = {"User-Agent": "Deep Sweep research contact@pkresearch.in"}
DELAY = 0.15

# SEC's own numbered items -> the same category vocabulary India's
# announcements.classify() uses, so the US and India tabs group filings
# the same way. (weight, category) -- weight only orders "most explanatory
# first" within a filing's items; every 8-K item is already inherently
# material, so nothing is scored 0 the way NSE's routine filings are.
ITEM_CATEGORY: dict[str, tuple[str, int]] = {
    "1.01": ("agreement", 4), "1.02": ("agreement", 3), "1.03": ("regulatory or legal", 5),
    "1.04": ("regulatory or legal", 3), "1.05": ("regulatory or legal", 4),
    "2.01": ("M&A", 5), "2.02": ("results", 5), "2.03": ("fundraise", 4),
    "2.04": ("regulatory or legal", 3), "2.05": ("other disclosure", 2), "2.06": ("other disclosure", 2),
    "3.01": ("regulatory or legal", 5), "3.02": ("fundraise", 4), "3.03": ("capital action", 4),
    "4.01": ("other disclosure", 2), "4.02": ("regulatory or legal", 4),
    "5.01": ("M&A", 5), "5.02": ("management change", 3), "5.03": ("capital action", 3),
    "5.04": ("other disclosure", 1), "5.05": ("other disclosure", 1), "5.06": ("other disclosure", 2),
    "5.07": ("investor meeting", 2), "5.08": ("other disclosure", 1),
    "6.01": ("other disclosure", 1), "6.02": ("other disclosure", 1), "6.03": ("other disclosure", 1),
    "6.04": ("other disclosure", 1), "6.05": ("other disclosure", 1),
    "7.01": ("other disclosure", 2), "8.01": ("other disclosure", 2),
}
# 9.01 (Financial Statements and Exhibits) rides alongside almost every
# other item and carries no signal alone -- the closest thing here to
# NSE's "routine filing" bucket.
ROUTINE_ITEMS_ONLY = {"9.01", ""}

# Item 8.01 is SEC's own catch-all ("Other Events") -- used for everything
# from an order win to a plant expansion. Same free-text patterns India's
# announcements.classify() runs, applied to the filing's own text so an
# 8.01 filing isn't stuck labelled "other disclosure" when the text says
# more, mirroring India's structure-plus-text approach rather than
# dropping the text signal just because SEC gives structure for most
# other items.
_TEXT_OVERRIDE = (
    (re.compile(r"receiv(?:e|ed|es|ing)\s+(?:a|an|the)\s+.{0,30}\border\b|\bawarded\b.{0,30}\bcontract\b", re.I), "order win", 4),
    (re.compile(r"\bacqui(?:re|red|sition|ring)\b|\bmerger\b|\bdivest", re.I), "M&A", 4),
    (re.compile(r"\bpartnership\b|\bjoint venture\b|\bcollaborat|\blicens(?:e|ing) agreement\b", re.I), "agreement", 3),
    (re.compile(r"\bcredit rating\b|\bupgrad(?:e|ed|es)\b.{0,20}\brating\b|\bdowngrad(?:e|ed|es)\b.{0,20}\brating\b", re.I), "rating change", 3),
    (re.compile(r"\blawsuit\b|\blitigation\b|\bsettlement\b|\binvestigation\b|\bsubpoena\b", re.I), "regulatory or legal", 4),
    (re.compile(r"\bnew (?:plant|facility|factory)\b|\bexpansion\b|\bcapacity\b", re.I), "capacity or plant", 3),
)

SUMMARY_MAX = 320


@dataclass(frozen=True, slots=True)
class Announcement8K:
    symbol: str
    company: str
    items: list[str] = field(default_factory=list)
    category: str = "other disclosure"
    weight: int = 1
    summary: str = ""
    filed_at: date | None = None
    accession: str = ""
    url: str = ""


def _html_to_text(html: str) -> str:
    """Same simple block-tag-aware strip scripts/us_auto_screen.py already
    uses for 10-K text -- proven on real EDGAR filing HTML, so reused in
    shape rather than reinvented (that one is script-local, not importable
    from here). Modern filings are Inline XBRL: the whole machine-readable
    tag header sits in one hidden <ix:header>...</ix:header> block up
    front (confirmed on a real AAPL 8-K -- without stripping it, the
    "summary" was literally XBRL boilerplate: "true true true ... NASDAQ
    NASDAQ ..."), so that goes before the general tag-strip, not after."""
    html = re.sub(r"(?is)<ix:header\b.*?</ix:header>", " ", html)
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


_ITEM_HEADING = re.compile(r"^Item\s+(\d+\.\d+)\b")


def _summary(text: str, target_item: str | None) -> str:
    """The narrative under the item that drove the classification -- Form
    8-K's own structure is always 'Item N.NN <Title>.' followed by one or
    more explanatory paragraphs, then the next Item heading (or the
    exhibit list / signature block). Verified against a real filing: a
    naive "first real-looking paragraph" heuristic kept landing on cover-
    page boilerplate ("Pursuant to Section 13 OR 15(d)...", "Date of
    Report...") instead of the actual story, since EDGAR's cover page has
    several paragraph-shaped lines before Item 1 ever starts."""
    blocks = [re.sub(r"\s+", " ", b).strip() for b in re.split(r"\n\s*\n", text)]
    blocks = [b for b in blocks if b]
    starts = {m.group(1): i for i, b in enumerate(blocks) if (m := _ITEM_HEADING.match(b))}
    if target_item is None or target_item not in starts:
        return ""
    start = starts[target_item] + 1
    end = next((i for i in range(start, len(blocks)) if _ITEM_HEADING.match(blocks[i])), len(blocks))
    # SEC text carries HTML entities (&#8203;, &amp;, ...) and zero-width spaces
    body = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", html.unescape(" ".join(blocks[start:end]))).strip()
    return body[:SUMMARY_MAX] + ("…" if len(body) > SUMMARY_MAX else "")


def classify(items: list[str], text: str) -> tuple[str, int, str | None]:
    """Category, weight and the item number that decided them -- the
    structured item codes first, falling back to the highest-weight one
    when there are several; an Item 8.01-only filing gets a free-text
    second look (returning None for target_item then, since there's no
    single "Item N.NN" to point the summary at -- the whole 8.01 body is
    the story)."""
    usable = [i for i in items if i not in ROUTINE_ITEMS_ONLY]
    if not usable:
        return "other disclosure", 0, None
    scored = [(i, *ITEM_CATEGORY.get(i, ("other disclosure", 1))) for i in usable]
    best_item, category, weight = max(scored, key=lambda x: x[2])
    if set(usable) == {"8.01"}:
        for pattern, kind, w in _TEXT_OVERRIDE:
            if pattern.search(text):
                return kind, w, "8.01"
        return category, weight, "8.01"
    return category, weight, best_item


def filings_for(symbol: str, company: str, cik: str, session: requests.Session, limit: int = 15) -> list[Announcement8K]:
    """This company's most recent 8-K filings (up to `limit`), classified
    and summarized."""
    r = session.get(f"{SEC_DATA}/submissions/CIK{cik}.json", headers=HEADERS, timeout=20)
    time.sleep(DELAY)
    if r.status_code != 200:
        return []
    recent = (r.json().get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    out: list[Announcement8K] = []
    n = 0
    for i, f in enumerate(forms):
        if f not in ("8-K", "8-K/A"):
            continue
        n += 1
        if n > limit:
            break
        accession = recent["accessionNumber"][i]
        items = [x.strip() for x in (recent.get("items", [""] * len(forms))[i] or "").split(",") if x.strip()]
        filed = datetime.strptime(recent["filingDate"][i], "%Y-%m-%d").date()
        primary = PurePosixPath(recent["primaryDocument"][i]).name
        acc_nodash = accession.replace("-", "")
        doc_url = f"{SEC_WWW}/Archives/edgar/data/{int(cik)}/{acc_nodash}/{primary}"
        index_url = f"{SEC_WWW}/Archives/edgar/data/{int(cik)}/{acc_nodash}/{accession}-index.htm"
        text = ""
        try:
            rr = session.get(doc_url, headers=HEADERS, timeout=20)
            time.sleep(DELAY)
            if rr.status_code == 200:
                text = _html_to_text(rr.text)
        except requests.RequestException:
            pass
        category, weight, target_item = classify(items, text)
        if weight <= 0:
            continue
        out.append(Announcement8K(symbol=symbol, company=company, items=items, category=category, weight=weight,
                                  summary=_summary(text, target_item), filed_at=filed, accession=accession, url=index_url))
    return out
