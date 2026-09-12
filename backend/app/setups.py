"""
Base / breakout scanner behind Market view.

Runs inside scripts/update_charts.py after the daily candles are rebuilt,
and writes chart_data/setups.json. Everything here is computed from the
split-adjusted daily candles; nothing is entered by hand.

The method (a VCP-style reading, kept deliberately simple and explicit)
------------------------------------------------------------------------
Base      On a given day, look back up to 130 sessions (~6 months). The
          highest high in that window is the *pivot* -- the ceiling. It is a
          base when that high is at least 15 sessions (3 weeks) old, the
          pullback from it is no deeper than 35%, and the stock ran up at
          least 25% into it (a base follows an advance, not a slide).
Breakout  The first close above the pivot, on at least 1.4x the 50-day
          average volume.
Exit      Stopped: the low trades 8% below the pivot.
          Trailed: a close below the 50-day average, 3+ sessions after the
          breakout.
Stage     Fresh breakouts   broke out within the last 5 sessions, not exited
          Climbing          broke out earlier, not exited
          Forming           in a base, closing within 15% under its pivot, in
                            an uptrend (above the 200-day, 50-day above 200-day)
                            -- including a 2nd base built after a breakout
          Played out        broke out this year and has since exited

Measures on every card
          RS rating       1-99 percentile of 3/6/9/12-month weighted return
                          against every liquid NSE stock (IBD-style weights)
          Now vs pivot    last close against the pivot, %
          Tightening      10-day ATR / 50-day ATR (below 1 = contracting)
          Volume dry-up   10-day average volume / 50-day average volume
          Up/down volume  (up-day volume - down-day volume) / total, 50 days
          From 52w high   % below the 52-week high
Flags     squat       traded above the pivot but closed back under it
                      (last 10 sessions)
          failed poke closed above the pivot without breakout volume, then
                      fell back under it (last 10 sessions)
"""
from __future__ import annotations

from datetime import date

LOOKBACK = 130
MIN_AGE = 15
MAX_DEPTH = 0.35
MIN_ADVANCE = 1.25
BREAKOUT_VOL = 1.4
STOP = 0.92
FRESH = 5
FORMING_BAND = 0.85


# ------------------------------------------------------------ primitives
class _RMQ:
    """O(1) range max/min over a fixed list (sparse table)."""

    def __init__(self, xs: list[float], fn):
        self.fn, self.t = fn, [list(xs)]
        j = 1
        while (1 << j) <= len(xs):
            prev, half = self.t[-1], 1 << (j - 1)
            self.t.append([fn(prev[i], prev[i + half]) for i in range(len(xs) - (1 << j) + 1)])
            j += 1

    def q(self, lo: int, hi: int) -> float:
        """inclusive range [lo, hi]"""
        k = (hi - lo + 1).bit_length() - 1
        return self.fn(self.t[k][lo], self.t[k][hi - (1 << k) + 1])


def _sma(xs: list[float], n: int) -> list[float | None]:
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


class Series:
    def __init__(self, rows: list[list]):
        self.rows = rows
        self.d = [r[0] for r in rows]
        self.o = [r[1] for r in rows]
        self.h = [r[2] for r in rows]
        self.l = [r[3] for r in rows]
        self.c = [r[4] for r in rows]
        self.v = [r[5] for r in rows]
        self.n = len(rows)
        self.sma10, self.sma50, self.sma200 = _sma(self.c, 10), _sma(self.c, 50), _sma(self.c, 200)
        self.vol50, self.vol10 = _sma(self.v, 50), _sma(self.v, 10)
        tr = [self.h[0] - self.l[0]] + [max(self.h[i], self.c[i - 1]) - min(self.l[i], self.c[i - 1]) for i in range(1, self.n)]
        self.atr10, self.atr50 = _sma(tr, 10), _sma(tr, 50)
        self.hmax = _RMQ(self.h, max)
        self.lmin = _RMQ(self.l, min)
        # index of the latest highest high: needed for the pivot's position
        self._hidx = _RMQ([(h, i) for i, h in enumerate(self.h)], max)

    def base(self, end: int) -> dict | None:
        """The base whose window ends on session `end` (inclusive), if any."""
        start = max(0, end - LOOKBACK + 1)
        if end - start < MIN_AGE:
            return None
        pivot, p = self._hidx.q(start, end)
        age = end - p
        if age < MIN_AGE or p >= end:
            return None
        low = self.lmin.q(p + 1, end)
        depth = 1 - low / pivot
        if depth > MAX_DEPTH or depth <= 0:
            return None
        run_from = self.lmin.q(max(0, p - 126), p)
        if pivot < run_from * MIN_ADVANCE:
            return None
        return {"pivot": pivot, "peak": p, "start": p, "end": end, "low": low, "depth": depth, "age": age}

    def uptrend(self, i: int) -> bool:
        s50, s200 = self.sma50[i], self.sma200[i]
        if s200 is not None and s50 is not None:
            return self.c[i] > s200 and s50 > s200
        if s50 is not None:     # under a year of history: judge on the 50-day
            return self.c[i] > s50 * 0.95
        return False

    def is_breakout(self, i: int, b: dict | None) -> bool:
        if not b or i < 1 or self.vol50[i - 1] in (None, 0):
            return False
        return self.c[i] > b["pivot"] >= self.c[i - 1] and self.v[i] >= BREAKOUT_VOL * self.vol50[i - 1]


# ------------------------------------------------------------ strength
def rs_score(closes: list[float]) -> float | None:
    """IBD-style weighted performance: 40% last quarter, 20% each earlier one."""
    n = len(closes)
    if n < 127:
        return None
    last = closes[-1]
    r = lambda k: last / closes[-1 - k] - 1 if n > k and closes[-1 - k] > 0 else None
    q1, q2, q3, q4 = r(63), r(126), r(189), r(250)
    q3 = q3 if q3 is not None else q2
    q4 = q4 if q4 is not None else q3
    return 0.4 * q1 + 0.2 * q2 + 0.2 * q3 + 0.2 * q4


def percentile_ranker(scores: list[float]):
    ordered = sorted(scores)
    import bisect

    def rank(x: float | None) -> int | None:
        if x is None or not ordered:
            return None
        return max(1, min(99, round(bisect.bisect_left(ordered, x) / len(ordered) * 99)))
    return rank


# ------------------------------------------------------------ one stock
def _walk(s: Series) -> list[dict]:
    """Every breakout in the series, with its exit if it had one."""
    events, pos = [], None
    for i in range(60, s.n):
        if pos:
            if s.l[i] <= pos["pivot"] * STOP:
                pos["exit"] = {"i": i, "reason": "stopped", "price": round(pos["pivot"] * STOP, 2)}
                pos = None
            elif i - pos["i"] >= 3 and s.sma50[i] is not None and s.c[i] < s.sma50[i]:
                pos["exit"] = {"i": i, "reason": "trailed", "price": s.c[i]}
                pos = None
        b = s.base(i - 1)
        if b and (pos is None or b["peak"] > pos["i"]) and s.is_breakout(i, b):
            ev = {"i": i, "pivot": b["pivot"], "base": b, "vol_x": round(s.v[i] / s.vol50[i - 1], 1),
                  "chain": (pos["chain"] + 1) if pos else 1, "first": pos["first"] if pos else None}
            if pos:
                pos["exit"] = {"i": i, "reason": "rolled", "price": s.c[i]}   # into the next base's breakout
            ev["first"] = ev["first"] or ev
            events.append(ev)
            pos = ev
    return events


def stage_at(s: Series, events: list[dict], t: int) -> dict:
    active = None
    for ev in events:
        if ev["i"] <= t and ("exit" not in ev or ev["exit"]["i"] > t):
            active = ev
    if active:
        since = t - active["i"]
        if since < FRESH:
            return {"stage": "fresh", "event": active, "since": since}
        b = s.base(t)
        if b and b["peak"] > active["i"] and s.c[t] >= b["pivot"] * FORMING_BAND:
            return {"stage": "forming", "event": active, "base": b, "second": True, "since": since}
        return {"stage": "climbing", "event": active, "since": since}
    b = s.base(t)
    if b and b["pivot"] * FORMING_BAND <= s.c[t] <= b["pivot"] * 1.02 and s.uptrend(t):
        return {"stage": "forming", "base": b}
    year = s.d[t][:4]
    done = [ev for ev in events if "exit" in ev and ev["exit"]["i"] <= t and s.d[ev["i"]][:4] == year and ev["exit"]["reason"] != "rolled"]
    if done:
        return {"stage": "played", "event": done[-1]}
    return {"stage": None}


def analyze(rows: list[list], rs_rank) -> dict | None:
    if len(rows) < 80:
        return None
    s = Series(rows)
    t = s.n - 1
    events = _walk(s)
    now, before = stage_at(s, events, t), stage_at(s, events, t - 1)

    ev, base = now.get("event"), now.get("base")
    pivot = base["pivot"] if base else (ev["pivot"] if ev else None)
    rs = rs_rank(rs_score(s.c))
    up = sum(s.v[i] for i in range(max(1, s.n - 50), s.n) if s.c[i] > s.c[i - 1])
    dn = sum(s.v[i] for i in range(max(1, s.n - 50), s.n) if s.c[i] < s.c[i - 1])
    hi52 = s.hmax.q(max(0, s.n - 250), t)

    flags = []
    if pivot:
        recent = range(max(1, s.n - 10), s.n)
        if any(s.h[i] > pivot >= s.c[i] for i in recent):
            flags.append("squat")
        for i in recent:
            vol_ok = s.vol50[i - 1] and s.v[i] >= BREAKOUT_VOL * s.vol50[i - 1]
            if s.c[i] > pivot and not vol_ok and any(s.c[j] <= pivot for j in range(i + 1, s.n)):
                flags.append("failed poke")
                break

    out = {
        "stage": now["stage"],
        "prev_stage": before["stage"],
        "asof": s.d[t],
        "last": {"o": s.o[t], "h": s.h[t], "l": s.l[t], "c": s.c[t], "v": s.v[t],
                 "chg_pct": round((s.c[t] / s.c[t - 1] - 1) * 100, 2)},
        "rs": rs,
        "pivot": pivot,
        "now_vs_pivot": round((s.c[t] / pivot - 1) * 100, 1) if pivot else None,
        "atr_ratio": round(s.atr10[t] / s.atr50[t], 2) if s.atr50[t] else None,
        "vol_dryup": round(s.vol10[t] / s.vol50[t], 2) if s.vol50[t] else None,
        "updown": round((up - dn) / (up + dn), 2) if up + dn else None,
        "from_high": round((1 - s.c[t] / hi52) * 100, 1) if hi52 else None,
        "high52_today": s.h[t] >= hi52 and s.n > 200,
        "low52_today": s.l[t] <= s.lmin.q(max(0, s.n - 250), t) and s.n > 200,
        "mood": "Powering up" if s.n > 5 and s.c[t] >= s.c[t - 5] else "Cooling off",
        "flags": flags,
    }
    if base:
        out["base"] = {"start": s.d[base["start"]], "end": s.d[base["end"]], "low": base["low"],
                       "weeks": _weeks(base["age"]), "depth_pct": round(base["depth"] * 100, 1)}
        out["base_age_w"] = out["base"]["weeks"]
    if ev:
        b = ev["base"]
        out["breakout"] = {"date": s.d[ev["i"]], "pivot": ev["pivot"], "vol_x": ev["vol_x"],
                           "gain_pct": round((s.c[t] / ev["pivot"] - 1) * 100, 1), "chain": ev["chain"],
                           "sessions": t - ev["i"],
                           "base": {"start": s.d[b["start"]], "end": s.d[b["end"]], "low": b["low"],
                                    "weeks": _weeks(b["age"])}}
        if "exit" in ev and ev["exit"]["i"] <= t:
            x = ev["exit"]
            out["breakout"]["exit"] = {"date": s.d[x["i"]], "reason": x["reason"],
                                       "result_pct": round((x["price"] / ev["pivot"] - 1) * 100, 1)}
        if now.get("second"):
            first = ev["first"]
            out["note"] = (f"Building its {_ordinal(ev['chain'] + 1)} base — up "
                           f"{round((s.c[t] / first['pivot'] - 1) * 100, 1)}% since the 1st breakout "
                           f"({_short_date(s.d[first['i']])})")
    out["feed"] = _feed(out, s, t)
    return out


def _weeks(sessions: int) -> float | int:
    w = round(sessions / 5, 1)
    return int(w) if w == int(w) else w


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _short_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b')}"


def _inr(x: float) -> str:
    return f"₹{x:,.2f}".rstrip("0").rstrip(".") if x < 1000 else f"₹{x:,.1f}".rstrip("0").rstrip(".")


def _feed(a: dict, s: Series, t: int) -> list[dict]:
    """What changed for this stock on the latest session, as sentences."""
    items, bo, c = [], a.get("breakout"), s.c[t]
    if a["stage"] == "fresh" and bo and bo["sessions"] == 0:
        items.append(("breakout", 1, f"broke out. Closed {_inr(c)} — {abs(a['now_vs_pivot'])}% above the "
                                     f"{_inr(bo['pivot'])} pivot, on {bo['vol_x']}× its usual trading."))
    if a["stage"] == "climbing" and a["prev_stage"] == "fresh":
        items.append(("climbing", 2, "five sessions since its breakout — moved to Climbing."))
    if a["stage"] == "played" and a["prev_stage"] in ("fresh", "climbing", "forming") and bo and bo.get("exit", {}).get("date") == s.d[t]:
        x = bo["exit"]
        why = "fell 8% under its pivot — stopped out" if x["reason"] == "stopped" else "closed under its 50-day average — trailed out"
        items.append(("exit", 3, f"{why} ({'+' if x['result_pct'] >= 0 else ''}{x['result_pct']}% from the breakout)."))
    if a["stage"] == "forming" and a["prev_stage"] != "forming" and a.get("base"):
        items.append(("forming", 4, f"started setting up — a {a['base']['weeks']}-week base, "
                                    f"{abs(a['now_vs_pivot'])}% under the {_inr(a['pivot'])} pivot."))
    if a["high52_today"] and not items:
        items.append(("high52", 5, f"made a new 52-week high at {_inr(s.h[t])}."))
    if a["low52_today"] and not items:
        items.append(("low52", 6, f"made a new 52-week low at {_inr(s.l[t])}."))
    return [{"kind": k, "order": o, "text": txt} for k, o, txt in items]


# ------------------------------------------------------------ market-wide
def market_breakout_today(rows: list[list]) -> dict | None:
    """Did this (non-board) stock break out on its latest session? Cheap check."""
    if len(rows) < 80:
        return None
    s = Series(rows)
    t = s.n - 1
    b = s.base(t - 1)
    if not (s.is_breakout(t, b) and s.uptrend(t)):
        return None
    return {"close": s.c[t], "chg_pct": round((s.c[t] / s.c[t - 1] - 1) * 100, 2), "pivot": b["pivot"],
            "above_pct": round((s.c[t] / b["pivot"] - 1) * 100, 1), "vol_x": round(s.v[t] / s.vol50[t - 1], 1)}
