"""Insider clusters: several DIFFERENT insiders (promoters/directors/KMPs) of the same company buying,
or selling, within the same rolling window -- the signal behind the Alerts bell's "Insider clusters".

Why a rolling window, not a single session like deal_clusters.py's bulk/block clusters: a bulk/block deal
settles and is disclosed the same day, so "today's activity" is a real unit. An insider trade is disclosed
days after the fact (the person has up to 2 trading days to report it, and NSE's own broadcast can lag
further), and one person's single transaction is common and unremarkable on its own -- the signal here is
specifically MULTIPLE INDEPENDENT PEOPLE reaching the same decision close together in time, which only
shows up by looking across a window, not one calendar day.

One kind of item, insider_buy / insider_sell: at least MIN_INSIDERS distinct people (by name) bought, or
sold, on the open market (side BUY/SELL only -- pledge/pledge-invoke/pledge-revoke are a collateral
signal, not a conviction one, and are excluded here) in the same stock within a trailing WINDOW_DAYS-day
window. Emitted once, dated at the transaction that pushed the distinct-person count to a NEW high for
that stock -- not re-emitted every day the same cluster remains true, so a reader sees "3rd insider just
joined in" as one dated event, not the same alert every day for a week.

Reuses deal_clusters.py's announcement-context lookup (a same-day filing that might explain the
activity) since the "possible reason, not a confirmed one" framing is identical."""
from __future__ import annotations

import re
from collections import defaultdict, deque
from datetime import date as _date, timedelta

from .deal_clusters import find_context

WINDOW_DAYS = 7                # how far back "several insiders" looks
MIN_INSIDERS = 2                # that many distinct people, same direction, within the window
TOP_N = 5

_HONORIFIC = re.compile(r"^(mr|mrs|ms|miss|dr|shri|smt)\.?\s+", re.I)


def _person_key(name: str) -> str:
    """Same person, counted once: NSE's own disclosures are not consistent about an honorific prefix
    ("Mr. Anjan Chatterjee" vs "Anjan Chatterjee" turned up in real data for the same filer) --
    de-duplicated on a normalised key, while the original name (with whatever prefix that particular
    filing used) is still what gets displayed."""
    return _HONORIFIC.sub("", name.strip().lower())


def build(trades: list[dict], announcements: list[dict] | None = None, market: dict | None = None) -> dict[str, list[dict]]:
    """{date: [item, ...]} -- one entry per (symbol, side) each time its rolling distinct-insider count
    reaches a new high. `trades` is data/insider_trades/latest.json's own `trades` list."""
    announcements = announcements or []
    market = market or {}

    by_symbol_side: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in trades:
        if t.get("side") not in ("BUY", "SELL"):
            continue   # pledge / pledge invoke / pledge revoke -- a different signal, not conviction
        if not t.get("date"):
            continue
        by_symbol_side[(t["symbol"], t["side"])].append(t)

    by_date_symbol_type: dict[tuple[str, str, str], dict] = {}
    for (symbol, side), rows in by_symbol_side.items():
        rows = sorted(rows, key=lambda r: r["date"])
        window: deque[tuple[_date, dict]] = deque()
        best = 0
        item_type = "insider_buy" if side == "BUY" else "insider_sell"
        for r in rows:
            try:
                d = _date.fromisoformat(r["date"])
            except ValueError:
                continue
            window.append((d, r))
            cutoff = d - timedelta(days=WINDOW_DAYS - 1)
            while window and window[0][0] < cutoff:
                window.popleft()
            by_person: dict[str, dict] = {}
            for _, wr in window:
                by_person[_person_key(wr["person"])] = wr   # last (most recent) transaction per person in the window
            if len(by_person) < MIN_INSIDERS or len(by_person) <= best:
                continue
            best = len(by_person)
            people = sorted(by_person.values(), key=lambda p: -p["value_cr"])
            # keyed by (date, symbol, type) rather than appended: two different rows landing on the same
            # date each pushing the count higher must replace, not duplicate, that date's card
            by_date_symbol_type[(r["date"], symbol, item_type)] = {
                "type": item_type,
                "scope": "board" if r.get("board_code") else "market",
                "code": r.get("board_code"), "symbol": symbol, "name": r.get("name") or symbol,
                "date": r["date"], "window_days": WINDOW_DAYS,
                "insiders": len(people), "total_value_cr": round(sum(p["value_cr"] for p in people), 2),
                "people": [{"name": p["person"], "category": p["category"], "value_cr": round(p["value_cr"], 2)}
                           for p in people[:TOP_N]],
                "context": find_context(announcements, symbol, r["date"]),
                **({"close": market[symbol]["close"], "chg_pct": market[symbol]["chg_pct"]} if symbol in market else {}),
            }

    out: dict[str, list[dict]] = defaultdict(list)
    for (date, _symbol, _type), item in by_date_symbol_type.items():
        out[date].append(item)
    return dict(out)
