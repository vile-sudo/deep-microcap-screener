"""
Base / breakout scanner behind Market view.

Runs inside scripts/update_charts.py after the daily candles are rebuilt,
and writes chart_data/setups.json. Everything here is computed from the
split-adjusted daily candles; nothing is entered by hand.

Four screens share one engine -- the same breakout, exit and stage rules --
and differ only in what counts as a base:

VCP screen
  Base      Look back up to 130 sessions (~6 months). The highest high in that
            window is the *pivot*. It is a base when that high is at least 15
            sessions (3 weeks) old, the pullback from it is no deeper than 35%,
            and the stock ran up at least 25% into it.
  Forming   also needs an uptrend: above the 200-day, 50-day above 200-day.

Blue sky screen (at least a year of trading history)
  Base      A VCP base whose pivot is also the highest price in all the data
            held for the stock (NSE/BSE files back to about January 2020) --
            no one who bought higher is waiting to sell at break-even. A
            company listed before 2020 may have traded higher before then.
  Forming   also needs an uptrend, as VCP.

Multi-year breakouts screen
  Base      The highest high of up to 1,250 sessions (~5 years) is the pivot.
            It is a base when that high is at least 250 sessions (a year) old
            -- nothing has closed the gap since -- and the stock has held no
            deeper than 50% under it.
  Forming   also needs an uptrend, as VCP.

IPO base screen (companies listed within the last two years)
  Listing   The first session the stock appears in the exchange's daily
            files, when that is after the start of the two-year window.
  Base      The highest high since listing is the pivot. It is a base when
            that high is at least 15 sessions old and the stock has held no
            deeper than 50% under it (IPO bases run deeper than later ones).
  Volume    "Usual volume" skips the first 5 sessions after listing, whose
            volumes are listing-day noise.

Shared by both
  Breakout  The first close above the pivot, on at least 1.4x usual volume
            (50-day average; since listing for younger IPOs).
  Exit      Stopped: the low trades 8% below the pivot.
            Trailed: a close below the 50-day average (20-day for IPOs with
            under 50 sessions), 3+ sessions after the breakout.
  Stage     Fresh breakouts  broke out within the last 5 sessions, not exited
            Climbing         broke out earlier, not exited
            Forming          in a base within 15% under its pivot (a new base
                             after an earlier breakout counts too)
            Played out       broke out this year and has since exited
  Verge     Forming and within 5% of the pivot.

Measures on every card
  RS rating       1-99 percentile of 3/6/9/12-month weighted return against
                  every liquid NSE stock (IBD-style weights)
  Now vs pivot    last close against the pivot, %
  Tightening      10-day ATR / 50-day ATR (below 1 = contracting)
  Volume dry-up   10-day average volume / 50-day average volume
  Up/down volume  (up-day volume - down-day volume) / total, 50 days
  From 52w high   % below the 52-week high
Flags
  squat           traded above the pivot but closed back under it (10 sessions)
  failed poke     closed above the pivot without breakout volume, then fell
                  back under it (10 sessions)
"""
from __future__ import annotations

import bisect
from datetime import date

LOOKBACK = 130
MIN_AGE = 15
MAX_DEPTH = 0.35
IPO_MAX_DEPTH = 0.50
MIN_ADVANCE = 1.25
BREAKOUT_VOL = 1.4
STOP = 0.92
FRESH = 5
FORMING_BAND = 0.85
VERGE = 0.95
IPO_SKIP = 5          # listing-day sessions left out of "usual volume"
BLUESKY_MIN_HISTORY = 250   # a year of sessions, so a new listing isn't trivially at its high
MULTI_LOOKBACK = 1250       # ~5 years
MULTI_MIN_AGE = 250         # the ceiling has held for a year or more
MULTI_MAX_DEPTH = 0.50


# ------------------------------------------------------------ primitives
class _RMQ:
    """O(1) range max/min over a fixed list (sparse table)."""

    def __init__(self, xs: list, fn):
        self.fn, self.t = fn, [list(xs)]
        j = 1
        while (1 << j) <= len(xs):
            prev, half = self.t[-1], 1 << (j - 1)
            self.t.append([fn(prev[i], prev[i + half]) for i in range(len(xs) - (1 << j) + 1)])
            j += 1

    def q(self, lo: int, hi: int):
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
        self.sma10, self.sma20 = _sma(self.c, 10), _sma(self.c, 20)
        self.sma50, self.sma200 = _sma(self.c, 50), _sma(self.c, 200)
        self.vol50, self.vol10 = _sma(self.v, 50), _sma(self.v, 10)
        tr = [self.h[0] - self.l[0]] + [max(self.h[i], self.c[i - 1]) - min(self.l[i], self.c[i - 1]) for i in range(1, self.n)]
        self.atr10, self.atr50 = _sma(tr, 10), _sma(tr, 50)
        self.hmax = _RMQ(self.h, max)
        self.lmin = _RMQ(self.l, min)
        self._hidx = _RMQ([(h, i) for i, h in enumerate(self.h)], max)   # latest highest high wins ties
        self._vsum = [0.0]
        for x in self.v:
            self._vsum.append(self._vsum[-1] + x)

    # -------- bases
    def base(self, end: int) -> dict | None:
        """VCP base whose window ends on session `end` (inclusive), if any."""
        start = max(0, end - LOOKBACK + 1)
        if end - start < MIN_AGE:
            return None
        pivot, p = self._hidx.q(start, end)
        age = end - p
        if age < MIN_AGE:
            return None
        low = self.lmin.q(p + 1, end)
        depth = 1 - low / pivot
        if depth > MAX_DEPTH or depth <= 0:
            return None
        if pivot < self.lmin.q(max(0, p - 126), p) * MIN_ADVANCE:
            return None
        return {"pivot": pivot, "peak": p, "start": p, "end": end, "low": low, "depth": depth, "age": age}

    def ipo_base(self, end: int) -> dict | None:
        """IPO base: the post-listing high held for 3+ weeks, at most 50% deep."""
        if end < MIN_AGE:
            return None
        pivot, p = self._hidx.q(0, end)
        age = end - p
        if age < MIN_AGE:
            return None
        low = self.lmin.q(p + 1, end)
        depth = 1 - low / pivot
        if depth > IPO_MAX_DEPTH or depth <= 0:
            return None
        return {"pivot": pivot, "peak": p, "start": p, "end": end, "low": low, "depth": depth, "age": age}

    def bluesky_base(self, end: int) -> dict | None:
        """A VCP base whose pivot is also the highest high in all the data up to `end`."""
        if end + 1 < BLUESKY_MIN_HISTORY:
            return None
        b = self.base(end)
        return b if b and b["pivot"] >= self.hmax.q(0, end) else None

    def multiyear_base(self, end: int) -> dict | None:
        """Multi-year base: the highest high of up to ~5 years, a year or more old, at most 50% deep."""
        start = max(0, end - MULTI_LOOKBACK + 1)
        if end - start < MULTI_MIN_AGE:
            return None
        pivot, p = self._hidx.q(start, end)
        age = end - p
        if age < MULTI_MIN_AGE:
            return None
        low = self.lmin.q(p + 1, end)
        depth = 1 - low / pivot
        if depth > MULTI_MAX_DEPTH or depth <= 0:
            return None
        return {"pivot": pivot, "peak": p, "start": p, "end": end, "low": low, "depth": depth, "age": age}

    # -------- volume and trend
    def usual_vol(self, i: int, ipo: bool = False) -> float | None:
        """Average volume of the sessions before i: 50-day, or since listing for young IPOs."""
        if not ipo:
            return self.vol50[i - 1] if i >= 1 else None
        lo = max(IPO_SKIP, i - 50)
        if i - lo < 10:
            return None
        return (self._vsum[i] - self._vsum[lo]) / (i - lo)

    def trail(self, i: int, ipo: bool = False) -> float | None:
        return self.sma50[i] if self.sma50[i] is not None or not ipo else self.sma20[i]

    def uptrend(self, i: int) -> bool:
        s50, s200 = self.sma50[i], self.sma200[i]
        if s200 is not None and s50 is not None:
            return self.c[i] > s200 and s50 > s200
        if s50 is not None:     # under a year of history: judge on the 50-day
            return self.c[i] > s50 * 0.95
        return False


SCREENS = ("vcp", "bluesky", "multiyear", "ipo")


class Screen:
    """What differs between the screens: what counts as a base, and (IPO only)
    usual volume, the trailing average and whether forming needs an uptrend."""

    def __init__(self, s: Series, kind: str):
        self.s, self.kind, self.ipo = s, kind, kind == "ipo"
        self._base = {"vcp": s.base, "ipo": s.ipo_base, "bluesky": s.bluesky_base, "multiyear": s.multiyear_base}[kind]

    def base(self, end: int):
        return self._base(end)

    def avg(self, i: int):
        return self.s.usual_vol(i, self.ipo)

    def trail(self, i: int):
        return self.s.trail(i, self.ipo)

    def forming_ok(self, t: int) -> bool:
        return True if self.ipo else self.s.uptrend(t)

    def is_breakout(self, i: int, b: dict | None) -> bool:
        avg = self.avg(i) if b else None
        return bool(avg) and self.s.c[i] > b["pivot"] >= self.s.c[i - 1] and self.s.v[i] >= BREAKOUT_VOL * avg


# ------------------------------------------------------------ strength
def rs_score(closes: list[float]) -> float | None:
    """IBD-style weighted performance: 40% last quarter, 20% each earlier one."""
    n = len(closes)
    if n < 127:
        return None
    last = closes[-1]
    r = lambda k: last / closes[-1 - k] - 1 if n > k and closes[-1 - k] > 0 else None
    q1, q2, q3, q4 = r(63), r(126), r(189), r(250)
    if q1 is None or q2 is None:
        # a non-positive close at the 3- or 6-month mark (a bad $0 print on
        # a thin OTC ticker -- confirmed live, it crashed the first US
        # backfill run) leaves no return to weight; no rating, not a crash
        return None
    q3 = q3 if q3 is not None else q2
    q4 = q4 if q4 is not None else q3
    return 0.4 * q1 + 0.2 * q2 + 0.2 * q3 + 0.2 * q4


def percentile_ranker(scores: list[float]):
    ordered = sorted(scores)

    def rank(x: float | None) -> int | None:
        if x is None or not ordered:
            return None
        return max(1, min(99, round(bisect.bisect_left(ordered, x) / len(ordered) * 99)))
    return rank


# ------------------------------------------------------------ engine
def _walk(sc: Screen) -> list[dict]:
    """Every breakout under this screen's bases, with its exit if it had one."""
    s, events, pos = sc.s, [], None
    for i in range(20 if sc.ipo else 60, s.n):
        if pos:
            tr = sc.trail(i)
            if s.l[i] <= pos["pivot"] * STOP:
                pos["exit"] = {"i": i, "reason": "stopped", "price": round(pos["pivot"] * STOP, 2)}
                pos = None
            elif i - pos["i"] >= 3 and tr is not None and s.c[i] < tr:
                pos["exit"] = {"i": i, "reason": "trailed", "price": s.c[i]}
                pos = None
        b = sc.base(i - 1)
        if b and (pos is None or b["peak"] > pos["i"]) and sc.is_breakout(i, b):
            ev = {"i": i, "pivot": b["pivot"], "base": b, "vol_x": round(s.v[i] / sc.avg(i), 1),
                  "chain": (pos["chain"] + 1) if pos else 1, "first": pos["first"] if pos else None}
            if pos:
                pos["exit"] = {"i": i, "reason": "rolled", "price": s.c[i]}   # into the next base's breakout
            ev["first"] = ev["first"] or ev
            events.append(ev)
            pos = ev
    return events


def stage_at(sc: Screen, events: list[dict], t: int) -> dict:
    s = sc.s
    active = None
    for ev in events:
        if ev["i"] <= t and ("exit" not in ev or ev["exit"]["i"] > t):
            active = ev
    if active:
        since = t - active["i"]
        if since < FRESH:
            return {"stage": "fresh", "event": active, "since": since}
        b = sc.base(t)
        if b and b["peak"] > active["i"] and s.c[t] >= b["pivot"] * FORMING_BAND:
            return {"stage": "forming", "event": active, "base": b, "second": True, "since": since}
        return {"stage": "climbing", "event": active, "since": since}
    b = sc.base(t)
    if b and b["pivot"] * FORMING_BAND <= s.c[t] <= b["pivot"] * 1.02 and sc.forming_ok(t):
        return {"stage": "forming", "base": b}
    year = s.d[t][:4]
    done = [ev for ev in events if "exit" in ev and ev["exit"]["i"] <= t
            and s.d[ev["i"]][:4] == year and ev["exit"]["reason"] != "rolled"]
    if done:
        return {"stage": "played", "event": done[-1]}
    return {"stage": None}


def _screen_view(sc: Screen) -> dict:
    """Stage and pivot-relative fields for one screen, at the latest session."""
    s, t = sc.s, sc.s.n - 1
    events = _walk(sc)
    now, before = stage_at(sc, events, t), stage_at(sc, events, t - 1)
    ev, base = now.get("event"), now.get("base")
    pivot = base["pivot"] if base else (ev["pivot"] if ev else None)

    flags = []
    if pivot:
        recent = range(max(1, s.n - 10), s.n)
        if any(s.h[i] > pivot >= s.c[i] for i in recent):
            flags.append("squat")
        for i in recent:
            avg = sc.avg(i)
            if s.c[i] > pivot and not (avg and s.v[i] >= BREAKOUT_VOL * avg) and any(s.c[j] <= pivot for j in range(i + 1, s.n)):
                flags.append("failed poke")
                break

    out = {
        "stage": now["stage"],
        "prev_stage": before["stage"],
        "pivot": pivot,
        "now_vs_pivot": round((s.c[t] / pivot - 1) * 100, 1) if pivot else None,
        "flags": flags,
    }
    out["verge"] = bool(now["stage"] == "forming" and pivot and s.c[t] >= pivot * VERGE)
    if base:
        out["base"] = {"start": s.d[base["start"]], "end": s.d[base["end"]], "low": base["low"],
                       "weeks": _weeks(base["age"]), "depth_pct": round(base["depth"] * 100, 1)}
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
    # every base's breakout, newest first -- the "X-ray: every base it ever
    # built" view, and (for the IPO screen) the "broke out in the last
    # week / month / 3 / 6 / 12 months" view
    out["breakouts"] = [_event_summary(s, e, t) for e in reversed(events)]
    return out


BASE_FLAG_WINDOW = 10   # sessions right after a breakout that squat/failed-poke are judged over


def _event_flags(s: Series, ev: dict, end: int) -> list[str]:
    """squat / failed poke for one historical breakout, judged over the
    BASE_FLAG_WINDOW sessions right after it broke out (not "now" -- there is
    no "now" for a base that finished years ago)."""
    pivot = ev["pivot"]
    window = range(ev["i"], min(end + 1, ev["i"] + BASE_FLAG_WINDOW))
    flags = []
    if any(s.h[i] > pivot >= s.c[i] for i in window):
        flags.append("squat")
    for i in window:
        avg = s.vol50[i - 1] if i >= 1 else None
        if s.c[i] > pivot and not (avg and s.v[i] >= BREAKOUT_VOL * avg) and any(s.c[j] <= pivot for j in range(i + 1, min(end + 1, i + BASE_FLAG_WINDOW))):
            flags.append("failed poke")
            break
    return flags


def _event_summary(s: Series, ev: dict, t: int) -> dict:
    b, i = ev["base"], ev["i"]
    end = ev["exit"]["i"] if "exit" in ev and ev["exit"]["i"] <= t else t
    peak = s.hmax.q(i, end)
    out = {"date": s.d[i], "sessions_ago": t - i, "pivot": ev["pivot"], "vol_x": ev["vol_x"], "chain": ev["chain"],
           "close": s.c[i], "gain_pct": round((s.c[t] / ev["pivot"] - 1) * 100, 1),
           "best_pct": round((peak / ev["pivot"] - 1) * 100, 1),
           "base": {"start": s.d[b["start"]], "end": s.d[b["end"]], "low": b["low"], "weeks": _weeks(b["age"])},
           "flags": _event_flags(s, ev, end), "status": "active"}
    if "exit" in ev and ev["exit"]["i"] <= t:
        x = ev["exit"]
        out["status"] = x["reason"]                       # stopped | trailed | rolled (into a later breakout)
        out["exit"] = {"date": s.d[x["i"]], "result_pct": round((x["price"] / ev["pivot"] - 1) * 100, 1)}
    return out


def analyze(rows: list[list], rs_rank, listed: str | None = None, long_bases: bool = False,
            fmt_money=None) -> dict | None:
    """Everything Market view shows for one company.

    Top level: the shared measures plus the VCP screen. `ipo`: the IPO base
    screen, present only when the company listed within the data window.
    `bluesky` / `multiyear`: only with long_bases, and a year of history --
    both need the full price history to mean anything, which the board's
    series have and Screen any Chart's universe (UNIVERSE_SESSIONS) does not.

    fmt_money formats the prices named in feed sentences -- ₹ by default
    (_inr); the US board passes _usd. None rather than a literal default of
    _inr since _inr is defined later in this file than analyze() is."""
    fmt_money = fmt_money or _inr
    s = Series(rows)
    t = s.n - 1
    if s.n < 20 or (s.n < 80 and not listed):
        return None
    up = sum(s.v[i] for i in range(max(1, s.n - 50), s.n) if s.c[i] > s.c[i - 1])
    dn = sum(s.v[i] for i in range(max(1, s.n - 50), s.n) if s.c[i] < s.c[i - 1])
    hi52 = s.hmax.q(max(0, s.n - 250), t)
    long_enough = s.n > 200
    out = {
        "asof": s.d[t],
        "last": {"o": s.o[t], "h": s.h[t], "l": s.l[t], "c": s.c[t], "v": s.v[t],
                 "chg_pct": round((s.c[t] / s.c[t - 1] - 1) * 100, 2)},
        "rs": rs_rank(rs_score(s.c)),
        "atr_ratio": round(s.atr10[t] / s.atr50[t], 2) if s.atr50[t] else None,
        "vol_dryup": round(s.vol10[t] / s.vol50[t], 2) if s.vol50[t] else None,
        "updown": round((up - dn) / (up + dn), 2) if up + dn else None,
        "from_high": round((1 - s.c[t] / hi52) * 100, 1) if hi52 else None,
        "high52_today": s.h[t] >= hi52 and long_enough,
        "low52_today": s.l[t] <= s.lmin.q(max(0, s.n - 250), t) and long_enough,
        "mood": "Powering up" if s.n > 5 and s.c[t] >= s.c[t - 5] else "Cooling off",
    }
    if s.n >= 80:
        vcp = _screen_view(Screen(s, "vcp"))
    else:
        vcp = {"stage": None, "prev_stage": None, "pivot": None, "now_vs_pivot": None, "flags": [], "verge": False}
    out.update(vcp)
    feed = _feed(vcp, out, s, t, "vcp", fmt_money)
    if long_bases and s.n >= BLUESKY_MIN_HISTORY:
        for kind in ("bluesky", "multiyear"):
            out[kind] = _screen_view(Screen(s, kind))
            feed += _feed(out[kind], out, s, t, kind, fmt_money)
    if listed:
        ipo = _screen_view(Screen(s, "ipo"))
        ipo["listed"] = listed
        ipo["sessions_listed"] = s.n
        ipo["listing_high"] = s.hmax.q(0, t)
        ipo["from_listing_high"] = round((1 - s.c[t] / ipo["listing_high"]) * 100, 1)
        out["ipo"] = ipo
        feed += _feed(ipo, out, s, t, "ipo")
    out["feed"] = feed
    return out


_VIEW_KEYS = ("stage", "pivot", "now_vs_pivot", "flags", "verge", "base", "breakout", "note")
_IPO_KEYS = ("listed", "sessions_listed", "listing_high", "from_listing_high")
_SHARED_KEYS = ("asof", "last", "rs", "atr_ratio", "vol_dryup", "updown", "from_high", "mood")


def compact(a: dict | None) -> dict | None:
    """analyze()'s output trimmed to what Market view's cards and list need,
    for the whole-market file: only the latest breakout of each screen's
    history (the IPO "broke out in the last ..." windows and the CSV read no
    further back), and a screen left out entirely when it has nothing to show.
    None when no screen has the stock in a setup."""
    if not a:
        return None

    def _present(x) -> bool:
        # not "if v.get(k) not in (None, [], False)" -- 0 == False in Python,
        # so that tuple-membership test silently drops a genuine 0 (a stock
        # exactly at its pivot, e.g.), not just truly-absent values
        return x is not None and x != [] and x is not False

    def view(v: dict, extra=()) -> dict | None:
        bos = v.get("breakouts") or []
        if not v.get("stage") and not bos:
            return None
        out = {k: v[k] for k in _VIEW_KEYS + extra if _present(v.get(k))}
        out["stage"] = v.get("stage")
        if bos:
            out["breakouts"] = bos[:1]
        return out

    screens = {"vcp": view(a)}
    for kind in ("bluesky", "multiyear"):
        screens[kind] = view(a[kind]) if a.get(kind) else None
    screens["ipo"] = view(a["ipo"], _IPO_KEYS) if a.get("ipo") else None
    if not any(screens.values()):
        return None
    out = {k: a[k] for k in _SHARED_KEYS if k in a}
    out.update(screens["vcp"] or {"stage": None})
    for kind in ("bluesky", "multiyear", "ipo"):
        if screens[kind]:
            out[kind] = screens[kind]
    return out


# ------------------------------------------------------------ text
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


def _usd(x: float) -> str:
    return f"${x:,.2f}".rstrip("0").rstrip(".") if x < 1000 else f"${x:,.1f}".rstrip("0").rstrip(".")


# (what it broke out of, what the pivot is called, what the base is called)
_WORDS = {"vcp": ("its base", "pivot", "base"),
          "ipo": ("its IPO base", "post-listing high", "IPO base"),
          "bluesky": ("its base at an all-time high", "all-time high", "base at an all-time high"),
          "multiyear": ("its multi-year base", "multi-year high", "multi-year base")}


def _feed(v: dict, common: dict, s: Series, t: int, screen: str, fmt_money=None) -> list[dict]:
    """What changed for this stock on the latest session, as sentences."""
    fmt_money = fmt_money or _inr
    items, bo, c = [], v.get("breakout"), s.c[t]
    what, pivot_name, noun = _WORDS[screen]
    if v["stage"] == "fresh" and bo and bo["sessions"] == 0:
        items.append(("breakout", 1, f"broke out of {what}. Closed {fmt_money(c)} — {abs(v['now_vs_pivot'])}% above the "
                                     f"{fmt_money(bo['pivot'])} {pivot_name}, on {bo['vol_x']}× its usual trading."))
    if v["stage"] == "climbing" and v["prev_stage"] == "fresh":
        items.append(("climbing", 2, "five sessions since its breakout — moved to Climbing."))
    if v["stage"] == "played" and v["prev_stage"] in ("fresh", "climbing", "forming") and bo and bo.get("exit", {}).get("date") == s.d[t]:
        x = bo["exit"]
        why = "fell 8% under its pivot — stopped out" if x["reason"] == "stopped" else "closed under its moving average — trailed out"
        items.append(("exit", 3, f"{why} ({'+' if x['result_pct'] >= 0 else ''}{x['result_pct']}% from the breakout)."))
    if v["stage"] == "forming" and v.get("verge") and not (v["prev_stage"] == "forming" and s.c[t - 1] >= v["pivot"] * VERGE):
        items.append(("verge", 4, f"is on the verge — {abs(v['now_vs_pivot'])}% under the {fmt_money(v['pivot'])} {pivot_name}"
                                  f" after a {v['base']['weeks']}-week {noun}." if v.get("base") else ""))
    elif v["stage"] == "forming" and v["prev_stage"] != "forming" and v.get("base"):
        items.append(("forming", 5, f"started setting up — a {v['base']['weeks']}-week {noun}, "
                                    f"{abs(v['now_vs_pivot'])}% under the {fmt_money(v['pivot'])} {pivot_name}."))
    if screen == "vcp" and not items:
        if common["high52_today"]:
            items.append(("high52", 6, f"made a new 52-week high at {fmt_money(s.h[t])}."))
        elif common["low52_today"]:
            items.append(("low52", 7, f"made a new 52-week low at {fmt_money(s.l[t])}."))
    return [{"screen": screen, "kind": k, "order": o, "text": txt} for k, o, txt in items if txt]


# ------------------------------------------------------------ market-wide
def market_breakout_today(rows: list[list], listed: str | None = None) -> dict:
    """Did this stock break out on its latest session, under any screen? Cheap check.

    `rows` is the full history. VCP and IPO base only ever look back a year,
    so they run on that; Blue sky and Multi-year need everything, and that
    bigger build is skipped unless the close is above every high of the
    last LOOKBACK sessions -- which a breakout under either one needs anyway."""
    out = {}
    t = len(rows) - 1
    if t < 21:
        return out
    year = Series(rows if listed else rows[-250:])
    wanted = [("vcp", year)] if year.n >= 80 else []
    if listed:
        wanted.append(("ipo", year))
    if t >= BLUESKY_MIN_HISTORY and rows[t][4] > max(r[2] for r in rows[t - LOOKBACK:t]):
        full = Series(rows)
        wanted += [("bluesky", full), ("multiyear", full)]
    for key, s in wanted:
        n = s.n - 1
        sc = Screen(s, key)
        b = sc.base(n - 1)
        if sc.is_breakout(n, b) and sc.forming_ok(n):
            out[key] = {"close": s.c[n], "chg_pct": round((s.c[n] / s.c[n - 1] - 1) * 100, 2), "pivot": b["pivot"],
                        "above_pct": round((s.c[n] / b["pivot"] - 1) * 100, 1), "vol_x": round(s.v[n] / sc.avg(n), 1)}
    return out
