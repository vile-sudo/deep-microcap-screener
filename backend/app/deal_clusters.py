"""Institutional deal clusters: a stock with unusually many, and unusually one-sided, disclosed
bulk/block deals on one session -- the signal behind the Alerts bell's "Deal clusters".

Why this works without guessing at *why* anyone traded: a two-party block deal always nets to an exact
50/50 split of value (one known buyer, one known seller, same quantity and price), so a session's worth
of pure block-deal rotation among institutions is not directional -- ownership just changed hands. A real
skew away from 50/50 only appears when many bulk-deal participants pile onto one side without an equal
and opposite block counterparty on the other -- which is exactly institutional accumulation or
distribution. So the imbalance itself, computed from NSE's own disclosed reports with no external
assumption, is the signal.

One kind of item, deal_buy / deal_sell: directional -- one side is at least DOMINANCE of the day's
disclosed value, with at least MIN_SIDE_CLIENTS distinct counterparties on that side (several institutions
agreeing, not one large trade), and at least MIN_TOTAL_CR crore disclosed in the stock that session. Pure
matched block-deal rotation with no such skew (the common case around an IPO lock-in expiry, where the
day's buy value equals its sell value almost exactly) is deliberately left out: it is real turnover, but it
does not point either way, so it is not an actionable "someone is accumulating/distributing" signal.

Input is the deduplicated deal rows this board already collects (data/deals/latest.json, written by
scripts/run_deals.py): the same trade is reported under both the bulk and the block disclosure when it
qualifies for both, so it is de-duplicated here by (date, symbol, client, side, quantity, price) before
anything is summed, never double-counted.

Each item optionally carries `context`: a same-day company announcement (data/announcements/latest.json)
that might explain the activity -- a QIP/preferential allotment, an OFS, a promoter/investor disclosure,
an anchor lock-in-linked event, and so on. This is offered as a lead, not a verified reason: the exchange
does not disclose *why* a counterparty traded, so a reader is always told to check the filing themselves.
"""
from __future__ import annotations

from collections import defaultdict

MIN_TOTAL_CR = 20.0          # a session's disclosed value in the stock must reach this to matter at all
DOMINANCE = 0.60              # one side must be at least this share of value to call it directional
MIN_SIDE_CLIENTS = 3          # that many distinct counterparties on the dominant side (not one whale trade)
TOP_N = 5                     # counterparties shown per side

# Filing categories/keywords that plausibly explain a burst of institutional deals -- offered as a lead,
# matched loosely, never asserted as the actual reason. Plain strings, not regex fragments -- word
# boundaries are added once, below, so a literal "\b" typo here can't silently become a backspace byte.
CONTEXT_KEYWORDS = ("qip", "preferential", "anchor", "offer for sale", "ofs", "block deal", "bulk deal",
                    "stake sale", "stake purchase", "acquisition", "encumbrance", "pledge", "lock-in",
                    "lock in", "promoter", "open offer", "delisting", "buyback")


def _dedupe(deals: list[dict]) -> list[dict]:
    seen, out = set(), []
    for d in deals:
        k = (d["date"], d["symbol"], d["client"], d["side"], d["quantity"], d["price"])
        if k in seen:
            continue
        seen.add(k)
        out.append(d)
    return out


def _find_context(announcements: list[dict], symbol: str, date: str):
    import re
    pat = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in CONTEXT_KEYWORDS) + r")\b", re.I)
    best = None
    for a in announcements:
        if a.get("symbol") != symbol or abs((_day_diff(a.get("date"), date))) > 1:
            continue
        text = f"{a.get('category') or ''} {a.get('summary') or ''}"
        if pat.search(text) and (best is None or (a.get("weight") or 0) > (best.get("weight") or 0)):
            best = a
    if not best:
        return None
    return {"summary": (best.get("summary") or "")[:280], "category": best.get("category"), "date": best.get("date"), "url": best.get("url")}


def _day_diff(a: str | None, b: str | None) -> int:
    from datetime import date as _date
    if not a or not b:
        return 99
    try:
        return (_date.fromisoformat(a) - _date.fromisoformat(b)).days
    except ValueError:
        return 99


def _top(rows: list[dict], side: str) -> list[dict]:
    by = defaultdict(float)
    for r in rows:
        if r["side"] == side:
            by[r["client"]] += r["value_cr"]
    return [{"client": c, "value_cr": round(v, 2)} for c, v in sorted(by.items(), key=lambda kv: -kv[1])[:TOP_N]]


def build(deals: list[dict], announcements: list[dict] | None = None, market: dict | None = None) -> dict[str, list[dict]]:
    """{date: [item, ...]} for every session present in `deals`. `announcements` (data/announcements/
    latest.json's list) is optional context; `market` (symbol -> {close, chg_pct}) is optional enrichment."""
    rows = _dedupe(deals)
    by_day: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_day[r["date"]][r["symbol"]].append(r)

    announcements = announcements or []
    market = market or {}
    out: dict[str, list[dict]] = {}
    for day, symbols in by_day.items():
        items = []
        for symbol, g in symbols.items():
            buy = [r for r in g if r["side"] == "BUY"]
            sell = [r for r in g if r["side"] == "SELL"]
            buy_val, sell_val = sum(r["value_cr"] for r in buy), sum(r["value_cr"] for r in sell)
            total = buy_val + sell_val
            if total < MIN_TOTAL_CR:
                continue
            buy_clients, sell_clients = len({r["client"] for r in buy}), len({r["client"] for r in sell})
            dom_side, dom_val, dom_clients = ("BUY", buy_val, buy_clients) if buy_val >= sell_val else ("SELL", sell_val, sell_clients)
            dominance = dom_val / total
            if not (dominance >= DOMINANCE and dom_clients >= MIN_SIDE_CLIENTS):
                continue
            rec = g[0]
            item = {
                "type": "deal_buy" if dom_side == "BUY" else "deal_sell",
                "scope": "board" if rec.get("board_code") else "market",
                "code": rec.get("board_code"), "symbol": symbol, "name": rec.get("name") or symbol,
                "date": day, "deals": len(g),
                "buy_value_cr": round(buy_val, 2), "sell_value_cr": round(sell_val, 2), "total_value_cr": round(total, 2),
                "buy_clients": buy_clients, "sell_clients": sell_clients, "dominance_pct": round(dominance * 100, 1),
                "top_buyers": _top(g, "BUY"), "top_sellers": _top(g, "SELL"),
                "context": _find_context(announcements, symbol, day),
            }
            m = market.get(symbol)
            if m:
                item["close"], item["chg_pct"] = m.get("close"), m.get("chg_pct")
            items.append(item)
        items.sort(key=lambda i: -i["total_value_cr"])
        out[day] = items
    return out
