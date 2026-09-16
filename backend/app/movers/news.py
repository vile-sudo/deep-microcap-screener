"""Per-company news, allowlisted by source and filtered for causal content.

Three things make raw search results unusable as explanations.

Source quality: a query returns the mainstream desks alongside auto-generated
stock pages, so only named outlets are kept.

Relevance: the search matches article bodies, so a result can be about something
else entirely. A headline that never names the company cannot explain its move.

Circularity: much financial coverage exists *because* the price moved. The line
to draw is not "does this mention the price", because the usual way a reason
arrives is bundled with it - "shares jump 18% after GrafTech price hike" carries
both. It is listicles that explain nothing, naming a dozen companies at once, so
those are vetoed and everything else is judged on whether it reports an event.
"""

from __future__ import annotations

import email.utils
import logging
import re
import json
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

import requests

LOGGER = logging.getLogger(__name__)

SEARCH_URL = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
# How far either side of a session to look. Coverage often lands a day or two
# after the move, when the desks publish their follow-up.
DAYS_BEFORE = 3
DAYS_AFTER = 2
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".movers_cache" / "news"

# Desks with editorial standards. Everything else is dropped.
ALLOWED_SOURCES = {
    "moneycontrol.com": "Moneycontrol",
    "livemint.com": "Mint",
    "mint": "Mint",
    "the economic times": "Economic Times",
    "economictimes.indiatimes.com": "Economic Times",
    "business standard": "Business Standard",
    "business-standard.com": "Business Standard",
    "the hindu businessline": "Hindu BusinessLine",
    "thehindubusinessline.com": "Hindu BusinessLine",
    "the hindu": "The Hindu",
    "cnbc tv18": "CNBC TV18",
    "cnbctv18.com": "CNBC TV18",
    "reuters": "Reuters",
    "bloomberg": "Bloomberg",
    "financial express": "Financial Express",
    "business today": "Business Today",
    "ndtv profit": "NDTV Profit",
    "investing.com": "Investing.com",
    "upstox": "Upstox",
    # Wire services, which carry the mid-caps the national desks skip.
    "press trust of india": "PTI",
    "pti": "PTI",
    "ians": "IANS",
    # Other desks seen carrying company events for this universe.
    "businessline": "Hindu BusinessLine",
    "fortune india": "Fortune India",
    "the times of india": "Times of India",
    "the indian express": "Indian Express",
    "the new indian express": "New Indian Express",
    "india today": "India Today",
    "bw businessworld": "Businessworld",
    "moneylife": "Moneylife",
    "business upturn": "Business Upturn",
    "the hindu business line": "Hindu BusinessLine",
}

# Filing-derived services: accurate and specific about corporate events, but
# machine-written, so they are consulted only where nothing better was found and
# whatever they yield is reported at lower confidence.
SECONDARY_SOURCES = {
    "scanx.trade": "Scanx",
    "sahi": "Sahi",
    "psu connect": "PSU Connect",
    "goodreturns": "Goodreturns",
    "analytics insight": "Analytics Insight",
}


# A headline naming many companies cannot attribute a reason to any one of them.
LISTICLE = re.compile(
    r"stocks? to watch|stocks? in news|stocks? in focus|among \d+ [a-z ]*stocks?|"
    r"\d+ stocks? (?:to|that|turning)|top gainers|top losers|market wrap|closing bell|"
    r"opening bell|trade spotlight|hot stocks|stock recommendations?|"
    r"market trading guide|buzzing stocks",
    re.I,
)
# Reported, but housekeeping: scheduled meetings, transcripts, statutory notices,
# awards, and changes to staff too junior to matter. These reach the wire because
# a filing exists, not because anything happened to the business.
ROUTINE = re.compile(
    r"earnings call transcript|investor presentation|analyst meet\b|"
    r"schedules? (?:institutional )?investor|investor meetings?\b|"
    r"\bagm\b|annual general meeting|postal ballot|record dates? to mark|"
    r"trading window|newspaper publication|confirms no |no (?:share )?encumbrance|"
    r"wins? .{0,40}award|awards? \d{4}|felicitat|"
    r"associate vice president|company secretary|compliance officer|"
    r"\bdeputy\b|\bassistant\b",
    re.I,
)
# Headlines reporting something that happened, which can explain a move.
CAUSAL = (
    (re.compile(r"\border\b|contract|wins?\b|bags?\b|awarded|l1\b", re.I), "order win"),
    (re.compile(r"\bq[1-4]\b|results|profit|revenue|earnings|margin", re.I), "results"),
    # Broker calls are tested before M&A, because "Strong Sell" is a rating, not a sale.
    (re.compile(r"upgrade|downgrade|initiat|target price|brokerage|\brating\b|strong (?:buy|sell)", re.I), "broker action"),
    (re.compile(r"acquisi|merger|demerger|\bstake\b|takeover|divest", re.I), "M&A or stake"),
    (re.compile(r"encumbrance|pledge", re.I), "pledge or encumbrance"),
    (re.compile(r"qip|fund ?rais|placement|rights issue|ipo|block deal|offer for sale", re.I), "fundraise or deal"),
    (re.compile(r"dividend|bonus|split|buyback", re.I), "capital action"),
    (re.compile(r"sebi|penalty|probe|raid|court|tribunal|\bban\b|insolvency|show cause|legal notice|tax notice", re.I), "regulatory or legal"),
    (re.compile(r"resign|appoint|steps down|new (?:ceo|md|cfo)", re.I), "management change"),
    (re.compile(r"plant|capacity|expansion|commission|launch|approval|licence|license", re.I), "operations"),
    # A global peer raising prices lifts the domestic makers; Indian desks write
    # that link for us, so the pricing vocabulary is what has to be recognised.
    # "price rise" is deliberately absent: it collides with the "Share Price ...
    # price rise" boilerplate, which describes the stock rather than the product.
    (re.compile(r"price (?:hike|increase|cut)s?\b|hikes? prices?|raises? prices?|"
                r"tariff|anti-?dumping|\bduty\b|\blevy\b|"
                r"supply (?:crunch|shortage)|capacity closure", re.I), "sector pricing"),
    (re.compile(r"here'?s why|what'?s driving|reason behind|why .{0,30}(?:surge|rally|jump|slump|fall|crash)", re.I), "explainer"),
)


# Where several desks carry the same story, the verdict should cite the strongest.
SOURCE_RANK = {
    "Reuters": 0, "Bloomberg": 0,
    "Moneycontrol": 1, "Mint": 1, "Economic Times": 1, "Business Standard": 1,
    "CNBC TV18": 1, "Hindu BusinessLine": 1, "The Hindu": 1,
    "Financial Express": 2, "Business Today": 2, "NDTV Profit": 2,
    "Investing.com": 3, "Upstox": 3,
    "PTI": 1, "IANS": 2,
    "Fortune India": 2, "Times of India": 2, "Indian Express": 2,
    "New Indian Express": 3, "India Today": 3, "Businessworld": 3,
    "Moneylife": 3, "Business Upturn": 4,
}


@dataclass(frozen=True, slots=True)
class Story:
    symbol: str
    headline: str
    source: str
    url: str
    kind: str
    # "primary" for an editorial desk, "secondary" for a filing-derived service.
    tier: str = "primary"
    published: str = ""


# First words too common to identify a company on their own.
AMBIGUOUS_HEAD = {
    "force", "power", "energy", "national", "global", "first", "premier", "united",
    "general", "standard", "century", "capital", "finance", "industries", "orient",
    "eastern", "western", "northern", "southern", "central", "prime", "supreme",
    "sun", "star", "royal", "swan", "apex", "elite",
}
NAME_NOISE = re.compile(r"\b(ltd\.?|limited|corporation|corp\.?|co\.?|company|the|and|of)\b", re.I)


def name_tokens(company: str) -> list[str]:
    cleaned = NAME_NOISE.sub(" ", company)
    return [w for w in re.findall(r"[A-Za-z][A-Za-z&']+", cleaned) if len(w) > 1]


def mentions(company: str, headline: str) -> bool:
    """Does this headline actually name the company?

    The search engine matches article bodies, so a result can be about something
    else entirely: a query for Swan Corp returned a story about a Pizza Hut raid,
    and one for Welspun Corp returned a mutual-fund notice containing "Bonus".
    A headline that never names the company cannot explain its move.
    """
    tokens = name_tokens(company)
    if not tokens:
        return False
    low = headline.lower()
    head = tokens[0]
    if not re.search(r"\b" + re.escape(head.lower()), low):
        return False
    # A short or common first word needs the second word to confirm the match.
    if len(head) < 4 or head.lower() in AMBIGUOUS_HEAD:
        if len(tokens) < 2:
            return False
        # Prefix match, so "IRB Infra" still matches "IRB Infrastructure".
        if not re.search(r"\b" + re.escape(tokens[1][:5].lower()), low):
            return False
    return True


def classify(headline: str) -> str | None:
    """Return why this headline could explain a move, or None if it cannot.

    Causal content wins over price-action phrasing. Financial headlines routinely
    carry both - "Graphite India shares jump 18%, hit 52-wk high after GrafTech
    price hike" is how the move *and* its cause usually arrive in one line - so
    vetoing on the price half throws away the explanation with it.

    A listicle is different, and still vetoed outright: a headline naming a dozen
    companies cannot attribute a reason to any one of them.
    """
    if LISTICLE.search(headline) or ROUTINE.search(headline):
        return None
    for pattern, kind in CAUSAL:
        if pattern.search(headline):
            return kind
    return None


def _squash(text: str) -> str:
    """Reduce a source name to letters and digits, so spelling variants converge."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# Longest keys first, so "thehindubusinessline" is not claimed by "thehindu".
_SOURCE_KEYS = sorted(
    ((_squash(needle), label) for needle, label in ALLOWED_SOURCES.items()),
    key=lambda pair: -len(pair[0]),
)


def _published(raw: str | None) -> date | None:
    """The item's publication date, or None when the feed omits a usable one."""
    if not raw:
        return None
    try:
        return email.utils.parsedate_to_datetime(raw).date()
    except (TypeError, ValueError):
        return None


def secondary_source(raw: str) -> str | None:
    """Map a source to a second-tier service, used only as a fallback."""
    text = _squash(raw)
    for needle, label in SECONDARY_SOURCES.items():
        if _squash(needle) in text:
            return label
    return None


def normalise_source(raw: str) -> str | None:
    """Map a feed's source name to an allowlisted desk.

    The same desk arrives under several spellings - "NDTV Profit" and
    "ndtvprofit.com", "The Hindu BusinessLine" and bare "BusinessLine" - so the
    comparison ignores spacing and punctuation. Matching on the raw string missed
    22 NDTV Profit stories and 8 from BusinessLine.
    """
    text = _squash(raw)
    if not text:
        return None
    for needle, label in _SOURCE_KEYS:
        if needle and needle in text:
            return label
    return None


def search(
    company: str,
    symbol: str,
    *,
    session: requests.Session,
    window_start: date,
    window_end: date,
    secondary: bool = False,
) -> list[Story]:
    """Query one company and keep allowlisted, causal headlines inside a window.

    The window is anchored to the session being explained rather than to today,
    so a backfilled date searches the right days. Google's own `before:` operator
    is only a hint - a query bounded at 8 September still returned items from the
    9th - so each item's pubDate is checked against the window here.

    With `secondary`, the filing-derived services are accepted too. That pass is
    run only for stocks the strict pass could not explain, so the wider net never
    displaces an editorial desk where one exists.
    """
    # The operators bias the result set; the pubDate check below is what binds it.
    query = urllib.parse.quote(
        f'"{company}" after:{window_start - timedelta(days=1)} '
        f'before:{window_end + timedelta(days=1)}'
    )
    try:
        response = session.get(SEARCH_URL.format(query=query), timeout=25)
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
    except Exception:
        LOGGER.debug("News search failed for %s", company, exc_info=True)
        return []

    stories: list[Story] = []
    seen: set[str] = set()
    for item in root.iter("item"):
        headline = (item.findtext("title") or "").strip()
        source_el = item.find("source")
        published = _published(item.findtext("pubDate"))
        if published is None or not (window_start <= published <= window_end):
            continue
        raw_source = source_el.text if source_el is not None else ""
        source, tier = normalise_source(raw_source), "primary"
        if source is None and secondary:
            source, tier = secondary_source(raw_source), "secondary"
        if not headline or source is None:
            continue
        # Google appends " - Source" to every title.
        headline = re.sub(r"\s+-\s+[^-]+$", "", headline).strip()
        if not mentions(company, headline):
            continue
        kind = classify(headline)
        if kind is None:
            continue
        key = headline.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        stories.append(
            Story(
                symbol=symbol,
                headline=headline,
                source=source,
                url=(item.findtext("link") or "").strip(),
                kind=kind,
                tier=tier,
                published=published.isoformat(),
            )
        )
    # Strongest desk first, so the verdict quotes the best source carrying the story.
    stories.sort(key=lambda story: SOURCE_RANK.get(story.source, 5))
    return stories


def stories_for(
    symbols: dict[str, str],
    trade_date: date,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    pause: float = 1.0,
    today: date | None = None,
) -> dict[str, list[Story]]:
    """Fetch news for each flagged symbol across the window around the session.

    The window runs from three days before the session to two days after, because
    the piece that explains a move is often published the next morning. A scan run
    the day after the session therefore cannot see all of it, so the cache records
    when it was built and is refreshed on later runs until the window has closed.
    """
    now = today or date.today()
    window_start = trade_date - timedelta(days=DAYS_BEFORE)
    window_end = trade_date + timedelta(days=DAYS_AFTER)
    path = cache_dir / f"{trade_date.isoformat()}.json"

    cached = _read_cache(path)
    if cached is not None:
        built_on = cached.get("fetched_on")
        complete = built_on is not None and built_on >= window_end.isoformat()
        if complete or (built_on is not None and built_on >= now.isoformat()):
            return _to_stories(cached.get("stories", {}))
        LOGGER.info(
            "News for %s was gathered on %s, before the window closed on %s; refreshing",
            trade_date.isoformat(),
            built_on,
            window_end.isoformat(),
        )

    session = requests.Session()
    session.headers.update(HEADERS)
    collected: dict[str, list[dict]] = {}
    unexplained: list[tuple[str, str]] = []

    def keep(found: list[Story]) -> list[dict]:
        return [
            {
                "headline": story.headline,
                "source": story.source,
                "url": story.url,
                "kind": story.kind,
                "tier": story.tier,
                "published": story.published,
            }
            for story in found[:4]
        ]

    for index, (symbol, company) in enumerate(sorted(symbols.items()), start=1):
        found = search(
            company, symbol, session=session,
            window_start=window_start, window_end=window_end,
        )
        if found:
            collected[symbol] = keep(found)
        else:
            unexplained.append((symbol, company))
        if index % 10 == 0:
            LOGGER.info("News: %d/%d companies searched", index, len(symbols))
        # A courtesy delay; this runs for a few dozen symbols once a day.
        time.sleep(pause)

    # Second pass, wider net, only where the editorial desks had nothing.
    if unexplained:
        LOGGER.info("Retrying %d company(s) with the second-tier sources", len(unexplained))
        for symbol, company in unexplained:
            found = search(
                company, symbol, session=session,
                window_start=window_start, window_end=window_end, secondary=True,
            )
            if found:
                collected[symbol] = keep(found)
            time.sleep(pause)

    payload = {
        "trade_date": trade_date.isoformat(),
        "window": [window_start.isoformat(), window_end.isoformat()],
        "fetched_on": now.isoformat(),
        "stories": collected,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1) + chr(10), encoding="utf-8")
    LOGGER.info(
        "News for %s: %d company(s) covered, window %s to %s",
        trade_date.isoformat(),
        len(collected),
        window_start.isoformat(),
        window_end.isoformat(),
    )
    return _to_stories(collected)


def _read_cache(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    # Caches written before the window was recorded carry the stories at the top
    # level, so they are treated as stale and rebuilt.
    return payload if isinstance(payload, dict) and "stories" in payload else None


def _to_stories(raw: dict) -> dict[str, list[Story]]:
    return {
        symbol: [
            Story(
                symbol,
                row["headline"],
                row["source"],
                row["url"],
                row["kind"],
                row.get("tier", "primary"),
                row.get("published", ""),
            )
            for row in rows
        ]
        for symbol, rows in raw.items()
    }
