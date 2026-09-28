"""Match News Channel items to board companies whose disclosed business plausibly makes the story
relevant to them -- turns a generic "China rare earth export curb" headline into "may affect: X, Y" on
the dashboard.

No new manual tagging: a company matches a story only because its own disclosed business text
(companies_raw.json's `sector` + `business` fields, already written by the fundamentals refresh) happens
to contain one of the keywords that made news_channel.py tag the story with that sector in the first
place -- e.g. a company whose business mentions "battery" matches a China lithium-export-curb story
tagged "Rare earths & critical minerals". Reusing the same keyword lists means this needs no separate
taxonomy to keep in sync with news_channel.COUNTRIES.

This is a plausible-relevance hint, exactly like deal_clusters.py's announcement "context": offered as a
lead for a reader to judge, not asserted as a confirmed impact -- the exchange doesn't disclose actual
supply-chain relationships either, so neither can this.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import news_channel as nc

BACKEND_DIR = Path(__file__).resolve().parent.parent
COMPANIES_RAW = BACKEND_DIR / "data" / "companies_raw.json"
MAX_MATCHES = 4          # shown per story -- a lead list, not an exhaustive one

_cache: dict = {"mtime": None, "index": None}


def _build_index() -> dict[tuple[str, str], list[dict]]:
    """(country, sector_name) -> up to MAX_MATCHES board companies (highest-ranked first, since
    companies_raw.json is already sorted by rank_v4) whose sector/business text contains one of that
    sector's own keywords."""
    try:
        records = json.loads(COMPANIES_RAW.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        records = []
    corpus = []
    for rec in records:
        code, name = rec.get("code"), rec.get("name")
        if not code or not name:
            continue
        text = f"{rec.get('sector') or ''} {rec.get('business') or ''}".lower()
        corpus.append((code, name, text))

    index: dict[tuple[str, str], list[dict]] = {}
    for country, cfg in nc.COUNTRIES.items():
        for sector_name, keywords in cfg["sectors"]:
            key = (country, sector_name)
            if key in index:      # TRADE_REMEDY is the same object shared by every country's list
                continue
            matches = []
            for code, name, text in corpus:
                if any(kw in text for kw in keywords):
                    matches.append({"code": code, "name": name})
                    if len(matches) >= MAX_MATCHES:
                        break
            if matches:
                index[key] = matches
    return index


def _index() -> dict:
    try:
        mtime = COMPANIES_RAW.stat().st_mtime
    except OSError:
        mtime = None
    if _cache["mtime"] != mtime:
        _cache["index"] = _build_index()
        _cache["mtime"] = mtime
    return _cache["index"] or {}


def companies_for(item: dict) -> list[dict]:
    """Board companies plausibly affected by this News Channel item -- deduped, capped at MAX_MATCHES,
    highest-ranked first."""
    idx = _index()
    country = item.get("country")
    seen: set[str] = set()
    out: list[dict] = []
    for sector_name in item.get("sectors") or []:
        for c in idx.get((country, sector_name), ()):
            if c["code"] in seen:
                continue
            seen.add(c["code"])
            out.append(c)
            if len(out) >= MAX_MATCHES:
                return out
    return out


def enrich(items: list[dict]) -> list[dict]:
    """New item dicts (the input is never mutated -- callers may hold a cached copy) with a
    `companies` field added, omitted entirely when nothing matched so old cached clients ignore it."""
    out = []
    for it in items:
        companies = companies_for(it)
        out.append({**it, "companies": companies} if companies else it)
    return out
