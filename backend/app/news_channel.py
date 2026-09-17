"""
News Channel: China, India and USA news for the sectors each one dominates
globally -- a leading indicator for Indian listed companies downstream (a
Chinese API price hike is a margin story for the Indian bulk-drug makers who
buy from it; a US tariff on steel reroutes demand; and so on).

Deliberately built around a hard constraint: newsdata.io's free plan is 200
requests/day, and its `q` query is capped at 100 characters -- too short to
also filter for price language in the same request. So this makes exactly
one request per country per run (COUNTRIES below), using a short OR'd list
of sector keywords to narrow what comes back, and does the real work --
sector tagging, source quality, and "is this actually a price move" --
here, client-side, the same way app/movers/news.py already does for company
news. Scheduled every 12 hours (6 requests/day, ~3% of the free plan) to
keep the quota nowhere near exhausted.

That schedule alone relies on the cron never being changed and the admin
"Refresh now" button never being clicked to excess, so write() also keeps
its own running count of the day's requests (in NEWS_FILE, since a GitHub
Actions run starts from a fresh checkout each time and has nowhere else to
remember it) and simply declines to call newsdata.io at all once DAILY_CAP
is reached -- a hard floor under the real 200, so the key never actually
gets rate-limited no matter what schedules or admin clicks stack up
against it that day.

scripts/run_news_channel.py runs write() on a schedule (see
.github/workflows/news_channel.yml) and commits the result;
routers/news_channel.py serves it. Nothing else on the dashboard reads from
this, and it reads from nothing else -- nothing here touches board
companies, setups, or any other screen.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
NEWS_FILE = BACKEND_DIR / "data" / "news_channel" / "latest.json"
API_URL = "https://newsdata.io/api/1/latest"
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
}

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


def _quota_today(prev: dict) -> int:
    """Requests already made today (UTC), 0 if this is the first run of a
    new day. Persisted in NEWS_FILE itself since a GitHub Actions run has
    no other memory between schedules."""
    q = prev.get("quota") or {}
    return q.get("calls", 0) if q.get("date") == time.strftime("%Y-%m-%d", time.gmtime()) else 0


def write(api_key: str) -> dict:
    """Fetches all three countries (3 requests total) and merges the result
    into the existing feed, rather than replacing it -- each request only
    returns newsdata.io's latest ~10 results per country, so a run that
    simply overwrote the file would lose almost everything between the
    12-hourly schedule. Safe to call often regardless -- see the module
    docstring for why the quota comfortably allows that, and for DAILY_CAP,
    the hard floor that makes "regardless" actually true."""
    prev = load()
    if not api_key:
        return {**prev, "error": "NEWSDATA_API_KEY not set"}
    calls_today = _quota_today(prev)
    if calls_today + len(COUNTRIES) > DAILY_CAP:
        msg = f"Daily cap reached ({calls_today}/{DAILY_CAP} newsdata.io requests already made today) -- skipping this run"
        return {**prev, "errors": (prev.get("errors") or []) + [msg]}
    session = requests.Session()
    fresh, errors = [], []
    for code in COUNTRIES:
        try:
            fresh.extend(fetch_country(code, api_key, session))
        except Exception as exc:                                   # noqa: BLE001
            errors.append(f"{COUNTRIES[code]['name']}: {exc}")
        time.sleep(0.3)   # newsdata.io asks for a little spacing between calls
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - LOOKBACK_HOURS * 3600))
    seen, merged = set(), []
    for it in sorted(fresh + (prev.get("items") or []), key=lambda x: x.get("published") or "", reverse=True):
        key = it["link"] or it["title"]
        if key in seen or (it.get("published") or "") < cutoff:
            continue
        seen.add(key)
        merged.append(it)
    payload = {"as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "count": len(merged), "items": merged, "errors": errors,
               "quota": {"date": time.strftime("%Y-%m-%d", time.gmtime()), "calls": calls_today + len(COUNTRIES)}}
    NEWS_FILE.parent.mkdir(parents=True, exist_ok=True)
    NEWS_FILE.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return payload


def load() -> dict:
    try:
        return json.loads(NEWS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"as_of": None, "count": 0, "items": [], "errors": []}
