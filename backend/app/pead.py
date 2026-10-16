"""Earnings drift (PEAD): Quantpedia's post-earnings-announcement strategy, long only, on NSE results.

https://quantpedia.com/strategies/post-earnings-announcement-effect -- stocks whose results beat their own
trend by the widest margin AND that the market bid up hardest around the announcement keep drifting higher
for weeks. The rules, as specified for the Indian market:

1. Universe: NSE-listed companies with quarterly results (the filing time decides the announcement day).
   Excluded: financials (banks, NBFCs, insurers, brokers... NSE sector "Financial Services"), utilities
   (power, water, waste, city-gas distribution), and stocks under Rs 100 when the signal is known.
2. SUE, the profit surprise: expected net profit = the same quarter last year + drift, where drift is the
   average year-on-year change over the previous 4 quarters (seasonal random walk with drift);
   SUE = (actual - expected) / standard deviation of those year-on-year changes (the last 3-8 quarters).
3. EAR, the price reaction: the stock's return over the 3 sessions around the announcement (the session
   before, the reaction session, the session after) minus the Nifty 500 over the same days.
4. Buckets: SUE and EAR are each cut into fifths using the PREVIOUS results season's cut-offs, so a signal
   only uses numbers known at the time. Buy = top fifth on both.
5. Trade: buy at the open of the 2nd session after the announcement, equal weight, hold 60 sessions.

Built nightly from Results Preview's dataset (app/results_preview.build) -> data/results_preview/pead.json.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HOLD = 60                    # sessions held
MIN_PRICE = 100.0
COST = 0.30                  # round-trip cost assumed in the "after costs" figures, % (brokerage, STT, slippage)
EXCLUDE_SECTORS = {"financial-services", "power", "utilities"}
EXCLUDE_INDUSTRIES = {"LPG/CNG/PNG/LNG Supplier", "Gas Transmission/Marketing", "Trading - Gas"}
INDICES = Path(__file__).resolve().parent.parent / "data" / "sector_strength" / "indices.json"
SECTORS = Path(__file__).resolve().parent.parent / "data" / "sector_strength" / "latest.json"
QUANTPEDIA = "https://quantpedia.com/strategies/post-earnings-announcement-effect"


def sue(NP: list, t: int) -> tuple[float, float, float] | None:
    """(SUE, expected profit, sigma) for quarter t of a quarterly reporter, or None without enough history."""
    if t < 4 or t >= len(NP) or NP[t] is None or NP[t - 4] is None:
        return None
    D = [NP[j] - NP[j - 4] for j in range(max(4, t - 8), t) if NP[j] is not None and NP[j - 4] is not None]
    if len(D) < 3:
        return None
    drift = statistics.mean(D[-4:])
    sd = statistics.pstdev(D)
    if not sd:
        return None
    exp = NP[t - 4] + drift
    return (NP[t] - exp) / sd, exp, sd


def nifty500() -> dict:
    """{date: Nifty 500 close} -- the benchmark (Sector Strength's index history)."""
    try:
        d = json.loads(INDICES.read_text(encoding="utf-8"))
        return {k: v for k, v in zip(d.get("dates") or [], d.get("nifty500") or []) if v}
    except (OSError, ValueError):
        return {}


class Bench:
    def __init__(self):
        self.lvl = nifty500()
        self.days = sorted(self.lvl)

    def at(self, d: str):
        import bisect
        i = bisect.bisect_right(self.days, d) - 1
        return self.lvl[self.days[i]] if i >= 0 else None

    def ret(self, d0: str, d1: str):
        a, b = self.at(d0), self.at(d1)
        return (b / a - 1) * 100 if a and b else None

    def fill(self, dates) -> int:
        """Sessions after the last day in Sector Strength's index history (it can lag a day or two): fetched from
        NSE's daily index file, cached in .bhav_cache -- so EAR never uses a stale Nifty 500 close."""
        last = self.days[-1] if self.days else "0000"
        want = sorted({str(d)[:10] for d in dates if str(d)[:10] > last})[-15:]
        if not want:
            return 0
        from .sector_strength import index_closes
        got = index_closes([date.fromisoformat(d) for d in want], Path(__file__).resolve().parent.parent / ".bhav_cache", 15)
        for d, v in got.items():
            self.lvl[d.isoformat()] = v
        miss = [d for d in want if d not in self.lvl]
        if miss:                                     # NSE's archive often refuses cloud runners: Yahoo's ^CRSLDX
            try:                                     # is the same Nifty 500 close
                import requests
                r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5ECRSLDX?range=3mo&interval=1d",
                                 headers={"User-Agent": "Mozilla/5.0"}, timeout=30).json()["chart"]["result"][0]
                ist = timezone(timedelta(hours=5, minutes=30))
                for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
                    d = datetime.fromtimestamp(t, ist).date().isoformat()
                    if d in miss and c:
                        self.lvl[d] = round(float(c), 2)
            except Exception:  # noqa: BLE001 - a missing day only leaves that EAR blank
                pass
        self.days = sorted(self.lvl)
        return sum(1 for d in want if d in self.lvl)


_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
HISTORY = Path(__file__).resolve().parent.parent / "data" / "results_preview" / "pead_history.json.gz"


def _qafter(q: date) -> date:
    m = q.month + 3
    y = q.year + (m > 12)
    m = m - 12 if m > 12 else m
    return date(y, m, 31 if m in (3, 12) else 30)


def _history() -> dict:
    import gzip
    try:
        return json.loads(gzip.decompress(HISTORY.read_bytes()))
    except (OSError, ValueError, EOFError):
        return {}


def hist_ok(check: list) -> bool:
    """Use a company's NSE XBRL history when it matches screener on the overlapping quarters: all of them, or
    all but one (a single restated quarter) when at least two match. A wrong basis mismatches every quarter."""
    ok, bad = check
    return bad == 0 or (ok >= 2 and bad == 1)


def _prices(rows: list, old: list | None) -> list:
    """The chart's [(date, close, volume, open)], with older bhavcopy days put in front, scaled to meet it."""
    if not rows or not old:
        return rows
    first = rows[0][0]
    m = next((r for r in old if r[0] == first), None)
    if not m or not m[2]:
        return rows
    k = rows[0][1] / m[2]
    return [(d, c * k, None, (o * k) if o else None) for d, o, c in old if d < first and c] + list(rows)


IST = timezone(timedelta(hours=5, minutes=30))


def _next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _next_session(now: datetime) -> date:
    """The next session that hasn't closed yet (weekends skipped; exchange holidays aren't known here)."""
    d = now.date()
    return d if d.weekday() < 5 and now.strftime("%H:%M") < "15:30" else _next_weekday(d)


def _reaction_session(filed: str) -> date:
    d = date.fromisoformat(filed[:10])
    return d if d.weekday() < 5 and filed[11:16] < "15:30" else _next_weekday(d)


def _cuts(xs: list[float]) -> list[float]:
    xs = sorted(xs)
    return [xs[int(len(xs) * k / 5)] for k in (1, 2, 3, 4)]


def _fifth(v: float, cuts: list[float]) -> int:
    return 1 + sum(v >= c for c in cuts)


def _r(v, n=2):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, n)


def build(mk, ix, rows, today: date) -> dict:
    from . import results_read as CR
    from .results_preview import lag_of, qend, reaction_index
    bm = Bench()
    if not bm.days:
        return {"asof": today.isoformat(), "error": "no Nifty 500 history"}
    bm.fill(ix.lvl)                                  # every session the market index has (all stocks' prices)
    excluded = {}
    try:
        sectors = json.loads(SECTORS.read_text(encoding="utf-8")).get("sectors") or []
    except (OSError, ValueError):
        sectors = []
    for sec in sectors:
        if sec.get("group") != "nse":
            continue
        for s in sec.get("stocks") or []:
            if s.get("exchange") == "NSE" and (sec["slug"] in EXCLUDE_SECTORS or s.get("industry") in EXCLUDE_INDUSTRIES):
                excluded[f"NSE:{s.get('symbol')}"] = s.get("industry") or sec["slug"]

    # ---- every NSE quarterly result with a SUE: screener's 12 quarters, extended three years back with
    # pead_history.json.gz (NSE XBRL profits, filing times, older prices -- scripts/pead_history.py)
    H = _history()
    for d, v in (H.get("nifty500") or {}).items():
        bm.lvl.setdefault(d, v)
    bm.days = sorted(bm.lvl)
    events = []
    for key, e in mk.ent.items():
        if not key.startswith("NSE:") or key in excluded:
            continue
        sym = key[4:]
        P0 = e["q"].get("periods") or []
        if lag_of(P0) != 4:
            continue
        npq = {}
        h = (H.get("companies") or {}).get(sym)
        if h and hist_ok(h["check"]):                    # older quarters only where they agree with screener
            npq.update({lab: v["np"] for lab, v in h["q"].items() if v.get("np") is not None})
        for lab, v in zip(P0, e["q"].get("net_profit") or []):
            if v is not None:
                npq[lab] = v
        claude = {lab: v for lab, v in CR.quarters(sym).items() if lab not in npq and v.get("np") is not None}
        npq.update({lab: v["np"] for lab, v in claude.items()})
        if not npq:
            continue
        labs = sorted(npq, key=qend)
        P, q = [], qend(labs[0])
        while q <= qend(labs[-1]):
            P.append(f"{_MON[q.month - 1]} {q.year}")
            q = _qafter(q)
        NP = [npq.get(lab) for lab in P]
        px = _prices(mk.prices(key), (H.get("prices") or {}).get(sym))
        if not px:
            continue
        for t in range(8, len(P)):
            qe = qend(P[t])
            s = sue(NP, t)
            if s is None:
                continue
            filed = mk.result_filed(key, qe) if qe >= date(2023, 9, 30) else None
            filed = filed or ((h or {}).get("q", {}).get(P[t]) or {}).get("filed") or (claude.get(P[t]) or {}).get("filed")
            if not filed:
                continue
            i = reaction_index(px, filed)
            ev = {"key": key, "symbol": e["symbol"], "name": e["name"], "board": bool(e.get("code")), "code": e.get("code"),
                  "q": P[t], "qe": qe.isoformat(), "filed": filed, "sue": s[0], "np": NP[t],
                  "exp": s[1], "sector": (mk.sector.get(key) or "").replace("-", " "),
                  "src": "claude" if P[t] in claude else None}
            if i is None or i < 3:
                if filed[:10] >= px[-1][0]:
                    ev["stage"] = "reacting"              # announced; its reaction session isn't in the data yet
                    ev["px_last"] = px[-1][1]
                    events.append(ev)
                continue
            ev["day0"] = px[i][0]
            # the early variant: decided before the open on the SUE alone, bought at the reaction session's open
            o0 = px[i][3] or None
            if o0 and i + HOLD - 1 < len(px):
                j0 = i + HOLD - 1
                ev["e_ret"] = (px[j0][1] / o0 - 1) * 100
                m0 = bm.ret(px[i - 1][0], px[j0][0])
                ev["e_xs"] = ev["e_ret"] - m0 if m0 is not None else None
                ev["e_px"] = px[i - 1][1]
                ev["gap0"] = (o0 / px[i - 1][1] - 1) * 100
            if o0:
                ev.update({"o0": o0, "e_now": (px[-1][1] / o0 - 1) * 100, "e_held": len(px) - i, "e_last": px[-1][1],
                           "e_px": px[i - 1][1], "e_mkt": bm.ret(px[i - 1][0], px[-1][0])})
            if i + 1 >= len(px):
                ev["stage"] = "reacting"
                r0, b0 = (px[i][1] / px[i - 2][1] - 1) * 100, bm.ret(px[i - 2][0], px[i][0])
                ev["ear_sofar"] = r0 - b0 if b0 is not None else None
                ev["px_last"] = px[-1][1]
                events.append(ev)
                continue
            b3 = bm.ret(px[i - 2][0], px[i + 1][0])
            ev["ear"] = (px[i + 1][1] / px[i - 2][1] - 1) * 100 - b3 if b3 is not None else None
            ev["px1"] = px[i + 1][1]
            if ev["ear"] is None:
                continue
            if i + 2 >= len(px):
                ev["stage"] = "to_buy"                    # signal complete; the entry session is the next one
                events.append(ev)
                continue
            e0 = px[i + 2][3] or px[i + 2][1]
            j = min(i + 1 + HOLD, len(px) - 1)
            ev.update({"entry_date": px[i + 2][0], "entry": e0, "last_date": px[j][0], "last": px[j][1], "held": j - (i + 2) + 1,
                       "ret": (px[j][1] / e0 - 1) * 100})
            ev["mkt"] = bm.ret(px[i + 1][0], px[j][0])
            ev["xs"] = ev["ret"] - ev["mkt"] if ev["mkt"] is not None else None
            ev["stage"] = "closed" if j == i + 1 + HOLD else "open"
            # the daily path while held (for the portfolio curve)
            ev["_path"] = [(px[k][0], (px[k][1] / (e0 if k == i + 2 else px[k - 1][1]) - 1)) for k in range(i + 2, j + 1)]
            events.append(ev)

    # ---- fifths from the previous results season's cut-offs (quarter by quarter, in order)
    byq = defaultdict(list)
    for ev in events:
        byq[ev["q"]].append(ev)
    qs = sorted(byq, key=qend)
    seasons, prev, used = [], None, {}
    for q in qs:
        used[q] = prev
        elig = [ev for ev in byq[q] if ev.get("ear") is not None and (ev.get("px1") or 0) >= MIN_PRICE]
        if prev:
            for ev in byq[q]:
                ev["sq"] = _fifth(ev["sue"], prev["sue"])
                if ev.get("ear") is not None:
                    ev["eq"] = _fifth(ev["ear"], prev["ear"])
                ev["cheap"] = ev.get("px1") is not None and ev["px1"] < MIN_PRICE
                ev["signal"] = ev.get("sq") == 5 and ev.get("eq") == 5 and not ev["cheap"]
            seasons.append({"q": q, "events": len(elig), "sue_cut": _r(prev["sue"][3]), "ear_cut": _r(prev["ear"][3]), "from": prev["q"],
                            "signals": sum(1 for ev in byq[q] if ev.get("signal"))})
        if len(elig) >= 100:
            prev = {"q": q, "sue": _cuts([ev["sue"] for ev in elig]), "ear": _cuts([ev["ear"] for ev in elig])}
    current_cuts = {"from": prev["q"], "sue": _r(prev["sue"][3]), "ear": _r(prev["ear"][3])} if prev else None

    traded = [ev for ev in events if ev.get("signal") and ev.get("entry")]
    done = [ev for ev in traded if ev["stage"] == "closed"]

    def stats(xs):
        xs = [x for x in xs if x.get("ret") is not None]
        if not xs:
            return {"n": 0}
        rets, exs = [x["ret"] for x in xs], [x["xs"] for x in xs if x.get("xs") is not None]
        return {"n": len(xs), "avg": _r(statistics.mean(rets)), "median": _r(statistics.median(rets)),
                "win": _r(100 * sum(r > 0 for r in rets) / len(rets), 1), "avg_cost": _r(statistics.mean(rets) - COST),
                "xs": _r(statistics.mean(exs)) if exs else None, "beat": _r(100 * sum(x > 0 for x in exs) / len(exs), 1) if exs else None}

    graded = [ev for ev in events if ev["stage"] == "closed" and ev.get("sq") and ev.get("eq") and not ev.get("cheap")]
    grid = []
    for sq in range(5, 0, -1):
        row = []
        for eq in range(1, 6):
            c = [ev for ev in graded if ev["sq"] == sq and ev["eq"] == eq]
            row.append({"n": len(c), "xs": _r(statistics.mean(ev["xs"] for ev in c if ev["xs"] is not None)) if c else None,
                        "win": _r(100 * sum(ev["ret"] > 0 for ev in c) / len(c), 0) if c else None})
        grid.append({"sq": sq, "cells": row})
    compare = [
        {"group": "Strategy: top fifth SUE and top fifth EAR", **stats([ev for ev in graded if ev["sq"] == 5 and ev["eq"] == 5])},
        {"group": "Top fifth SUE only (any reaction)", **stats([ev for ev in graded if ev["sq"] == 5])},
        {"group": "Top fifth EAR only (any surprise)", **stats([ev for ev in graded if ev["eq"] == 5])},
        {"group": "Every eligible result", **stats(graded)},
        {"group": "Bottom fifth SUE and bottom fifth EAR (the short side, not traded)", **stats([ev for ev in graded if ev["sq"] == 1 and ev["eq"] == 1])},
    ]
    cohorts = []
    for q in qs:
        c = [ev for ev in traded if ev["q"] == q]
        if c:
            cohorts.append({"q": q, "open": sum(ev["stage"] == "open" for ev in c), **stats(c)})

    # ---- the portfolio, day by day: equal weight across the positions held that day; cash when none
    daily = defaultdict(list)
    for ev in traded:
        for d, x in ev["_path"]:
            daily[d].append(x)
    curve, nav, mkt = [], 1.0, 1.0
    days = sorted(daily)
    if days:
        lv = bm.lvl
        ld = sorted(set(d for d in lv if d >= days[0]) | set(days))
        prev_l = None
        for d in ld:
            if d in daily:
                nav *= 1 + statistics.mean(daily[d])
            if prev_l is not None and bm.at(prev_l):
                mkt *= bm.at(d) / bm.at(prev_l)
            prev_l = d
            curve.append([d, round((nav - 1) * 100, 2), round((mkt - 1) * 100, 2), len(daily.get(d, []))])

    def row(ev):
        out = {k: ev.get(k) for k in ("symbol", "name", "board", "code", "q", "filed", "day0", "entry_date", "last_date", "held", "stage", "sector")}
        out.update({"sue": _r(ev["sue"]), "ear": _r(ev.get("ear")), "entry": _r(ev.get("entry")), "last": _r(ev.get("last")),
                    "ret": _r(ev.get("ret")), "mkt": _r(ev.get("mkt")), "xs": _r(ev.get("xs")), "np": _r(ev.get("np")), "exp": _r(ev.get("exp")),
                    "sq": ev.get("sq"), "eq": ev.get("eq"), "src": ev.get("src")})
        return out

    hold = sorted([ev for ev in traded if ev["stage"] == "open"], key=lambda e: e["entry_date"], reverse=True)
    to_buy = [ev for ev in events if ev.get("signal") and ev["stage"] == "to_buy"]
    # announced, top-fifth surprise on the current cut-offs, reaction still coming in
    watch = []
    cur = used.get(qs[-1]) if qs else None
    if cur:
        for ev in events:
            if ev["stage"] == "reacting" and ev["q"] == qs[-1] and _fifth(ev["sue"], cur["sue"]) == 5:
                watch.append({**row(ev), "ear_sofar": _r(ev.get("ear_sofar"))})
    # ---- before the open: results filed since the last close react in the next session; the early variant
    # decides on the profit surprise alone (SUE fifth) and buys the top fifth at that session's open
    now = datetime.now(IST)
    nxt = _next_session(now)
    morning = []
    for ev in events:
        if ev["stage"] != "reacting" or not ev.get("sq"):
            continue
        rs = _reaction_session(ev["filed"])
        need = _r((used.get(ev["q"]) or {}).get("ear", [None] * 4)[3])
        cheap = (ev.get("px_last") or 0) < MIN_PRICE
        if rs == nxt:
            call = "buy" if ev["sq"] == 5 and not cheap else "avoid" if ev["sq"] == 1 else "none"
            morning.append({**row(ev), "call": call, "cheap": cheap, "price": _r(ev.get("px_last")), "ear_needed": need, "reacted": False})
        elif rs < nxt and (nxt - rs).days <= 5:
            # filed during market hours: it already reacted, too late for the early version; the full strategy
            # decides after the next close (3-day move vs the cut-off) and buys the session after
            on = ev["sq"] == 5 and not cheap and ev.get("ear_sofar") is not None and need is not None and ev["ear_sofar"] >= need
            call = "track" if ev["sq"] == 5 and not cheap else "avoid" if ev["sq"] == 1 else "none"
            morning.append({**row(ev), "call": call, "cheap": cheap, "price": _r(ev.get("px_last")), "ear_needed": need, "reacted": True,
                            "ear_sofar": _r(ev.get("ear_sofar")), "on_track": on, "reaction_day": rs.isoformat(),
                            "buy_day": _next_weekday(_next_weekday(rs)).isoformat()})
    morning.sort(key=lambda x: -(x["sue"] or 0))
    early_g = [ev for ev in events if ev.get("e_ret") is not None and ev.get("sq") and (ev.get("e_px") or 0) >= MIN_PRICE]
    early_hold = [ev for ev in events if ev.get("sq") == 5 and ev.get("o0") and ev.get("e_ret") is None and (ev.get("e_px") or 0) >= MIN_PRICE
                  and (ev.get("e_held") or 0) >= 1]
    early = {
        "fifths": [{"sq": k, **stats([{"ret": ev["e_ret"], "xs": ev["e_xs"]} for ev in early_g if ev["sq"] == k])} for k in (5, 4, 3, 2, 1)],
        "seasons": [{"q": q, **stats([{"ret": ev["e_ret"], "xs": ev["e_xs"]} for ev in early_g if ev["q"] == q and ev["sq"] == 5])}
                    for q in qs if any(ev["q"] == q and ev["sq"] == 5 for ev in early_g)],
        "holdings": [{**row(ev), "entry": _r(ev["o0"]), "entry_date": ev["day0"], "last": _r(ev["e_last"]), "ret": _r(ev["e_now"]),
                      "xs": _r(ev["e_now"] - ev["e_mkt"]) if ev.get("e_mkt") is not None else None, "held": ev["e_held"]}
                     for ev in sorted(early_hold, key=lambda e: e["day0"], reverse=True)],
    }
    return {
        "asof": today.isoformat(), "generated_at": now.isoformat(timespec="minutes"), "next_session": nxt.isoformat(), "source": QUANTPEDIA,
        "morning": morning, "early": early,
        "rules": {"hold": HOLD, "min_price": MIN_PRICE, "cost": COST, "excluded": len(excluded)},
        "cuts": current_cuts, "seasons": seasons,
        "summary": {"trades": len(traded), "closed": len(done), "open": len(hold), **{f"c_{k}": v for k, v in stats(done).items()}},
        "compare": compare, "grid": grid, "cohorts": cohorts, "curve": curve,
        "holdings": [row(ev) for ev in hold], "to_buy": [row(ev) for ev in to_buy],
        "watch": sorted(watch, key=lambda x: -(x["sue"] or 0))[:60],
        "trades": [row(ev) for ev in sorted(traded, key=lambda e: e["entry_date"], reverse=True)],
    }
