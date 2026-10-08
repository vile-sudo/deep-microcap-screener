"""Sector Strength: how broadly each tracked sector's stocks are moving over 1, 2, 3 and 5 sessions.

A breadth monitor, not a signal. Backtested before building (scripts/backtest_sector_strength.py,
Oct 2026): breadth gave at most a small, inconsistent tilt in forward returns, so the page shows what is
happening -- "35 of 50 up over 3 days" -- and never labels anything a buy.

Members: every listed company (NSE and BSE, main board and SME) in the sector's NSE basic industries,
as screener.in lists them (scripts/run_sector_members_in.py -> data/sector_strength/members.json, weekly).
Prices: the exchange bhavcopies scripts/update_charts.py already keeps (.bhav_cache), so this costs no
extra download. Returns chain each session's close / previous close as the exchange prints them, so a
split or bonus inside the window doesn't show up as a crash.

Benchmark: the Nifty 500 -- the index's own return over each window (NSE's daily index close file,
ind_close_all_DDMMYYYY.csv, cached next to the bhavcopies) and the breadth of its 500 constituents, so a
sector's "70% up" can be read against the market's.

Sector indices (indices.json, GET /api/sector-strength/indices): each sector's daily level over the last
INDEX_SESSIONS sessions, built from its own members -- equal weight (the average day's move of its liquid
stocks) and market-cap weight (weights from today's market cap, carried back with each stock's price) --
next to the Nifty 500's own closes, for the TradingView-style comparison chart. Members are today's list
throughout, so a company that left the sector is missing from its past (survivorship).

write(days) is called at the end of scripts/update_charts.py and writes data/sector_strength/latest.json
(per-stock returns, the frontend aggregates them for its filters) and history.json (each sector's
breadth for the last HISTORY_SESSIONS sessions, for the sparklines). GET /api/sector-strength serves both.
"""
from __future__ import annotations

import csv
import io
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BACKEND_DIR / "data" / "sector_strength"
MEMBERS_FILE = OUT_DIR / "members.json"
LATEST_FILE = OUT_DIR / "latest.json"
HISTORY_FILE = OUT_DIR / "history.json"
WINDOWS = (1, 2, 3, 5)
HISTORY_SESSIONS = 60
INDEX_SESSIONS = 760          # about three years of trading days for the comparison chart
INDEX_FILE_BUDGET = 400       # Nifty 500 daily files fetched per run; older ones fill in on the next runs
DAY_CLIP = 25.0               # a single day's move counted at most +/-25% (a bad print can't swing a sector)
INDICES_FILE = OUT_DIR / "indices.json"
LIQUID_TURNOVER_CR = 1.0      # 20-session average turnover, Rs crore
LIQUID_SESSIONS = 18          # traded on at least this many of the last 20
UP_THRESHOLD = 0.01           # "up more than 1%"

# Themes: hand-picked groups of NSE basic industries, shown first. NSE's industry classification as
# screener.in lays it out: /market/<macro sector>/<sector>/<industry>/<basic industry>/
THEMES = [
    {"slug": "defence", "name": "Defence & Aerospace", "industries": [
        ("IN07/IN0702/IN070201/IN070201001", "Aerospace & Defense"),
        ("IN07/IN0702/IN070204/IN070204006", "Ship Building & Allied Services")]},
    {"slug": "specialty-chemicals", "name": "Specialty Chemicals", "industries": [
        ("IN01/IN0101/IN010101/IN010101002", "Specialty Chemicals")]},
    {"slug": "capital-markets", "name": "Capital Markets", "industries": [
        ("IN05/IN0501/IN050103/IN050103001", "Asset Management Company"),
        ("IN05/IN0501/IN050103/IN050103006", "Stockbroking & Allied"),
        ("IN05/IN0501/IN050103/IN050103005", "Exchange and Data Platform"),
        ("IN05/IN0501/IN050103/IN050103002", "Depositories, Clearing Houses and Other Intermediaries"),
        ("IN05/IN0501/IN050103/IN050103003", "Financial Products Distributor"),
        ("IN05/IN0501/IN050103/IN050103004", "Ratings"),
        ("IN05/IN0501/IN050103/IN050103007", "Other Capital Market related Services")]},
    {"slug": "electrical-equipment", "name": "Electrical Equipment", "industries": [
        ("IN07/IN0702/IN070203/IN070203001", "Heavy Electrical Equipment"),
        ("IN07/IN0702/IN070203/IN070203002", "Other Electrical Equipment"),
        ("IN07/IN0702/IN070205/IN070205003", "Cables - Electricals")]},
    {"slug": "machinery", "name": "Machinery & Equipment", "industries": [
        ("IN07/IN0702/IN070204/IN070204007", "Industrial Machinery"),
        ("IN07/IN0702/IN070204/IN070204008", "Industrial Products"),
        ("IN07/IN0702/IN070205/IN070205017", "Compressors, Pumps & Diesel Engines"),
        ("IN07/IN0702/IN070205/IN070205016", "Abrasives & Bearings"),
        ("IN07/IN0702/IN070204/IN070204005", "Railway Wagons")]},
    {"slug": "jewellery", "name": "Gems & Jewellery", "industries": [
        ("IN02/IN0202/IN020201/IN020201005", "Gems, Jewellery And Watches")],
     # small and newly listed jewellers are often missing from screener's industry listing: any traded company
     # whose name says so is checked against its own page (scripts/run_sector_members_in.py)
     "keywords": r"JEWEL|JWEL|\bGEMS?\b|BULLION|ORNAMENT|DIAMOND|\bGOLD\b|\bSWARN"},
]

# Every NSE sector (the 22 the exchange's own indices use), each made of all its basic industries as listed
# in data/sector_strength/nse_industries.json (written by scripts/run_sector_members_in.py from screener.in's
# industry index, so a basic industry NSE adds later is picked up by itself).
NSE_SECTORS = {
    "IN0101": ("chemicals", "Chemicals"), "IN0102": ("construction-materials", "Construction Materials"),
    "IN0103": ("metals-mining", "Metals & Mining"), "IN0104": ("forest-materials", "Forest Materials"),
    "IN0201": ("auto", "Automobile & Auto Components"), "IN0202": ("consumer-durables", "Consumer Durables"),
    "IN0203": ("textiles", "Textiles"), "IN0204": ("media", "Media, Entertainment & Publication"),
    "IN0205": ("realty", "Realty"), "IN0206": ("consumer-services", "Consumer Services"),
    "IN0301": ("oil-gas", "Oil, Gas & Consumable Fuels"), "IN0401": ("fmcg", "Fast Moving Consumer Goods"),
    "IN0501": ("financial-services", "Financial Services"), "IN0601": ("healthcare", "Healthcare"),
    "IN0701": ("construction", "Construction"), "IN0702": ("capital-goods", "Capital Goods"),
    "IN0801": ("it", "Information Technology"), "IN0901": ("services", "Services"),
    "IN1001": ("telecom", "Telecommunication"), "IN1101": ("power", "Power"),
    "IN1102": ("utilities", "Utilities"), "IN1201": ("diversified", "Diversified"),
}
INDUSTRIES_FILE = OUT_DIR / "nse_industries.json"


def sectors() -> list[dict]:
    """Themes first, then every NSE sector that has basic industries on file."""
    try:
        inds = json.loads(INDUSTRIES_FILE.read_text(encoding="utf-8")).get("industries") or []
    except (OSError, ValueError):
        inds = []
    by_sector: dict[str, list] = {}
    for path, label in inds:
        parts = path.split("/")
        if len(parts) == 4:
            by_sector.setdefault(parts[1], []).append((path, label))
    out = [{**t, "group": "theme"} for t in THEMES]
    for code, (slug, name) in NSE_SECTORS.items():
        if by_sector.get(code):
            out.append({"slug": slug, "name": name, "group": "nse", "code": code,
                        "industries": sorted(by_sector[code], key=lambda x: x[1])})
    return out


SECTORS = sectors()


INDEX_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d}.csv"
INDEX_NAME = "Nifty 500"
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def index_closes(dates: list, cache: Path | None, budget: int = 10_000) -> dict:
    """{date: Nifty 500 close} for the given sessions; each day's file fetched once and cached
    (newest first, at most `budget` downloads a run)."""
    import requests
    out = {}
    s = requests.Session()
    fetched = 0
    for d in sorted(dates, reverse=True):
        f = cache / f"IDX_{d.strftime('%Y%m%d')}.csv" if cache else None
        text = f.read_text(encoding="utf-8") if f and f.exists() else None
        if text is None:
            if fetched >= budget:
                continue
            fetched += 1
            try:
                r = s.get(INDEX_URL.format(d=d.strftime("%d%m%Y")), headers=BROWSER, timeout=30)
            except Exception:  # noqa: BLE001 - a missing day leaves a gap, never fails the run
                continue
            if r.status_code != 200 or not r.text.startswith("Index Name"):
                continue
            text = r.text
            if f:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8")
            time.sleep(0.3)
        for row in csv.DictReader(io.StringIO(text)):
            if (row.get("Index Name") or "").strip().lower() == INDEX_NAME.lower():
                try:
                    out[d] = float(row["Closing Index Value"])
                except (KeyError, ValueError):
                    pass
    return out


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _row(day_books, ex: str, key: str):
    nse, bse = day_books
    return (nse if ex == "NSE" else bse).get(key)


def _resolve(m: dict, latest_nse: dict, latest_bse: dict, by_isin_nse: dict, by_isin_bse: dict):
    """(exchange, bhavcopy key) for a member: its NSE line when it trades there, else its BSE line."""
    code = str(m["code"])
    if not code.isdigit() and code in latest_nse:
        return "NSE", code
    if code.isdigit() and code in latest_bse:
        isin = latest_bse[code][9]
        if isin and isin in by_isin_nse:
            return "NSE", by_isin_nse[isin]
        return "BSE", code
    if not code.isdigit() and code in by_isin_nse.values():
        return "NSE", code
    return None, None


ACTION_FACTORS = (1/2, 1/3, 2/3, 1/4, 3/4, 1/5, 2/5, 1/6, 1/8, 1/10, 1/20, 2, 5, 10)


def _move(prev: list, r: list) -> float | None:
    """One session's % move from the previous traded session's close, with splits and bonuses taken out.

    Close against close, not against the bhavcopy's printed previous close: that one isn't always
    restated on a split's ex-date (HAL's 1:2 in Sept 2023 printed the old close), and a special
    Saturday session missing from the files would otherwise drop that day's move. A corporate action is
    recognised the way app/charts.py's _adjust does: the exchange restating the previous close by a
    split/bonus ratio, or an overnight gap beyond -25%/+33% (circuit limits keep a normal day inside 20%)
    that fits a common ratio. A gap that fits none (a demerger) isn't counted at all."""
    pc = prev[6]
    if pc <= 0 or r[6] <= 0:
        return None
    gap = r[3] / pc if r[3] > 0 else 1.0
    if r[8] > 0 and abs(r[8] / pc - 1) > 0.02:
        ratio = r[8] / pc
        f = min(ACTION_FACTORS, key=lambda x: abs(ratio / x - 1))
        # a restated close counts only when the stock also opened there -- a rights issue restates the
        # close by an odd ratio (Camlin Fine, Sept 2024: 0.765) while the shares open where they were
        if abs(ratio / f - 1) <= 0.03 and abs(gap / ratio - 1) <= 0.15:
            return (r[6] / r[8] - 1) * 100
    if gap < 0.75 or gap > 1.33:
        seen = (r[4] + r[5] + r[6]) / 3 / pc
        f = min(ACTION_FACTORS, key=lambda x: abs(seen / x - 1))
        if abs(seen / f - 1) <= 0.12:
            return (r[6] / (pc * f) - 1) * 100
        # no split ratio fits: a jump up is a real move (an open offer, say); a drop is most likely a
        # demerger, whose value went to the new company's shares, so that day isn't counted
        return (r[6] / pc - 1) * 100 if gap > 1 else None
    return (r[6] / pc - 1) * 100


def _moves(days: list, ex: str, key: str, lo: int, hi: int) -> list:
    """Each session's move for indices lo..hi (None where the stock didn't trade), oldest first."""
    prev = None
    for i in range(lo - 1, max(-1, lo - 12), -1):     # the last traded session before the span
        r = _row((days[i][1], days[i][2]), ex, key)
        if r and r[7] > 0 and r[6] > 0:
            prev = r
            break
    out = []
    for i in range(lo, hi + 1):
        r = _row((days[i][1], days[i][2]), ex, key)
        if r and r[7] > 0 and r[6] > 0:
            out.append(_move(prev, r) if prev else None)
            prev = r
        else:
            out.append(None)
    return out


def _returns(days: list, ex: str, key: str, end: int) -> dict:
    """Compounded moves over each window ending at session index `end`."""
    mv = _moves(days, ex, key, end - max(WINDOWS) + 1, end)
    out = {}
    for w in WINDOWS:
        seg = [m for m in mv[-w:] if m is not None]
        prod = 1.0
        for m in seg:
            prod *= 1 + m / 100
        out[w] = (prod - 1) * 100 if seg else None
    return out


def _daily(days: list, ex: str, key: str, end: int) -> list:
    """Each of the last max(WINDOWS) sessions' own % change, oldest first (None: didn't trade that day)."""
    return [None if m is None else round(m, 2) for m in _moves(days, ex, key, end - max(WINDOWS) + 1, end)]


class _Prices:
    """Each stock's daily moves and turnover over the span the page needs, computed once per run and shared by
    every sector it belongs to (a company sits in a theme and in its NSE sector)."""

    def __init__(self, days: list, lo: int):
        self.days, self.lo, self.memo = days, lo, {}

    def get(self, ex: str, key: str):
        k = (ex, key)
        if k not in self.memo:
            days, lo = self.days, self.lo
            mv = _moves(days, ex, key, lo, len(days) - 1)
            close, tsum, csum = [], [0.0], [0]
            for i in range(lo, len(days)):
                r = _row((days[i][1], days[i][2]), ex, key)
                ok = r and r[6] > 0 and r[7] > 0
                close.append(r[6] if ok else None)
                tsum.append(tsum[-1] + (r[6] * r[7] / 1e7 if ok else 0.0))
                csum.append(csum[-1] + (1 if ok else 0))
            self.memo[k] = (mv, close, tsum, csum)
        return self.memo[k]

    def liquid_before(self, ex, key, i):
        """Average turnover (Rs cr) and sessions traded over the 20 sessions before index i."""
        _, _, tsum, csum = self.get(ex, key)
        j = i - self.lo
        a = max(0, j - 20)
        return (tsum[j] - tsum[a]) / 20, csum[j] - csum[a]

    def turnover_to(self, ex, key, i):
        """Average turnover and sessions traded over the 20 sessions ending at index i."""
        return self.liquid_before(ex, key, i + 1)

    def move(self, ex, key, i):
        return self.get(ex, key)[0][i - self.lo]

    def returns(self, ex, key, end):
        mv = self.get(ex, key)[0]
        j = end - self.lo
        out = {}
        for w in WINDOWS:
            seg = [m for m in mv[max(0, j - w + 1):j + 1] if m is not None]
            prod = 1.0
            for m in seg:
                prod *= 1 + m / 100
            out[w] = (prod - 1) * 100 if seg else None
        return out

    def daily(self, ex, key, end):
        mv = self.get(ex, key)[0]
        j = end - self.lo
        return [None if m is None else round(m, 2) for m in mv[j - max(WINDOWS) + 1:j + 1]]


def build_indices(days: list, groups: dict, cache: Path | None, px: "_Prices") -> dict:
    """Daily levels (base 100) per sector -- equal and market-cap weighted -- and the Nifty 500, last INDEX_SESSIONS."""
    first = max(px.lo + 21, len(days) - INDEX_SESSIONS)
    span = range(first, len(days))
    out = {"dates": [days[i][0].isoformat() for i in span], "sectors": {}}
    for slug, members in groups.items():
        ew, cw, lev_e, lev_c, count = [], [], 100.0, 100.0, []
        for i in span:
            num_e = n_e = num_c = den_c = 0.0
            for ex, key, mcap, last_close in members:
                m = px.move(ex, key, i)
                if m is None:
                    continue
                avg, traded = px.liquid_before(ex, key, i)
                if avg < LIQUID_TURNOVER_CR or traded < LIQUID_SESSIONS:
                    continue
                m = max(-DAY_CLIP, min(DAY_CLIP, m))
                num_e += m
                n_e += 1
                prev_close = px.get(ex, key)[1][i - 1 - px.lo]
                if mcap and last_close and prev_close:
                    w = mcap * prev_close / last_close        # yesterday's market cap, carried back by price
                    num_c += w * m
                    den_c += w
            if i > first:   # the first day is the base
                lev_e *= 1 + (num_e / n_e if n_e else 0) / 100
                lev_c *= 1 + (num_c / den_c if den_c else 0) / 100
            ew.append(round(lev_e, 2))
            cw.append(round(lev_c, 2))
            count.append(int(n_e))
        out["sectors"][slug] = {"ew": ew, "cw": cw, "n": count}
    idx = index_closes([days[i][0] for i in span], cache, INDEX_FILE_BUDGET)
    out["nifty500"] = [idx.get(days[i][0]) for i in span]
    return out


_ISIN_DAYS: dict = {}


def _listed(days: list, ex: str, key: str) -> str | None:
    """The first session the company traded on either exchange, when that's within the last year (a recent
    IPO), else None. Matched on ISIN, so a company moving from BSE to NSE isn't mistaken for a new listing."""
    lo = max(1, len(days) - 260)
    if id(days) not in _ISIN_DAYS:
        _ISIN_DAYS.clear()
        _ISIN_DAYS[id(days)] = [{r[9] for b in (days[i][1], days[i][2]) for r in b.values() if r[9]}
                                for i in range(lo - 1, len(days))]
    sets = _ISIN_DAYS[id(days)]
    r = _row((days[-1][1], days[-1][2]), ex, key)
    isin = r[9] if r else None
    if not isin:
        return None
    if isin in sets[0]:
        return None
    for j, s in enumerate(sets[1:], start=lo):
        if isin in s:
            return days[j][0].isoformat()
    return None


def _breadth(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "up": sum(v > 0 for v in vals), "down": sum(v < 0 for v in vals),
            "up1": sum(v > UP_THRESHOLD * 100 for v in vals), "median": round(statistics.median(vals), 2)}


def write(days: list, cache: Path | None = None) -> dict | None:
    """Compute the latest snapshot and the breadth history from the cached bhavcopies (oldest first)."""
    from .charts import drop_repeated_days
    days = drop_repeated_days(days)
    mfile = _load(MEMBERS_FILE, {})
    members = mfile.get("sectors") or {}
    if not members or len(days) < 25:
        print("sector strength: no member list or too little price history; skipped")
        return None
    last = len(days) - 1
    secs = sectors()
    px = _Prices(days, max(0, len(days) - INDEX_SESSIONS - 30))
    latest_nse, latest_bse = days[-1][1], days[-1][2]
    by_isin_nse = {r[9]: k for k, r in latest_nse.items() if r[9]}
    by_isin_bse = {r[9]: k for k, r in latest_bse.items() if r[9]}

    # the market yardstick: median return of every stock with Rs 1 cr+ average turnover
    def turnover(ex, key, end):
        return px.turnover_to(ex, key, end)

    def market_median(end):
        vals = {w: [] for w in WINDOWS}
        for ex, book in (("NSE", days[end][1]), ("BSE", days[end][2])):
            for key, r in book.items():
                if ex == "BSE" and r[9] and r[9] in by_isin_nse:
                    continue
                if not r[7] or r[6] * r[7] < 1e7 * 0.2:      # quick pre-filter before the 20-day average
                    continue
                avg, s = turnover(ex, key, end)
                if avg < LIQUID_TURNOVER_CR or s < LIQUID_SESSIONS:
                    continue
                rets = px.returns(ex, key, end)
                for w in WINDOWS:
                    if rets.get(w) is not None:
                        vals[w].append(rets[w])
        return {w: round(statistics.median(v), 2) if v else None for w, v in vals.items()}

    market = market_median(last)
    first = max(0, last - HISTORY_SESSIONS - max(WINDOWS) + 1)
    idx = index_closes([d[0] for d in days[first:]], cache)

    def idx_ret(end, w):
        a, b = idx.get(days[end][0]), idx.get(days[end - w][0]) if end - w >= 0 else None
        return round((a / b - 1) * 100, 2) if a and b else None
    n500 = [k for k in (mfile.get("nifty500") or []) if k in latest_nse]
    sectors_out, resolved, weighted = [], {}, {}
    for sec in secs:
        rows, seen_keys = [], set()
        for m in members.get(sec["slug"], []):
            ex, key = _resolve(m, latest_nse, latest_bse, by_isin_nse, by_isin_bse)
            if ex and (ex, key) in seen_keys:
                continue                       # the same company under its BSE code and its NSE symbol
            if ex:
                seen_keys.add((ex, key))
            if not ex:
                rows.append({**m, "exchange": None, "status": "no recent trade"})
                continue
            r = _row((latest_nse, latest_bse), ex, key)
            avg, s = turnover(ex, key, last)
            rets = px.returns(ex, key, last)
            resolved.setdefault(sec["slug"], []).append((ex, key))
            weighted.setdefault(sec["slug"], []).append((ex, key, m.get("mcap_cr"), r[6]))
            rows.append({**m, "exchange": ex, "symbol": r[1], "full_name": r[2] or m.get("name"), "close": r[6],
                         "d": px.daily(ex, key, last), "listed": _listed(days, ex, key), "traded_today": r[7] > 0, "turnover_cr": round(avg, 2), "sessions20": s,
                         "liquid": avg >= LIQUID_TURNOVER_CR and s >= LIQUID_SESSIONS,
                         **{f"r{w}": None if rets.get(w) is None else round(rets[w], 2) for w in WINDOWS}})
        sectors_out.append({"slug": sec["slug"], "name": sec["name"], "group": sec.get("group", "theme"), "code": sec.get("code"),
                            "industries": [i[1] for i in sec["industries"]], "stocks": rows})

    def group_breadth(keys, end):
        vals = {w: [] for w in WINDOWS}
        for ex, key in keys:
            rets = px.returns(ex, key, end)
            for w in WINDOWS:
                if rets.get(w) is not None:
                    vals[w].append(rets[w])
        return {w: _breadth(vals[w]) for w in WINDOWS}

    n500_keys = [("NSE", k) for k in n500]
    b500 = group_breadth(n500_keys, last)
    sessions = [days[i][0].isoformat() for i in range(last - max(WINDOWS) + 1, last + 1)]
    daily500 = {w: [] for w in range(len(sessions))}
    for k in n500:
        for j, v in enumerate(px.daily("NSE", k, last)):
            if v is not None:
                daily500[j].append(v)
    benchmark = {"name": INDEX_NAME, "close": idx.get(days[last][0]), "constituents": len(n500),
                 "d": [idx_ret(i, 1) for i in range(last - max(WINDOWS) + 1, last + 1)],
                 "d_breadth": [_breadth(daily500[j]) for j in range(len(sessions))],
                 **{f"r{w}": idx_ret(last, w) for w in WINDOWS},
                 "breadth": {str(w): b500[w] for w in WINDOWS}}

    # breadth history for the sparklines: every member that traded, each of the last sessions
    hist = {"dates": [], "market": {str(w): [] for w in WINDOWS}, "nifty500": {str(w): [] for w in WINDOWS}, "sectors": {}}
    for end in range(max(0, last - HISTORY_SESSIONS + 1), last + 1):
        hist["dates"].append(days[end][0].isoformat())
        b = group_breadth(n500_keys, end)
        for w in WINDOWS:
            hist["nifty500"][str(w)].append([round(100 * b[w]["up"] / b[w]["n"], 1) if b[w]["n"] else None, idx_ret(end, w)])
        for sec in secs:
            h = hist["sectors"].setdefault(sec["slug"], {str(w): [] for w in WINDOWS})
            vals = {w: [] for w in WINDOWS}
            for ex, key in resolved.get(sec["slug"], []):
                rets = px.returns(ex, key, end)
                for w in WINDOWS:
                    if rets.get(w) is not None:
                        vals[w].append(rets[w])
            for w in WINDOWS:
                b = _breadth(vals[w])
                h[str(w)].append([round(100 * b["up"] / b["n"], 1), b["median"]] if b["n"] else None)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    latest = {"generated_at": now, "asof": days[-1][0].isoformat(), "windows": list(WINDOWS), "sessions": sessions,
              "market": {str(w): v for w, v in market.items()}, "benchmark": benchmark, "liquid_turnover_cr": LIQUID_TURNOVER_CR,
              "sme": mfile.get("sme") or [], "sectors": sectors_out}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_FILE.write_text(json.dumps(latest, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    HISTORY_FILE.write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")
    try:
        ind = build_indices(days, weighted, cache, px)
        ind["names"] = {sec["slug"]: sec["name"] for sec in secs}
        ind["groups"] = {sec["slug"]: sec.get("group", "theme") for sec in secs}
        INDICES_FILE.write_text(json.dumps(ind, separators=(",", ":")), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - the chart is extra; the snapshot above already stands
        print(f"sector strength: indices failed ({e})")
    n = sum(len(s["stocks"]) for s in sectors_out)
    print(f"sector strength: {n} stocks across {len(sectors_out)} sectors as of {latest['asof']}; "
          f"Nifty 500 3D {benchmark['r3']}%, {b500[3].get('up', 0)}/{b500[3].get('n', 0)} constituents up")
    return latest
