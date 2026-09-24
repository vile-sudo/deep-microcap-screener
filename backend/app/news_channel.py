"""
News Channel: China, India, USA and Japan news for the sectors each one dominates
globally -- a leading indicator for Indian listed companies downstream (a
Chinese API price hike is a margin story for the Indian bulk-drug makers who
buy from it; a US tariff on steel reroutes demand; and so on).

Three sources (Google News, newsdata.io, BusinessLine), fanned into the same filter/tag pipeline (sector tagging,
source quality, "is this actually a price move" -- the same client-side
work app/movers/news.py already does for company news), not separate
feeds:

- Google News RSS (fetch_google_sector) -- no API key, no formal daily
  quota, ~100 results per query where newsdata.io's free plan hard-caps at
  10. Reused each sector's own keyword list (COUNTRIES[c]["sectors"]) as
  ONE QUERY PER SECTOR instead of one OR'd blob per country -- this is only
  possible because nothing meters it in credits the way newsdata.io does,
  and it is both more precise (a real query per sector) and higher-volume
  (100 vs 10 results) than the newsdata.io path ever could be. Unofficial
  (Google could change or throttle it without notice; the feed's own
  <copyright> tag restricts it to "personal, non-commercial use", which
  this dashboard is) -- paced at half a second between requests and left
  to fail silently into `errors` per query, never allowed to break the run.
- BusinessLine RSS (fetch_businessline) -- thehindubusinessline.com's own
  section feeds, no key, no quota. An Indian desk covering all four
  countries, so each headline is assigned to a country by who it is about
  (_COUNTRY_HINTS). Exists because Google's sector queries only find what
  their keywords name: "India starts anti-dumping probe into Chinese
  Glycine imports" matched nothing until trade-remedy language (TRADE_REMEDY)
  and this direct feed were added.
- newsdata.io (fetch_country) -- kept as a second, independent source, not
  replaced: one request per country per run (COUNTRIES below), same 100-char
  query cap as before. Optional now -- write() runs Google News regardless
  of whether NEWSDATA_API_KEY is set, and only adds newsdata.io on top when
  it is. Its own request budget is still tracked and capped exactly as
  before (DAILY_CAP, a hard floor under the real 200/day, so a scheduling
  mistake or a burst of "Refresh now" clicks can never exhaust the key for
  the rest of the day) -- see _quota_today().

scripts/run_news_channel.py runs write() on a schedule (see
.github/workflows/news_channel.yml) and commits the result;
routers/news_channel.py serves it. Nothing else on the dashboard reads from
this, and it reads from nothing else -- nothing here touches board
companies, setups, or any other screen.
"""
from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
NEWS_FILE = BACKEND_DIR / "data" / "news_channel" / "latest.json"
API_URL = "https://newsdata.io/api/1/latest"
GOOGLE_RSS_URL = "https://news.google.com/rss/search"
GOOGLE_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
BL_FEEDS = ["economy", "economy/policy", "companies", "markets", "news", "news/world", "economy/agri-business"]
BL_URL = "https://www.thehindubusinessline.com/{}/feeder/default.rss"   # ~60 latest items each
GOOGLE_WINDOW_DAYS = 3     # the query's own "when:Nd" -- wider than LOOKBACK_HOURS so a story near the
                           # edge still gets picked up; LOOKBACK_HOURS is still what decides what's kept
MAX_PER_COUNTRY = 10       # one page per country per run -- 1 credit each
LOOKBACK_HOURS = 48        # older stories are dropped from the feed on write
DAILY_CAP = 180            # newsdata.io's real ceiling is 200/day; this stops
                            # well short of it, on purpose, so a scheduling
                            # mistake or a burst of "Refresh now" clicks can
                            # never actually exhaust the key for the rest of
                            # the day -- see the module docstring

# Each country's `keywords` is the newsdata.io query (its own <=100-char OR
# list -- see the module docstring). `sectors` is the real filter: a
# headline only survives if it hits one of these phrases too, checked
# against the raw title/description text, not newsdata's own fuzzy match.
COUNTRIES = {
    "cn": {
        "name": "China",
        "keywords": 'chemical OR pharma OR lithium OR solar OR battery OR steel OR graphite OR "rare earth"',
        "sectors": [
            ("Specialty chemicals", ["chemical", "dye ", "pigment", "agrochemical intermediate"]),
            ("Pharma APIs / bulk drugs", ["bulk drug", "active pharmaceutical ingredient",
                                          "api price", "api export", "pharma intermediate"]),
            ("Rare earths & critical minerals", ["rare earth", "lithium", "cobalt", "graphite",
                                                  "magnet export"]),
            ("Solar", ["solar", "polysilicon", "photovoltaic", "pv cell", "solar panel"]),
            ("Battery / EV materials", ["battery", "ev battery", "lithium-ion", "cathode"]),
            ("Steel", ["steel"]),
            ("Electronics components", ["semiconductor", "chip export", "chip ban"]),
        ],
    },
    "in": {
        "name": "India",
        "keywords": "pharma OR generic OR textile OR cotton OR diamond OR jewellery OR agrochemical OR API",
        "sectors": [
            ("Generic pharma", ["generic drug", "pharma export", "formulation export", "usfda"]),
            ("IT services", ["it services", "it export", "software export"]),
            ("Textiles & cotton", ["textile", "cotton export", "apparel export", "yarn"]),
            ("Gems & jewellery", ["diamond", "jewellery export", "gem export", "polished diamond"]),
            ("Agrochemicals", ["agrochemical", "crop protection", "pesticide export"]),
            ("Auto components", ["auto component", "auto parts export"]),
        ],
    },
    "us": {
        "name": "USA",
        "keywords": "semiconductor OR biotech OR FDA OR defense OR aerospace OR soybean OR tariff OR shale",
        "sectors": [
            ("Semiconductors", ["semiconductor", "chip export", "chip ban", "export control"]),
            ("Biotech / FDA", ["fda approval", "fda warning", "biotech", "patent"]),
            ("Defence & aerospace", ["defense contract", "aerospace", "military aircraft"]),
            ("Agri commodities", ["soybean", "corn export", "wheat export"]),
            ("Oil & gas / shale", ["shale", "crude oil", "oil price"]),
            ("Tariffs & trade policy", ["tariff", "trade deal", "trade war", "sanction"]),
        ],
    },
    "jp": {
        "name": "Japan",
        "keywords": 'semiconductor OR robot OR "machine tool" OR MLCC OR "carbon fiber" OR steel OR toyota OR ship',
        "sectors": [
            ("Semiconductor materials & equipment", ["semiconductor", "photoresist", "silicon wafer", "chip equipment",
                                                      "chipmaking", "chip material"]),
            ("Robotics & machine tools", ["robot", "machine tool", "factory automation", "fanuc", "yaskawa"]),
            ("Auto & EV", ["toyota", "honda", "nissan", "suzuki", "auto maker", "automaker", "hybrid car"]),
            ("Electronic components", ["mlcc", "capacitor", "electronic component", "image sensor", "murata", "tdk"]),
            ("Specialty chemicals & materials", ["specialty chemical", "carbon fiber", "polyimide", "fluorine",
                                                  "advanced material"]),
            ("Steel & shipbuilding", ["steel", "shipbuilding", "shipbuilder"]),
        ],
    },
}

# Trade-remedy news (anti-dumping probes, countervailing/safeguard duties) is
# a direct, tradeable catalyst for the Indian maker on the protected side, and
# the goods involved (glycine, say) are often too obscure for any product
# keyword list to name -- so this is matched on the action, not the product.
# Added to every country's sector list below.
TRADE_REMEDY = ("Trade remedies / anti-dumping",
                ["anti-dumping", "antidumping", "anti dumping", "countervailing", "safeguard duty", "dgtr"])
for _cfg in COUNTRIES.values():
    _cfg["sectors"].append(TRADE_REMEDY)

# BusinessLine is an Indian desk covering every country, so its feed is
# sorted into a country by who the headline is about, not by where it was
# published. First match wins; nothing matched = India.
_COUNTRY_HINTS = (
    ("cn", ("china", "chinese", "beijing")),
    ("jp", ("japan", "japanese", "tokyo")),
    ("us", (" u.s.", " us ", " usa ", "american", "trump", "washington", " fda ")),
)

# Two cheap tells that a headline is about an actual price move, not just
# sector news in general -- either is enough to flag it, no proximity check
# (this is a "read me first" hint for a person, not an automated signal).
_MOVE_WORDS = ("rise", "rises", "rising", "rose", "jump", "jumps", "surge", "surges", "soar", "soars",
               "climb", "climbs", "rally", "gain", "gains", "fall", "falls", "falling", "fell", "drop",
               "drops", "plunge", "plunges", "slump", "slumps", "decline", "declines", "cut", "cuts",
               "slash", "slashes", "hike", "hikes")
_SUPPLY_WORDS = ("ban", "curb", "curbs", "halt", "restrict", "restriction", "shortage", "surplus",
                 "glut", "tariff", "tariffs", "quota", "export control", "stockpile", "capacity cut")
_PCT = re.compile(r"\d+(\.\d+)?\s?%")

# Press-release mills, not desks with editorial standards -- the same problem
# app/movers/news.py's ALLOWED_SOURCES exists for, just a denylist here
# instead of an allowlist, since global wire coverage is too varied to name
# every legitimate outlet up front.
_BLOCKED_SOURCES = {"businesswire", "business wire", "pr newswire", "prnewswire", "globenewswire",
                     "globe newswire", "abnewswire", "accesswire", "einpresswire", "issuewire"}


def _is_price_move(text: str) -> bool:
    low = text.lower()
    if _PCT.search(low):
        return True
    return any(f" {w} " in f" {low} " for w in _MOVE_WORDS + _SUPPLY_WORDS)


def _sector_matches(title: str, sectors: list[tuple[str, list[str]]]) -> list[str]:
    """Title only, not description: a keyword incidentally mentioned deep in
    a summary (a paleontology story's "chemical signature", say) still isn't
    what this headline is about, and free-tier descriptions are missing on
    most results anyway."""
    low = title.lower()
    return [name for name, kws in sectors if any(kw in low for kw in kws)]


def fetch_country(code: str, api_key: str, session: requests.Session) -> list[dict]:
    """One newsdata.io request for this country -- one credit, whatever the
    result count. Returns already-tagged, already-filtered articles."""
    cfg = COUNTRIES[code]
    params = {"apikey": api_key, "country": code, "language": "en", "category": "business",
              "prioritydomain": "top", "q": cfg["keywords"], "size": MAX_PER_COUNTRY}
    r = session.get(API_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "success":
        raise RuntimeError(f"newsdata.io [{code}]: {data.get('results', {}).get('message', data)}")
    out = []
    for a in data.get("results") or []:
        source = (a.get("source_name") or a.get("source_id") or "").lower()
        if any(b in source for b in _BLOCKED_SOURCES):
            continue
        title = a.get("title") or ""
        sectors = _sector_matches(title, cfg["sectors"])
        if not sectors:
            continue   # newsdata's own match is loose; ours is the real gate
        text = f"{title} {a.get('description') or ''}"
        out.append({
            "country": code, "sectors": sectors, "title": title,
            "link": a.get("link"), "source": a.get("source_name"),
            "published": a.get("pubDate"), "price_move": _is_price_move(text),
        })
    return out


def _gnews_query(country_name: str, keywords: list[str]) -> str:
    terms = " OR ".join(f'"{k}"' if " " in k else k for k in keywords)
    return f"{country_name} ({terms}) when:{GOOGLE_WINDOW_DAYS}d"


def _gnews_published(raw: str | None) -> str:
    """RFC 822 ("Mon, 21 Sep 2026 07:45:45 GMT") -> the same "%Y-%m-%d %H:%M:%S"
    shape newsdata.io's pubDate already uses, so write()'s merge/sort/cutoff
    logic can compare both sources' timestamps as plain strings, unchanged."""
    if not raw:
        return ""
    try:
        from datetime import timezone
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)   # BusinessLine sends +0530; the feed is UTC throughout
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ""


def fetch_google_sector(code: str, sector_name: str, keywords: list[str], session: requests.Session) -> list[dict]:
    """One Google News RSS request for one sector of one country -- no key,
    no credit, ~100 results where newsdata.io's free plan hard-caps at 10.
    Returns already-tagged, already-filtered articles, same shape as
    fetch_country() above so write() merges both without caring which
    source an item came from."""
    cfg = COUNTRIES[code]
    q = _gnews_query(cfg["name"], keywords)
    for attempt in range(3):   # Google throttles in bursts (503/429); a short backoff usually clears it
        r = session.get(GOOGLE_RSS_URL, params={"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
                        headers=GOOGLE_HEADERS, timeout=20)
        if r.status_code not in (429, 503) or attempt == 2:
            break
        time.sleep(3 * (attempt + 1))
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)].strip()
        if any(b in source.lower() for b in _BLOCKED_SOURCES):
            continue
        sectors = _sector_matches(title, cfg["sectors"])
        if not sectors:
            continue   # a sector-targeted query still isn't a guarantee -- same gate as newsdata.io
        link = (item.findtext("link") or "").strip()
        out.append({
            "country": code, "sectors": sectors, "title": title, "link": link, "source": source or None,
            "published": _gnews_published(item.findtext("pubDate")), "price_move": _is_price_move(title),
        })
    return out


def _bl_country(title: str) -> str:
    low = f" {title.lower()} "
    for code, words in _COUNTRY_HINTS:
        if any(w in low for w in words):
            return code
    return "in"


def fetch_businessline(section: str, session: requests.Session) -> list[dict]:
    """One BusinessLine section feed (thehindubusinessline.com RSS -- no key,
    no quota, latest ~60 items). Same output shape and same sector gate as
    the other two sources; only the country tag is worked out per headline
    (see _COUNTRY_HINTS)."""
    r = session.get(BL_URL.format(section), headers=GOOGLE_HEADERS, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        code = _bl_country(title)
        sectors = _sector_matches(title, COUNTRIES[code]["sectors"])
        if not sectors:
            continue
        out.append({
            "country": code, "sectors": sectors, "title": title, "link": link, "source": "BusinessLine",
            "published": _gnews_published(item.findtext("pubDate")), "price_move": _is_price_move(title),
        })
    return out


def _quota_today(prev: dict) -> int:
    """Requests already made today (UTC), 0 if this is the first run of a
    new day. Persisted in NEWS_FILE itself since a GitHub Actions run has
    no other memory between schedules."""
    q = prev.get("quota") or {}
    return q.get("calls", 0) if q.get("date") == time.strftime("%Y-%m-%d", time.gmtime()) else 0


def write(api_key: str | None) -> dict:
    """Fetches every sector of every country from Google News (no key needed,
    always runs) plus one request per country from newsdata.io (only when
    api_key is set), and merges the result into the existing feed rather
    than replacing it -- a single run only ever returns each source's
    latest results, so overwriting the file would lose almost everything
    between schedules. Safe to call often regardless -- see the module
    docstring for DAILY_CAP, the hard floor that makes that actually true
    for the newsdata.io side; the Google News side has no quota to floor."""
    prev = load()
    session = requests.Session()
    fresh, errors = [], []

    for code, cfg in COUNTRIES.items():
        for sector_name, keywords in cfg["sectors"]:
            try:
                fresh.extend(fetch_google_sector(code, sector_name, keywords, session))
            except Exception as exc:                               # noqa: BLE001
                errors.append(f"Google News [{cfg['name']}/{sector_name}]: {exc}")
            time.sleep(0.5)   # polite pacing -- this endpoint is unofficial, no documented quota to respect instead

    for section in BL_FEEDS:
        try:
            fresh.extend(fetch_businessline(section, session))
        except Exception as exc:                                   # noqa: BLE001
            errors.append(f"BusinessLine [{section}]: {exc}")
        time.sleep(0.3)

    calls_today = _quota_today(prev)
    if not api_key:
        errors.append("newsdata.io: NEWSDATA_API_KEY not set -- Google News only this run")
    elif calls_today + len(COUNTRIES) > DAILY_CAP:
        errors.append(f"newsdata.io: daily cap reached ({calls_today}/{DAILY_CAP} requests already made today) -- skipping")
    else:
        for code in COUNTRIES:
            try:
                fresh.extend(fetch_country(code, api_key, session))
            except Exception as exc:                               # noqa: BLE001
                errors.append(f"{COUNTRIES[code]['name']}: {exc}")
            time.sleep(0.3)   # newsdata.io asks for a little spacing between calls
        calls_today += len(COUNTRIES)

    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - LOOKBACK_HOURS * 3600))
    seen, merged = set(), []
    for it in sorted(fresh + (prev.get("items") or []), key=lambda x: x.get("published") or "", reverse=True):
        # by link and by headline: the same story arrives from BusinessLine,
        # Google News and newsdata.io under three different URLs
        keys = {it["link"] or it["title"], "t:" + re.sub(r"[^a-z0-9]+", "", it["title"].lower())}
        if keys & seen or (it.get("published") or "") < cutoff:
            continue
        seen |= keys
        merged.append(it)
    payload = {"as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "count": len(merged), "items": merged, "errors": errors,
               "quota": {"date": time.strftime("%Y-%m-%d", time.gmtime()), "calls": calls_today}}
    NEWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = NEWS_FILE.with_name(NEWS_FILE.name + f".{os.getpid()}.tmp")   # atomic: the API reads this file while a refresh writes it
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    os.replace(tmp, NEWS_FILE)
    return payload


LOCK_FILE = NEWS_FILE.with_name(".refresh.lock")


def age_seconds() -> float:
    """Seconds since the feed was last written, inf if there is none yet."""
    try:
        return max(0.0, time.time() - NEWS_FILE.stat().st_mtime)
    except OSError:
        return float("inf")


def refresh_if_stale(api_key: str | None, max_age: int) -> bool:
    """Server-side refresh, so the feed no longer depends on GitHub's
    schedule (which fires every 3-6 hours in practice) or on the committed
    copy reaching a Docker volume that shadows the image's data dir. Safe
    with several gunicorn workers: a lock file (O_EXCL, ignored once 10
    minutes old in case a worker died holding it) lets only one fetch at a
    time. Returns True if it actually fetched."""
    if age_seconds() < max_age:
        return False
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - LOCK_FILE.stat().st_mtime > 600:
            LOCK_FILE.unlink()
    except OSError:
        pass
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        os.close(fd)
        if age_seconds() < max_age:   # another worker finished between our check and the lock
            return False
        write(api_key)
        return True
    finally:
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass


def load() -> dict:
    try:
        return json.loads(NEWS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"as_of": None, "count": 0, "items": [], "errors": []}
