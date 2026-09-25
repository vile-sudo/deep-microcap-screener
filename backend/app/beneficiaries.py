"""News item -> Indian listed companies that sit on the product it is about.

Click a News Channel headline such as "China hydrochloric acid prices up 500% as environmental
checks shut plants" and the dashboard shows which Indian companies make (or use) that product,
ranked, with why -- the mapping an analyst otherwise does by hand after reading the story.

How (rule-based and deterministic: no model call, so it is free, instant and explainable):

1. THE INDEX -- data/product_index/companies.json (scripts/run_product_index.py): for every listed
   NSE/BSE company, what it says it does (screener.in "About"), its industry classification and
   market cap. The board's 455 companies are far too few; the index covers ~4,300.
2. THE LEXICON -- data/supply_chain/lexicon.json: domain knowledge about how products connect
   (hydrochloric acid -> dye intermediates, H-acid, chlor-alkali, ...). It holds no company names.
3. THE HEADLINE -- (a) any lexicon theme whose trigger words appear scores every company whose
   description contains the theme's phrases, plus a bonus when its industry classification fits;
   (b) product words taken straight from the headline (rare enough across all company
   descriptions to be specific -- "hydrochloric acid" yes, "chemical" no) score companies whose
   description contains them, weighted by how rare they are.
4. DIRECTION -- supply squeezes / shutdowns / bans / duties in the headline mean Indian PRODUCERS of
   the product tend to benefit; price collapses / gluts / dumping mean they are under pressure (and
   BUYERS of the product are on the opposite side). Labelled as a rule-of-thumb reading.

It finds companies connected to the story; it does not say a stock will rise. Companies are ranked
by strength of the text/industry match, shown with today's move and distance from the 52-week high
(from the chart job) so a name that has already run is visible as such.
"""
from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path

from .config import BASE_DIR

INDEX_FILE = BASE_DIR / "data" / "product_index" / "companies.json"
LEXICON_FILE = BASE_DIR / "data" / "supply_chain" / "lexicon.json"
UNIVERSE_FILE = BASE_DIR / "chart_data" / "universe.json"

MAX_RESULTS = 12
MIN_SCORE = 3.5
GENERIC_DF = 0.03          # a headline word found in more than 3% of company descriptions is too generic to mean a product

STOP = set("""a an the and or of to in on for with from by at as is are was were be been it its this that these those than then
into over after before amid against about up down out off more most less new old say says said report reports reported
price prices pricing surge surges surged soar soars soared spike spikes spiked jump jumps jumped rally rallies rise rises rose
rising fall falls fell drop drops crash slump plunge plunges hike hikes hiked cut cuts shut shuts shutdown shutdowns closure closures
halt halts curb curbs ban bans banned plant plants factory factories production output supply demand market markets
environmental regulatory regulation regulations rules crackdown inspection inspections policy government govt official officials
china chinese india indian indians japan japanese usa america american us united states beijing delhi mumbai tokyo
year years month months week weeks day days today yesterday quarter percent per cent pct billion million crore lakh lakhs
company companies firm firms shares share stock stocks stake sector industry industries global world international
sees see seen expected expects likely could would may might will can how why what when who which hit hits hitting record
high low higher lower first second third amid due because while during between across among per not no yes also just now
rs inr usd dollar rupee rupees tonne tonnes ton tons kg unit units order orders export exports import imports""".split())

_INDUSTRY_NEG = re.compile(r"\b(plunge|plunges|plunged|slump|slumps|slumped|crash|crashes|crashed|collapse|glut|oversupply|surplus|"
                           r"dumping|dumped|cheap imports|price cut|price war|falls?|fell|drops?|dropped|declines?|declined|weak demand|slows?|slowdown)\b")
_INDUSTRY_POS = re.compile(r"\b(surge|surges|surged|soar|soars|soared|spike|spikes|spiked|jump|jumps|jumped|rally|rallies|rallied|rise|rises|rose|rising|"
                           r"hike|hikes|hiked|shortage|shortages|shutdown|shutdowns|shut down|shuts|shut|closure|closures|curb|curbs|ban|bans|banned|halt|halts|halted|"
                           r"restrict|restricts|restriction|restrictions|crackdown|suspend|suspends|suspended|quota|quotas|export control|export controls|"
                           r"supply disruption|disruption|anti-dumping|antidumping|anti dumping|countervailing|safeguard duty|duty on|tariff on|higher prices|price increase)\b")

_lock = threading.Lock()
_cache: dict = {}


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _cached(name: str, path: Path, build):
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with _lock:
        hit = _cache.get(name)
        if hit and hit[0] == mtime:
            return hit[1]
    value = build()
    with _lock:
        _cache[name] = (mtime, value)
    return value


def _index() -> dict | None:
    def build():
        raw = (_load(INDEX_FILE, {}).get("companies")) or {}
        docs = []
        for sym, c in raw.items():
            # banks, NBFCs and insurers are never "the company that makes it"; they only show up because a
            # word in the headline happens to be in their name (a battery maker's finance arm, ...)
            if (c.get("industry") or [""])[0].lower() in ("financial services", "finance"):
                continue
            path = " > ".join(c.get("industry") or [])
            docs.append({**c, "symbol": sym, "_industry": path.lower(),
                         "_text": f" {(c.get('name') or '')} {path} {c.get('about') or ''} ".lower()})
        return docs
    return _cached("index", INDEX_FILE, build)


def _lexicon() -> list[dict]:
    return _cached("lexicon", LEXICON_FILE, lambda: _load(LEXICON_FILE, {}).get("themes") or []) or []


def _market() -> dict:
    """index key (NSE symbol or BSE code) -> {last, chg_pct, from_high_pct, status} from the chart job."""
    def build():
        out = {}
        for k, v in (_load(UNIVERSE_FILE, {}).get("stocks") or {}).items():
            code = k.split("-", 1)[1] if "-" in k else k
            out[code] = {"last": v.get("last"), "chg_pct": v.get("chg_pct"), "from_high_pct": v.get("from_high_pct"), "status": v.get("status")}
        return out
    return _cached("market", UNIVERSE_FILE, build) or {}


def _phrase_re(phrase: str) -> re.Pattern:
    p = phrase.strip().lower()
    return re.compile(r"(?<![a-z0-9])" + re.escape(p).replace(r"\ ", r"[\s\-]") + r"(?![a-z])")


def _title_terms(title: str) -> list[str]:
    """Candidate product phrases in a headline: 1-3 word runs of non-stopwords."""
    words = re.findall(r"[a-z][a-z0-9&\-]*", title.lower())
    out, seen = [], set()
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            run = words[i:i + n]
            if any(w in STOP or len(w) < 3 for w in (run[0], run[-1])) or any(w in STOP for w in run) and n > 1:
                continue
            if n == 1 and len(run[0]) < 5:
                continue
            ph = " ".join(run)
            if ph not in seen:
                seen.add(ph)
                out.append(ph)
    return out


def direction(title: str) -> dict:
    t = title.lower()
    pos, neg = bool(_INDUSTRY_POS.search(t)), bool(_INDUSTRY_NEG.search(t))
    if pos and not neg:
        return {"signal": "benefit", "label": "Supply squeeze / price spike / duty — Indian producers of this product tend to benefit",
                "buyers": "Companies that BUY this product face higher input costs."}
    if neg and not pos:
        return {"signal": "pressure", "label": "Price fall / glut / dumping — Indian producers of this product are under pressure",
                "buyers": "Companies that BUY this product may see lower input costs."}
    return {"signal": "unclear", "label": "Direction unclear from the headline — read the story; these are the companies exposed to the product",
            "buyers": ""}


_results: dict = {}          # (index mtime, title) -> result; the same headline is opened again and again


def find(title: str, sectors: list[str] | None = None, country: str | None = None) -> dict:
    try:
        stamp = INDEX_FILE.stat().st_mtime
    except OSError:
        stamp = 0
    key = (stamp, title)
    with _lock:
        if key in _results:
            return _results[key]
    out = _find(title, sectors, country)
    with _lock:
        if len(_results) > 400:
            _results.clear()
        _results[key] = out
    return out


def _find(title: str, sectors: list[str] | None = None, country: str | None = None) -> dict:
    docs = _index()
    result = {"title": title, "index_size": len(docs or []), "direction": direction(title), "themes": [], "terms": [], "companies": []}
    if not docs:
        return result
    tl = " " + title.lower() + " "
    n = len(docs)
    scores: dict[int, dict] = {}

    def add(i: int, pts: float, why: str):
        s = scores.setdefault(i, {"score": 0.0, "why": []})
        s["score"] += pts
        if why and why not in s["why"]:
            s["why"].append(why)

    # (a) lexicon themes whose trigger words are in the headline
    for th in _lexicon():
        if not any(_phrase_re(t).search(tl) for t in th.get("terms", [])):
            continue
        result["themes"].append({"id": th["id"], "note": th.get("note", "")})
        inds = [x.lower() for x in th.get("industries", [])]
        for phrase, w in th.get("expand", []):
            rx = _phrase_re(phrase)
            for i, d in enumerate(docs):
                if rx.search(d["_text"]):
                    add(i, 3.0 * w, phrase.strip())
        weak = [x.lower() for x in th.get("industries_weak", [])]
        for i, d in enumerate(docs):
            if any(ind in d["_industry"] for ind in inds):
                add(i, 4.0, "industry: " + (d.get("industry") or ["", "", "", ""])[-1])
            elif any(ind in d["_industry"] for ind in weak):
                add(i, 1.5, "")            # a broad sector fit: helps a company that also matches on words, never enough alone

    # (b) product words from the headline itself, weighted by rarity across company descriptions
    for ph in _title_terms(title):
        rx = _phrase_re(ph)
        hits = [i for i, d in enumerate(docs) if rx.search(d["_text"])]
        if not hits or len(hits) > GENERIC_DF * n:
            continue
        idf = math.log(n / len(hits))
        w = idf * (1.4 if " " in ph else 0.9)
        result["terms"].append({"term": ph, "companies": len(hits)})
        for i in hits:
            add(i, w, ph)

    if not scores:
        return result
    top = sorted(scores.items(), key=lambda kv: kv[1]["score"], reverse=True)
    best = top[0][1]["score"]
    market = _market()
    for i, s in top:
        if s["score"] < MIN_SCORE or s["score"] < 0.3 * best or len(result["companies"]) >= MAX_RESULTS:
            break
        d = docs[i]
        m = market.get(d["symbol"], {})
        result["companies"].append({
            "symbol": d["symbol"], "name": d.get("name"), "industry": (d.get("industry") or [])[-2:], "mcap_cr": d.get("mcap_cr"),
            "price": d.get("price"), "about": (d.get("about") or "")[:220], "match": round(min(100, s["score"] / best * 100)),
            "why": s["why"][:6], "chg_pct": m.get("chg_pct"), "from_high_pct": m.get("from_high_pct"),
            "url": f"https://www.screener.in/company/{d['symbol']}/",
        })
    return result
