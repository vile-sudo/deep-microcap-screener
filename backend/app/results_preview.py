"""Results Preview: what each company's next quarterly result is likely to show, how the stock is likely to
react, why, and how often this has been right.

It learns from the whole market -- every actively traded NSE/BSE company with quarterly results on file
(data/results_universe from scripts/run_results_universe.py, plus the board's own data/results_in) -- and
predicts, for every company with results due in the next three weeks (data/results_calendar_in):
  - p_profit  the chance net profit is above the same quarter last year
  - p_accel   the chance year-on-year sales growth is faster than last quarter's
  - p_react   the chance the stock beats the market from the close before the result to the close after
and the reasons, from what was public before the result:
  results    last quarter's growth vs its trailing year (bounce-back / fade), the base quarter, margins, profit
  guidance   management's fiscal-year revenue guidance (data/guidance_in)
  calls      what management said about the NEXT quarter on its latest earnings call, read by Claude
             (data/call_outlook, scripts/run_call_outlook.py): revenue and margin direction, capex phase,
             input costs
  capacity   commissioning / capacity-addition filings 2-12 months before (the Deals history)
  orders     order wins valued from the filing PDFs: inflow in the last 6 months, and the revenue those
             orders should deliver THIS quarter (each spread evenly over its execution period)
  costs      the sector's main input commodities, year-on-year (Yahoo Finance monthly futures)
  peers      companies in the same NSE sector that already reported this quarter: how many grew faster
  chart      what the market is already pricing in: 20- and 5-day move against the market, distance from the
             1-month and 52-week highs, volume build-up
The models are gradient-boosted trees (scikit-learn), refitted every night. The backtest is walk-forward:
each quarter is predicted by models fitted only on quarters reported before it. It also measures the drift
AFTER results (does a stock that beat keep going?), which needs no prediction at all.

Every live prediction is logged (data/results_preview/predictions.json) and graded once the result is out.
A research preview, not a buy signal -- the page shows the backtest beside every call.
"""
from __future__ import annotations

import gzip
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from . import charts

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA = BACKEND_DIR / "data"
OUT_DIR = DATA / "results_preview"
LATEST = OUT_DIR / "latest.json"
PRED_LOG = OUT_DIR / "predictions.json"
COMMOD_FILE = OUT_DIR / "commodities.json"
ALERTS_FILE = OUT_DIR / "alerts.json"
ALL_FILE = OUT_DIR / "all.json"
NAMES_FILE = OUT_DIR / "names.json"
POST_LOG = OUT_DIR / "overnight_reads.json"
BT_ROWS_FILE = OUT_DIR / "backtest_rows.json"   # every stock behind the backtest tables (loaded on demand)     # every overnight read, graded once the next session is in            # full company names (NSE's equity list), refreshed weekly                # every company's next-result call -- the "Check a stock" box          # merged into the India Alerts bell by routers/alerts.load()
PEAD_FILE = OUT_DIR / "pead.json"                  # Earnings drift (PEAD) strategy -- app/pead.py
READ_FILE = DATA / "results_read" / "latest.json"   # Claude's read of each results filing (scripts/run_results_read.py)
CLAUDE_SINCE = "2026-07-01"   # Claude's training data ends Jun 2026: only later filings are an honest test of its read
UPCOMING_DAYS = 21
MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
NAN = float("nan")

# the sector's input commodities: +1 when a rising price squeezes margins, -1 when it helps (producers, exporters)
COMMODITIES = {"CL=F": "crude oil", "NG=F": "natural gas", "HG=F": "copper", "ALI=F": "aluminium", "HRC=F": "steel (HRC)",
               "CT=F": "cotton", "ZL=F": "edible oil", "ZW=F": "wheat", "SB=F": "sugar", "GC=F": "gold", "INR=X": "USD/INR"}
SECTOR_INPUTS = {
    "chemicals": {"CL=F": 1, "NG=F": 1}, "construction-materials": {"NG=F": 1, "CL=F": 1},
    "metals-mining": {"HG=F": -1, "ALI=F": -1, "HRC=F": -1}, "forest-materials": {"CL=F": 1},
    "auto": {"HRC=F": 1, "ALI=F": 1, "HG=F": 1}, "consumer-durables": {"HG=F": 1, "ALI=F": 1, "HRC=F": 1, "GC=F": 1},
    "textiles": {"CT=F": 1}, "fmcg": {"ZL=F": 1, "ZW=F": 1, "SB=F": 1}, "oil-gas": {"CL=F": -1},
    "capital-goods": {"HRC=F": 1, "HG=F": 1}, "construction": {"HRC=F": 1, "CL=F": 1},
    "power": {"NG=F": 1}, "utilities": {"NG=F": 1}, "healthcare": {"INR=X": -1}, "it": {"INR=X": -1},
}
FEATURES = ["g1", "trail", "gap", "base", "opm_ch", "opm_last", "pg1", "size", "guide_gap",
            "call_rev", "call_margin", "call_capex", "call_input", "cap_n", "order_inflow_pct", "order_rev_pct",
            "input_cost", "peer_n", "peer_accel", "peer_g", "ret20", "ret5", "hi1m", "hi52", "volr",
            "hist_beat", "hist_avg", "hist_n", "sector"]
DIR = {"up": 1, "flat": 0, "down": -1}
CAPEX = {"building": 0, "commissioning": 1, "completed_ramping": 2, "completed_steady": 3}
INPUT = {"rising": 1, "stable": 0, "easing": -1}


# ------------------------------------------------------------------ helpers
def qend(label: str) -> date:
    m, y = label.split()
    m, y = MON[m], int(y)
    return date(y, m, 30 if m in (6, 9) else 31)


def next_label(label: str, lag: int = 4) -> str:
    """The period after `label`: three months on for a quarterly reporter, six for a half-yearly one."""
    m, y = label.split()
    n = MON[m] - 1 + (3 if lag == 4 else 6)
    return f"{[k for k, v in MON.items() if v == n % 12 + 1][0]} {int(y) + n // 12}"


def lag_of(P: list[str]) -> int:
    """How many periods back "the same period last year" is: 4 for a quarterly reporter, 2 for a half-yearly one
    (most SME companies report every six months), 0 when the periods are irregular."""
    try:
        if not P or any(p.split()[0] not in ("Mar", "Jun", "Sep", "Dec") for p in P):
            return 0
        gaps = [(qend(b) - qend(a)).days for a, b in zip(P, P[1:])]
        if gaps and all(88 <= g <= 94 for g in gaps):
            return 4
        if gaps and all(178 <= g <= 186 for g in gaps):
            return 2
    except (KeyError, ValueError, IndexError):
        pass
    return 0


def standard(P: list[str]) -> bool:
    return lag_of(P) > 0


def _standard_quarterly(P: list[str]) -> bool:
    """Quarters ending Mar/Jun/Sep/Dec, one after another -- "the same quarter last year" is 4 back only then."""
    try:
        return bool(P) and all(p.split()[0] in ("Mar", "Jun", "Sep", "Dec") for p in P) and \
            all(88 <= (qend(b) - qend(a)).days <= 94 for a, b in zip(P, P[1:]))
    except (KeyError, ValueError, IndexError):
        return False


def _yoy(a, b):
    if a is None or b is None or b <= 0:
        return None
    return (a / b - 1) * 100


def _clip(v, lo=-100.0, hi=150.0):
    return NAN if v is None else max(lo, min(hi, v))


def _load(path: Path, default):
    try:
        if path.suffix == ".gz":
            return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, EOFError):
        return default


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


# ------------------------------------------------------------------ commodities
def commodities(refresh: bool = True) -> dict:
    """{ticker: {"YYYY-MM": close}} monthly, refreshed from Yahoo when older than a day."""
    cur = _load(COMMOD_FILE, {})
    if not refresh or (cur.get("at") or "") >= (datetime.now(timezone.utc) - timedelta(days=1)).isoformat():
        return cur.get("series") or {}
    series = dict(cur.get("series") or {})
    for t in COMMODITIES:
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}", params={"range": "10y", "interval": "1mo"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            res = r.json()["chart"]["result"][0]
            closes = res["indicators"]["quote"][0]["close"]
            series[t] = {datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m"): round(c, 4)
                         for ts, c in zip(res["timestamp"], closes) if c}
        except Exception:  # noqa: BLE001 - keep what we had for this one
            continue
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    COMMOD_FILE.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "series": series},
                                      separators=(",", ":")), encoding="utf-8")
    return series


def _qavg(s: dict, qe: date) -> float | None:
    ms = [(qe.replace(day=1) - timedelta(days=31 * k)).strftime("%Y-%m") for k in range(3)]
    v = [s[m] for m in ms if m in s]
    return sum(v) / len(v) if v else None


def input_cost(sector: str | None, qe: date, com: dict) -> tuple[float, list]:
    """The sector's input-cost change: quarter-average vs the same quarter a year before, signed (see SECTOR_INPUTS)."""
    m = SECTOR_INPUTS.get(sector or "")
    if not m:
        return NAN, []
    parts, notes = [], []
    for t, sign in m.items():
        s = com.get(t) or {}
        a, b = _qavg(s, qe), _qavg(s, qe.replace(year=qe.year - 1))
        if a and b:
            ch = (a / b - 1) * 100
            parts.append(sign * ch)
            notes.append((COMMODITIES[t], round(ch, 1), sign))
    return (sum(parts) / len(parts) if parts else NAN), notes


def company_names() -> dict:
    """{NSE symbol: full company name} from NSE's own equity list (EQUITY_L.csv), plus the full names the
    Sector Strength classification already holds (SME and newer listings), cached for a week."""
    import csv
    import io
    cur = _load(NAMES_FILE, {})
    if (cur.get("at") or "") >= (datetime.now(timezone.utc) - timedelta(days=7)).isoformat() and cur.get("names"):
        return cur["names"]
    names = dict(cur.get("names") or {})
    for c in (_load(DATA / "sector_strength" / "members.json", {}).get("classified") or {}).values():
        if c.get("code") and c.get("name"):
            names.setdefault(str(c["code"]).upper(), c["name"])
    try:
        r = requests.get("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"}, timeout=40)
        for row in csv.DictReader(io.StringIO(r.text)):
            sym, nm = (row.get("SYMBOL") or "").strip().upper(), (row.get("NAME OF COMPANY") or "").strip()
            if sym and nm:
                names[sym] = nm
    except Exception:  # noqa: BLE001 - keep last week's list
        pass
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    NAMES_FILE.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "names": names},
                                     separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return names


def bse_forthcoming() -> dict:
    """{"NSE:SYMBOL" / "BSE:CODE": "YYYY-MM-DD"} from BSE's forthcoming-results list (board meetings to approve
    results, mainboard and SME) -- many companies notify BSE before (or instead of) NSE."""
    try:
        r = requests.get("https://api.bseindia.com/BseIndiaAPI/api/Corpforthresults/w", timeout=40,
                         headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com",
                                  "Accept": "application/json, text/plain, */*"})
        rows = r.json()
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for x in rows if isinstance(rows, list) else []:
        try:
            d = datetime.strptime(str(x.get("meeting_date")).strip(), "%d %b %Y").date().isoformat()
        except ValueError:
            continue
        if x.get("scrip_Code"):
            out[f"BSE:{x['scrip_Code']}"] = d
        if x.get("short_name"):
            out[f"NSE:{str(x['short_name']).upper()}"] = d
    return out


# ------------------------------------------------------------------ the market's companies
class Market:
    """Every company with results on file, keyed "NSE:SYMBOL" / "BSE:CODE", with what the features need."""

    def __init__(self):
        raw = _load(DATA / "companies_raw.json", [])
        board_results = _load(DATA / "results_in" / "latest.json", {}).get("companies") or {}
        self.guidance = _load(DATA / "guidance_in" / "latest.json", {}).get("companies") or {}
        self.calls = _load(DATA / "call_outlook" / "latest.json", {})
        self.ent: dict[str, dict] = {}
        for c in raw:
            code = str(c.get("code") or "")
            q = (board_results.get(code) or {}).get("quarters")
            if not code or not q:
                continue
            nse, bse = str(c.get("nse_code") or "").upper(), str(c.get("bse_code") or "")
            key = f"NSE:{nse}" if nse and not nse.isdigit() else f"BSE:{bse or code}"
            self.ent[key] = {"q": q, "code": code, "exchange": key[:3], "symbol": nse or bse, "name": c.get("name") or code}
        for key, v in (_load(DATA / "results_universe" / "latest.json.gz", {}).get("companies") or {}).items():
            if key in self.ent and v.get("quarters") and (v["quarters"].get("periods") or [""])[-1:] != (self.ent[key]["q"].get("periods") or [""])[-1:] \
                    and standard(v["quarters"].get("periods") or []) and qend(v["quarters"]["periods"][-1]) > qend(self.ent[key]["q"]["periods"][-1]):
                self.ent[key]["q"] = v["quarters"]      # a board company that just reported: the evening fetch is newer
            if key not in self.ent and v.get("quarters"):
                self.ent[key] = {"q": v["quarters"], "code": None, "exchange": v.get("exchange") or key[:3],
                                 "symbol": v.get("symbol") or key[4:], "name": v.get("name") or v.get("symbol") or key[4:]}
        # NSE sector of each company (Sector Strength's NSE classification)
        self.sector = {}
        for sec in _load(DATA / "sector_strength" / "latest.json", {}).get("sectors") or []:
            if sec.get("group") != "nse":
                continue
            for s in sec.get("stocks") or []:
                ex = s.get("exchange")
                if ex:
                    k = f"{ex}:{s.get('symbol') if ex == 'NSE' else s.get('code')}"
                    self.sector[k] = sec["slug"]
                    if s.get("full_name") and k in self.ent and not self.ent[k]["code"]:
                        self.ent[k]["name"] = s["full_name"]
        self.sectors = sorted(set(self.sector.values()))
        names = company_names()
        for key, e in self.ent.items():
            full = names.get(e["symbol"].upper()) if key.startswith("NSE:") else None
            short = e["name"]                                  # the exchange's short name, kept searchable
            if full and not e.get("code"):
                e["name"] = full
            e["alias"] = " ".join(x for x in dict.fromkeys([e["name"], full or "", short, e["symbol"]]) if x)
        # filings from the Deals history: capacity, valued orders, result dates (NSE symbols)
        self.cap, self.orders, self.res = defaultdict(list), defaultdict(list), defaultdict(list)
        try:
            from . import disclosures_archive as A
            path = A.archive_path()
        except Exception:  # noqa: BLE001
            path = None
        if path:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
            try:
                for sym, d, s in con.execute("SELECT symbol, date, summary FROM ann WHERE kind='capacity or plant' OR category IN "
                                             "('Commencement of commercial production/operations','Capacity addition')"):
                    self.cap[sym].append((d, s))
                for sym, d, v, m in con.execute("SELECT symbol, date, order_cr, months FROM orders WHERE not_order=0 AND order_cr IS NOT NULL"):
                    self.orders[sym].append((d, v, m))
                for sym, d, at in con.execute("SELECT symbol, date, filed_at FROM ann WHERE kind='results' AND category IN "
                                              "('Financial Result Updates','Outcome of Board Meeting')"):
                    self.res[sym].append(at or f"{d}T00:00:00")
            finally:
                con.close()
        self.com = commodities()
        self._px = {}

    def result_filed(self, key: str, qe: date) -> str | None:
        """When the quarter's results first reached the exchange (date and time): the earliest results filing
        (board-meeting outcome or results update) in the 75 days after the quarter ended."""
        if not key.startswith("NSE:"):
            return None
        lo, hi = (qe + timedelta(days=1)).isoformat(), (qe + timedelta(days=76)).isoformat()
        return min((at for at in self.res.get(key[4:], []) if lo <= at[:10] <= hi), default=None)

    def result_date(self, key: str, qe: date) -> str | None:
        at = self.result_filed(key, qe)
        return at[:10] if at else None

    def prices(self, key: str) -> list:
        """[(date, close, volume)] split-adjusted -- the board's own chart, else Screen any Chart's."""
        if key not in self._px:
            e = self.ent.get(key) or {}
            s = charts.load_series(e["code"]) if e.get("code") else None
            if not s:
                try:
                    from . import universe
                    s = universe.load(charts.universe_key(key[:3], key[4:]))
                except Exception:  # noqa: BLE001
                    s = None
            self._px[key] = [(r[0], r[4], r[5] if len(r) > 5 else None, r[1]) for r in (s or {}).get("rows") or [] if r[4]]
        return self._px[key]

    def guided(self, code: str | None, qe: date):
        fy = qe.year + (1 if qe.month > 3 else 0)
        best = None
        for s in (self.guidance.get(code or "") or {}).get("statements", []):
            if s.get("metric") != "revenue_growth" or (s.get("horizon") or {}).get("fy") != fy or s.get("call", "9") >= qe.isoformat()[:7]:
                continue
            v = ((s.get("low") or 0) + (s.get("high") or s.get("low") or 0)) / 2
            if best is None or s["call"] > best[0]:
                best = (s["call"], v, s.get("quote") or "")
        return best

    def call_before(self, code: str | None, qe: date):
        """The latest call held before this quarter ended, and at most ~5 months before (it spoke about this quarter)."""
        calls = self.calls.get(code or "") or {}
        lo = (qe - timedelta(days=150)).isoformat()[:7]
        ym = max((y for y in calls if lo <= y < qe.isoformat()[:7]), default=None)
        return (ym, calls[ym]) if ym else (None, None)


# ------------------------------------------------------------------ market index (equal weight, board stocks)
class Index:
    def __init__(self, mk: Market):
        daily = defaultdict(list)
        for key, e in mk.ent.items():
            if not e.get("code"):
                continue
            rows = mk.prices(key)
            for i in range(1, len(rows)):
                daily[rows[i][0]].append(rows[i][1] / rows[i - 1][1] - 1)
        self.lvl, acc = {}, 1.0
        for d in sorted(d for d in daily if len(daily[d]) >= 100):
            acc *= 1 + statistics.median(daily[d])
            self.lvl[d] = acc

    def excess(self, rows, i0, i1):
        if i0 < 0 or i1 >= len(rows) or i0 >= i1:
            return None
        la, lb = self.lvl.get(rows[i0][0]), self.lvl.get(rows[i1][0])
        if not (la and lb):
            return None
        return ((rows[i1][1] / rows[i0][1]) - (lb / la)) * 100


def reaction_index(rows, filed: str) -> int | None:
    """The session the market could first react in: the filing day when the results came out before the 3:30 PM
    close, else the next session (an evening or weekend filing)."""
    d, t = filed[:10], filed[11:16]
    i = _idx(rows, d)
    if i is not None and rows[i][0] == d and t >= "15:30":
        i += 1
    return i if i is not None and i < len(rows) else None


def _idx(rows, d: str) -> int | None:
    """Index of the first session on or after d."""
    lo, hi = 0, len(rows)
    while lo < hi:
        m = (lo + hi) // 2
        if rows[m][0] < d:
            lo = m + 1
        else:
            hi = m
    return lo if lo < len(rows) else None


# ------------------------------------------------------------------ features
def chart_features(rows, i: int, ix: Index) -> dict:
    """As of session i (the last close before the result): what the price says the market expects."""
    if i is None or i < 60:
        return {"ret20": NAN, "ret5": NAN, "hi1m": NAN, "hi52": NAN, "volr": NAN}
    c = rows[i][1]
    hi1m = max(r[1] for r in rows[max(0, i - 22):i])
    hi52 = max(r[1] for r in rows[max(0, i - 250):i + 1])
    v10 = [r[2] for r in rows[i - 9:i + 1] if r[2]]
    v60 = [r[2] for r in rows[i - 59:i + 1] if r[2]]
    r20, r5 = ix.excess(rows, i - 20, i), ix.excess(rows, i - 5, i)
    return {"ret20": _clip(r20, -60, 100), "ret5": _clip(r5, -40, 60), "hi1m": (c / hi1m - 1) * 100 if hi1m else NAN,
            "hi52": (c / hi52 - 1) * 100 if hi52 else NAN,
            "volr": (statistics.mean(v10) / statistics.mean(v60)) if v10 and v60 and statistics.mean(v60) else NAN}


def features(mk: Market, ix: Index, key: str, t: int, qe: date, asof: str, peers: dict) -> dict | None:
    """Everything known by `asof` (the session before the result) about quarter t (ending qe) of company `key`."""
    e = mk.ent[key]
    q = e["q"]
    S, NP, OPM = q.get("sales") or [], q.get("net_profit") or [], q.get("opm") or []
    L = lag_of(q.get("periods") or []) or 4          # periods in a year: 4 quarterly, 2 half-yearly
    if t < L + 1 or t - 1 >= len(S):
        return None
    g1 = _yoy(S[t - 1], S[t - 1 - L])
    if g1 is None:
        return None
    trail = _yoy(sum(x or 0 for x in S[t - L:t]), sum(x or 0 for x in S[t - 2 * L:t - L])) if t >= 2 * L else None
    base = _yoy(S[t - L], S[t - 2 * L]) if t >= 2 * L else None
    pg1 = _yoy(NP[t - 1], NP[t - 1 - L]) if t - 1 - L < len(NP) and NP[t - 1 - L] and NP[t - 1 - L] > 0 else None
    opm_ch = (OPM[t - 1] - OPM[t - 1 - L]) if t - 1 < len(OPM) and OPM[t - 1] is not None and OPM[t - 1 - L] is not None else None
    yr_sales = sum(x for x in S[max(0, t - L):t] if x)
    sym = key[4:] if key.startswith("NSE:") else ""
    g = mk.guided(e.get("code"), qe)
    ym, call = mk.call_before(e.get("code"), qe)
    lo12, hi2 = (qe - timedelta(days=365)).isoformat(), (qe - timedelta(days=60)).isoformat()
    caps = [(d, s) for d, s in mk.cap.get(sym, []) if lo12 < d <= hi2]
    six = (qe - timedelta(days=182)).isoformat()
    inflow = [(d, v, m) for d, v, m in mk.orders.get(sym, []) if six < d <= qe.isoformat()]
    # revenue due this quarter from orders already won: each spread evenly over its execution period
    q0, due = qe - timedelta(days=91 if L == 4 else 182), 0.0
    for d, v, m in mk.orders.get(sym, []):
        d0 = date.fromisoformat(d)
        if d0 > qe:
            continue
        months = m or 12.0
        d1 = d0 + timedelta(days=30.4 * months)
        overlap = (min(d1, qe) - max(d0, q0)).days
        if overlap > 0:
            due += v * overlap / (30.4 * months)
    base_q = S[t - L] if t - L < len(S) else None
    sec = mk.sector.get(key)
    ic, ic_notes = input_cost(sec, qe, mk.com)
    pr = peers.get((sec, qe.isoformat()), [])
    pr = [p for p in pr if p[0] < asof and p[3] != key]
    rows = mk.prices(key)
    i = _idx(rows, asof) if rows else None
    i = (len(rows) - 1) if rows and i is None else (i - 1) if i else i   # the last close before asof
    ch = chart_features(rows, i, ix) if rows else chart_features([], None, ix)
    f = {
        "g1": _clip(g1), "trail": _clip(trail), "gap": _clip(g1 - trail, -80, 80) if trail is not None else NAN,
        "base": _clip(base), "opm_ch": _clip(opm_ch, -30, 30), "opm_last": _clip(OPM[t - 1] if t - 1 < len(OPM) else None, -50, 80),
        "pg1": _clip(pg1, -100, 200), "size": math.log10(yr_sales) if yr_sales > 0 else NAN,
        "guide_gap": _clip(g[1] - g1, -80, 80) if g else NAN,
        "call_rev": DIR.get((call or {}).get("next_quarter_revenue"), NAN) if call else NAN,
        "call_margin": DIR.get((call or {}).get("next_quarter_margin"), NAN) if call else NAN,
        "call_capex": CAPEX.get((call or {}).get("capex_phase"), NAN) if call else NAN,
        "call_input": INPUT.get((call or {}).get("input_costs"), NAN) if call else NAN,
        "cap_n": float(len(caps)), "order_inflow_pct": min(100 * sum(v for _, v, _ in inflow) / yr_sales, 300) if inflow and yr_sales > 0 else 0.0,
        "order_rev_pct": min(100 * due / base_q, 300) if due and base_q and base_q > 0 else 0.0,
        "input_cost": _clip(ic, -60, 60) if not math.isnan(ic) else NAN,
        "peer_n": float(len(pr)), "peer_accel": (sum(p[1] for p in pr) / len(pr)) if len(pr) >= 3 else NAN,
        "peer_g": statistics.median(p[2] for p in pr) if len(pr) >= 3 else NAN,
        **ch, "sector": float(mk.sectors.index(sec)) if sec in mk.sectors else NAN,
    }
    f["_ctx"] = {"g1": g1, "trail": trail, "guide": g, "call": (ym, call), "caps": caps[-2:], "inflow": inflow, "due": due,
                 "base_q": base_q, "ic_notes": ic_notes, "peers": pr, "sector": sec}
    return f


# ------------------------------------------------------------------ the dataset: every reported company-quarter
def build(mk: Market, ix: Index):
    """(rows, peers): rows carry features and outcomes; peers[(sector, quarter end)] = [(result date, accelerated,
    growth, key)] -- used for the "peers already reported" features (only those dated before the asof)."""
    base_rows, peers = [], defaultdict(list)
    for key, e in mk.ent.items():
        P, S, NP = e["q"].get("periods") or [], e["q"].get("sales") or [], e["q"].get("net_profit") or []
        L = lag_of(P)
        if not L:
            continue
        for t in range(L + 1, len(P)):
            qe = qend(P[t])
            g, g1 = _yoy(S[t], S[t - L]), _yoy(S[t - 1], S[t - 1 - L])
            if g is None or g1 is None:
                continue
            filed = mk.result_filed(key, qe)
            rd = filed[:10] if filed else None
            pu = (NP[t] > NP[t - L]) if NP[t] is not None and NP[t - L] is not None and NP[t - L] > 0 else None
            base_rows.append((key, t, qe, rd, g, g1, pu, P[t], filed))
            if rd and mk.sector.get(key):
                peers[(mk.sector[key], qe.isoformat())].append((rd, 1 if g > g1 else 0, _clip(g), key))
    rows = []
    for key, t, qe, rd, g, g1, pu, label, filed in base_rows:
        asof = rd or (qe + timedelta(days=45)).isoformat()
        f = features(mk, ix, key, t, qe, asof, peers)
        if not f:
            continue
        r = {"key": key, "q": label, "qe": qe, "rd": rd, "f": f, "g": _clip(g), "accel": 1 if g > g1 else 0,
             "profit_up": None if pu is None else int(pu)}
        if rd:
            px = mk.prices(key)
            i = reaction_index(px, filed)
            if i is not None and i > 0 and px[i][0] <= (date.fromisoformat(rd) + timedelta(days=5)).isoformat():
                r["rday"], r["filed"] = px[i][0], filed
                # the result-day move: the stock's own close-to-close change in the session it could first react,
                # NOT netted against the market (the question is simply: did it go up or down on its result)
                r["react"] = r["react_raw"] = (px[i][1] / px[i - 1][1] - 1) * 100
                r["react_x"] = ix.excess(px, i - 1, i)            # kept for reference: the same session vs the market
                r["swing"] = ix.excess(px, i - 5, i + 3)          # 5 sessions before -> 3 after
                r["post20"] = ix.excess(px, i, i + 20)            # the drift: reaction-day close -> 20 sessions on
                r["post40"] = ix.excess(px, i, i + 40)
                o = px[i][3]
                if o:
                    r["gap"] = (o / px[i - 1][1] - 1) * 100                      # previous close -> next open
                    r["oc"] = (px[i][1] / o - 1) * 100                           # open -> close that session
                    r["o3"] = (px[i + 2][1] / o - 1) * 100 if i + 2 < len(px) else None
                tm = filed[11:16]
                r["after"] = tm >= "15:30" or tm < "09:00"       # out of market hours: the next session takes it all
        r["react_up"] = None if r.get("react") is None else int(r["react"] > 0)
        rows.append(r)
    hist = defaultdict(list)
    for r in rows:
        if r.get("react") is not None:
            hist[r["key"]].append((r["rd"], r["react"]))
    detail = defaultdict(list)
    for r in rows:
        if r.get("react") is not None:
            detail[r["key"]].append({"date": r.get("rday") or r["rd"], "filed": r.get("filed"), "quarter": r["q"],
                                     "react": round(r["react"], 2), "raw": round(r["react_raw"], 2),
                                     "swing": None if r.get("swing") is None else round(r["swing"], 2),
                                     "post20": None if r.get("post20") is None else round(r["post20"], 2),
                                     "g": round(r["g"], 1), "profit_up": r["profit_up"], "accel": r["accel"]})
    HIST_DETAIL.clear()
    HIST_DETAIL.update(detail)
    for r in rows:
        add_history(r["f"], hist.get(r["key"], []), r["rd"] or (r["qe"] + timedelta(days=45)).isoformat())
    return rows, peers, hist


HIST_DETAIL: dict = {}


def add_history(f: dict, past: list, asof: str) -> None:
    """How this stock has reacted on its earlier result days (vs the market): share that beat it, the
    average move, how many. In the backtest this alone ranked result-day reactions better than everything
    else together -- some stocks keep beating the market on results, some keep lagging."""
    prev = [x for d, x in past if d and d < asof]
    f["hist_n"] = float(len(prev))
    f["hist_beat"] = sum(x > 0 for x in prev) / len(prev) if len(prev) >= 2 else NAN
    f["hist_avg"] = _clip(sum(prev[-6:]) / len(prev[-6:]), -20, 20) if len(prev) >= 2 else NAN
    f["_ctx_hist"] = prev[-8:]


# ------------------------------------------------------------------ models
def _X(rows):
    import numpy as np
    return np.array([[_num(r["f"][k]) if _num(r["f"][k]) is not None else NAN for k in FEATURES] for r in rows], dtype=float)


def fit(rows, target):
    rows = [r for r in rows if r.get(target) is not None]
    if len(rows) < 300:
        return None
    from sklearn.ensemble import HistGradientBoostingClassifier
    m = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=40,
                                       l2_regularization=1.0, categorical_features=[FEATURES.index("sector")], random_state=0)
    m.fit(_X(rows), [r[target] for r in rows])
    return m


def predict(m, rows):
    if m is None or not rows:
        return [None] * len(rows)
    return [float(p[1]) for p in m.predict_proba(_X(rows))]


def _auc(ps, ys):
    pos = [p for p, y in zip(ps, ys) if y == 1]
    neg = [p for p, y in zip(ps, ys) if y == 0]
    if not pos or not neg:
        return None
    allv = sorted([(p, 1) for p in pos] + [(p, 0) for p in neg])
    rank, s = 0, 0.0
    for i, (p, y) in enumerate(allv, 1):
        if y:
            s += i
    return round((s - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)), 3)


def _summary(rows, pkey, ykey):
    rows = [r for r in rows if r.get(ykey) is not None and r.get(pkey) is not None]
    if not rows:
        return {"n": 0}
    hits = sum(1 for r in rows if (r[pkey] >= 0.5) == bool(r[ykey]))
    base = 100 * sum(r[ykey] for r in rows) / len(rows)
    buckets = []
    for lo, hi in ((0, .35), (.35, .5), (.5, .65), (.65, 1.01)):
        b = [r for r in rows if lo <= r[pkey] < hi]
        if b:
            buckets.append({"range": f"{int(lo * 100)}–{min(100, int(hi * 100))}%", "n": len(b),
                            "actual": round(100 * sum(r[ykey] for r in b) / len(b), 1)})
    return {"n": len(rows), "hit": round(100 * hits / len(rows), 1), "base": round(base, 1),
            "always_majority": round(max(base, 100 - base), 1), "auc": _auc([r[pkey] for r in rows], [r[ykey] for r in rows]),
            "buckets": buckets}


def _thirds(rows, key, val):
    out = []
    for name, sel in (("Top third (most likely)", 2), ("Middle third", 1), ("Bottom third (least likely)", 0)):
        xs = []
        for qq in {r["q"] for r in rows}:
            grp = sorted([r for r in rows if r["q"] == qq and r.get(key) is not None and r.get(val) is not None], key=lambda r: r[key])
            if len(grp) < 9:
                continue
            xs += [r[val] for r in grp[sel * len(grp) // 3:(sel + 1) * len(grp) // 3]]
        if xs:
            out.append({"group": name, "n": len(xs), "median": round(statistics.median(xs), 2), "mean": round(statistics.mean(xs), 2),
                        "beat": round(100 * sum(x > 0 for x in xs) / len(xs))})
    return out


REACT_BINS = ((0, .38), (.38, .44), (.44, .5), (.5, .56), (.56, 1.01))
POST_FEATURES = ["a_g", "a_dg", "a_pg", "a_dpg", "a_qoq", "a_om", "a_om1", "a_l2p", "a_p2l"]
POST_PRE = ["g1", "trail", "pg1", "opm_ch", "ret20", "ret5", "hi1m", "hi52", "volr", "hist_beat", "hist_avg", "size", "sector"]
POST_BINS = ((0, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6), (.6, 1.01))


def actual_features(q: dict, t: int, g1: float) -> dict:
    """What the result itself showed (known the moment it is filed): sales growth and its change vs last
    quarter's pace, profit growth and its change, sales vs the previous quarter, margin change, loss <-> profit."""
    S, NP, OPM = q.get("sales") or [], q.get("net_profit") or [], q.get("opm") or []
    L = lag_of(q.get("periods") or []) or 4
    g = _yoy(S[t], S[t - L])
    pg = _yoy(NP[t], NP[t - L]) if NP[t - L] and NP[t - L] > 0 else None
    pg1 = _yoy(NP[t - 1], NP[t - 1 - L]) if t >= L + 1 and NP[t - 1 - L] and NP[t - 1 - L] > 0 else None
    om = (OPM[t] - OPM[t - L]) if t < len(OPM) and OPM[t] is not None and OPM[t - L] is not None else None
    om1 = (OPM[t] - OPM[t - 1]) if t < len(OPM) and OPM[t] is not None and OPM[t - 1] is not None else None
    return {"a_g": _clip(g), "a_dg": _clip(None if g is None else g - g1, -100, 100), "a_pg": _clip(pg, -100, 300),
            "a_dpg": _clip(None if pg is None or pg1 is None else pg - pg1, -300, 300), "a_qoq": _clip(_yoy(S[t], S[t - 1]), -80, 150),
            "a_om": _clip(om, -30, 30), "a_om1": _clip(om1, -30, 30),
            "a_l2p": 1.0 if (NP[t] or 0) > 0 and (NP[t - L] or 0) <= 0 else 0.0,
            "a_p2l": 1.0 if (NP[t] or 0) <= 0 and (NP[t - L] or 0) > 0 else 0.0,
            "_g": g, "_pg": pg, "_om": om, "_np": NP[t], "_np0": NP[t - L]}


def _XP(rows):
    import numpy as np
    return np.array([[_num(r["a"].get(k)) if k.startswith("a_") and _num(r["a"].get(k)) is not None
                      else _num(r["f"].get(k)) if not k.startswith("a_") and _num(r["f"].get(k)) is not None else NAN
                      for k in POST_PRE + POST_FEATURES] for r in rows], dtype=float)


def fit_post(rows):
    rows = [r for r in rows if r.get("react") is not None]
    if len(rows) < 300:
        return None
    from sklearn.ensemble import HistGradientBoostingClassifier
    m = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=40, l2_regularization=1.0,
                                       categorical_features=[(POST_PRE + POST_FEATURES).index("sector")], random_state=0)
    m.fit(_XP(rows), [int(r["react"] > 0) for r in rows])
    return m


def post_dist(res) -> list:
    """For results the overnight read scored alike: how often the stock closed up, and the median opening gap,
    open -> close and open -> 2 sessions later. What the live read quotes as 'expected'."""
    out = []
    for lo, hi in POST_BINS:
        xs = [r for p, r in res if lo <= p < hi]
        if len(xs) < 30:
            continue
        md = lambda k: round(statistics.median([r[k] for r in xs if r.get(k) is not None]), 2) if any(r.get(k) is not None for r in xs) else None
        upk = lambda k: round(100 * sum(1 for r in xs if r.get(k) is not None and r[k] > 0) / max(1, sum(1 for r in xs if r.get(k) is not None)), 1)
        out.append({"lo": lo, "hi": hi, "n": len(xs), "up": upk("react"), "react": md("react"), "gap": md("gap"), "oc": md("oc"),
                    "oc_up": upk("oc"), "o3": md("o3"), "o3_up": upk("o3")})
    return out


def post_fifths(res) -> list:
    out = []
    for name, sel in (("Strongest fifth", 4), ("4th", 3), ("Middle", 2), ("2nd", 1), ("Weakest fifth", 0)):
        xs = []
        for qq in {r["q"] for _, r in res}:
            g = sorted([(p, r) for p, r in res if r["q"] == qq], key=lambda x: x[0])
            xs += [r for _, r in g[sel * len(g) // 5:(sel + 1) * len(g) // 5]]
        if xs:
            md = lambda k: round(statistics.median([r[k] for r in xs if r.get(k) is not None]), 2)
            out.append({"group": name, "n": len(xs), "up": round(100 * sum(r["react"] > 0 for r in xs) / len(xs), 1),
                        "react": md("react"), "gap": md("gap"), "oc": md("oc"), "o3": md("o3")})
    return out


def post_read(p: float, a: dict, dist: list) -> dict:
    """The plain-language overnight read and what to do with it, from the calibrated bin."""
    b = next((x for x in dist if x["lo"] <= p < x["hi"]), None)
    if b is None:
        return {"verdict": "Not enough history to read", "tone": "", "action": ""}
    if b["up"] >= 55:
        v, tone = "Strong result — likely to open higher", "up"
        act = ("Stocks read like this opened about {gap:+.1f}% and then slipped {oc:+.1f}% from the open on average — "
               "the move is mostly in the opening gap, so chasing it at the open has not paid.")
    elif b["up"] <= 30:
        v, tone = "Weak result — likely to open lower", "dn"
        act = ("Stocks read like this opened about {gap:+.1f}% and kept falling ({oc:+.1f}% from the open, {o3:+.1f}% over 3 sessions) — "
               "if you hold it, the open has usually been the better exit.")
    else:
        v, tone = "Mixed result — no clear direction", ""
        act = "Stocks read like this opened about {gap:+.1f}% on average, with no reliable direction after."
    vals = {k: (b.get(k) if b.get(k) is not None else 0.0) for k in ("gap", "oc", "o3")}
    return {"verdict": v, "tone": tone, "action": act.format(**vals), "bin": b}



def react_dist(rows) -> list:
    """For stocks the model gave a similar chance of closing up, how they really moved on the result day:
    how many closed up, the median move, and the middle half of moves (25th-75th percentile)."""
    out = []
    for lo, hi in REACT_BINS:
        xs = sorted(r["react"] for r in rows if r.get("p_react") is not None and r.get("react") is not None and lo <= r["p_react"] < hi)
        if len(xs) >= 30:
            q = lambda f: round(xs[min(len(xs) - 1, int(f * len(xs)))], 2)
            out.append({"lo": lo, "hi": hi, "n": len(xs), "up": round(100 * sum(x > 0 for x in xs) / len(xs), 1),
                        "median": q(.5), "p25": q(.25), "p75": q(.75), "p10": q(.1), "p90": q(.9)})
    return out


HIST_BINS = ((0, .21), (.21, .41), (.41, .61), (.61, .81), (.81, 1.01))


def hist_dist(rows) -> list:
    """How often a stock closed up on its result day, by how often it closed up on its EARLIER result days
    (at least three of them) -- the steadiest single signal for result-day direction in the backtest."""
    by = defaultdict(list)
    for r in rows:
        if r.get("react") is not None and r.get("rd"):
            by[r["key"]].append(r)
    pairs = []
    for v in by.values():
        v.sort(key=lambda r: r["rd"])
        for i, r in enumerate(v):
            prev = [x["react"] for x in v[:i]]
            if len(prev) >= 3:
                pairs.append((sum(p > 0 for p in prev) / len(prev), r["react"]))
    out = []
    for lo, hi in HIST_BINS:
        ys = sorted(y for p, y in pairs if lo <= p < hi)
        if len(ys) >= 30:
            q = lambda f: round(ys[min(len(ys) - 1, int(f * len(ys)))], 2)
            out.append({"lo": lo, "hi": hi, "n": len(ys), "up": round(100 * sum(y > 0 for y in ys) / len(ys), 1),
                        "median": q(.5), "p25": q(.25), "p75": q(.75)})
    return out


def _bucket_table(rows, label, cond_list, val):
    out = []
    for name, cond in cond_list:
        xs = [r[val] for r in rows if r.get(val) is not None and cond(r)]
        if len(xs) >= 20:
            out.append({"group": name, "n": len(xs), "median": round(statistics.median(xs), 2), "mean": round(statistics.mean(xs), 2),
                        "beat": round(100 * sum(x > 0 for x in xs) / len(xs))})
    return {"label": label, "rows": out}


# ------------------------------------------------------------------ reasons a reader can check
def drivers(f: dict, mk: Market) -> list[dict]:
    c = f["_ctx"]
    out = []
    g1, tr = c["g1"], c["trail"]
    out.append({"k": "trend", "tone": "up" if g1 > 15 else "dn" if g1 < 0 else "",
                "text": f"Last quarter sales {g1:+.0f}% YoY" + (f" (trailing year {tr:+.0f}%)" if tr is not None else "")})
    if tr is not None and g1 < tr - 10:
        out.append({"k": "bounce", "tone": "up", "text": f"Last quarter ran {tr - g1:.0f} pts below its own trend — tends to bounce back"})
    elif tr is not None and g1 > tr + 15:
        out.append({"k": "fade", "tone": "dn", "text": f"Last quarter ran {g1 - tr:.0f} pts above its own trend — hard to repeat"})
    ym, call = c["call"]
    if call:
        bits = [f"next-quarter revenue {call.get('next_quarter_revenue', 'not_said').replace('_', ' ')}",
                f"margin {call.get('next_quarter_margin', 'not_said').replace('_', ' ')}"]
        if call.get("capex_phase") not in (None, "none_said"):
            bits.append(f"capex {call['capex_phase'].replace('_', ' ')}")
        if call.get("input_costs") not in (None, "not_said"):
            bits.append(f"input costs {call['input_costs']}")
        tone = "up" if call.get("next_quarter_revenue") == "up" else "dn" if call.get("next_quarter_revenue") == "down" else ""
        out.append({"k": "call", "tone": tone, "text": f"{ym} earnings call: " + ", ".join(bits),
                    "quote": " … ".join((call.get("key_quotes") or [])[:2])[:300]})
    if c["guide"]:
        gv = c["guide"][1]
        out.append({"k": "guide", "tone": "up" if gv > g1 + 5 else "dn" if gv < g1 - 5 else "",
                    "text": f"Management guided ~{gv:.0f}% revenue growth for the year ({c['guide'][0]} call)", "quote": c["guide"][2][:220]})
    if c["caps"]:
        d, s = c["caps"][-1]
        out.append({"k": "capacity", "tone": "up", "text": f"New capacity / commissioning filed {d} — revenue from it can start showing", "quote": (s or "")[:200]})
    if c["due"] and c["base_q"]:
        out.append({"k": "orders", "tone": "up" if 100 * c["due"] / c["base_q"] >= 15 else "",
                    "text": f"Orders already won should bill ~₹{c['due']:,.0f} cr this quarter — {100 * c['due'] / c['base_q']:.0f}% of the same quarter last year's sales"})
    elif c["inflow"]:
        out.append({"k": "orders", "tone": "", "text": f"{len(c['inflow'])} order win(s) worth ₹{sum(v for _, v, _ in c['inflow']):,.0f} cr in the last 6 months"})
    if c["ic_notes"]:
        ic = f["input_cost"]
        if not math.isnan(ic) and abs(ic) >= 5:
            names = ", ".join(f"{n} {ch:+.0f}%" for n, ch, _ in c["ic_notes"])
            out.append({"k": "costs", "tone": "dn" if ic > 0 else "up", "text": f"Input costs vs a year ago: {names} — {'a squeeze' if ic > 0 else 'a tailwind'} for margins"})
    if not math.isnan(f["peer_accel"]):
        pa = f["peer_accel"]
        out.append({"k": "peers", "tone": "up" if pa >= .6 else "dn" if pa <= .4 else "",
                    "text": f"{int(f['peer_n'])} {(c['sector'] or '').replace('-', ' ')} peers already reported: {pa * 100:.0f}% grew faster than the quarter before (median growth {f['peer_g']:+.0f}%)"})
    past = f.get("_ctx_hist") or []
    if len(past) >= 2:
        b = sum(x > 0 for x in past)
        out.append({"k": "history", "tone": "up" if b / len(past) >= .6 else "dn" if b / len(past) <= .35 else "",
                    "text": f"On its last {len(past)} result days it closed up {b} time{'s' if b != 1 else ''} (average move {sum(past) / len(past):+.1f}%)"})
    if not math.isnan(f["ret20"]):
        r20, h1 = f["ret20"], f["hi1m"]
        if r20 >= 8 or (h1 >= -2 and r20 > 0):
            out.append({"k": "chart", "tone": "dn", "text": f"Chart: {r20:+.0f}% vs the market in 20 sessions, {abs(h1):.0f}% {'below' if h1 < 0 else 'above'} its 1-month high — good news is partly priced in"})
        elif r20 <= -8 or h1 <= -10:
            out.append({"k": "chart", "tone": "up", "text": f"Chart: {r20:+.0f}% vs the market in 20 sessions, {abs(h1):.0f}% below its 1-month high — expectations are low"})
        else:
            out.append({"k": "chart", "tone": "", "text": f"Chart: {r20:+.0f}% vs the market in 20 sessions, {abs(h1):.0f}% below its 1-month high"})
    return out


# ------------------------------------------------------------------ alerts for the bell
def claude_record(rows, pres, reads: dict) -> dict:
    """Claude's read of each results filing against what the stock then did, for filings after its training data
    ends -- next to the numbers model's call on the same stocks, so the two can be compared like for like."""
    pn = {id(r): p for p, r in pres}
    xs = []
    for r in rows:
        if r.get("react") is None or not r.get("filed") or not r["key"].startswith("NSE:") or r["filed"][:10] < CLAUDE_SINCE:
            continue
        c = reads.get(f"{r['key'][4:]}|{r['filed']}")
        if c:
            xs.append((c, r, pn.get(id(r))))
    if not xs:
        return {"n": 0, "since": CLAUDE_SINCE}
    up = lambda g: round(100 * sum(r["react"] > 0 for _, r, _ in g) / len(g), 1)
    md = lambda g, k: round(statistics.median([r[k] for _, r, _ in g if r.get(k) is not None]), 2) if any(r.get(k) is not None for _, r, _ in g) else None
    by_v = []
    for v in ("strong", "good", "mixed", "weak", "poor"):
        g = [x for x in xs if x[0].get("verdict") == v]
        if g:
            by_v.append({"verdict": v, "n": len(g), "up": up(g), "react": md(g, "react"), "gap": md(g, "gap"), "oc": md(g, "oc")})
    by_p = []
    for lo, hi in ((0, 30), (30, 45), (45, 60), (60, 101)):
        g = [x for x in xs if lo <= x[0].get("p_up", 0) < hi]
        if g:
            by_p.append({"range": f"{lo}–{min(hi, 100)}%", "n": len(g), "up": up(g), "react": md(g, "react")})
    both = [x for x in xs if x[2] is not None]
    ys = [int(r["react"] > 0) for _, r, _ in both]
    cmp = {"n": len(both), "claude": _auc([c["p_up"] for c, _, _ in both], ys), "numbers": _auc([p for *_, p in both], ys),
           "together": _auc([(c["p_up"] / 100 + p) / 2 for c, _, p in both], ys)} if len(both) >= 30 else {"n": len(both)}
    return {"since": CLAUDE_SINCE, "n": len(xs), "base": up(xs), "auc": _auc([c["p_up"] for c, _, _ in xs], [int(r["react"] > 0) for _, r, _ in xs]),
            "by_verdict": by_v, "by_p": by_p, "compare": cmp,
            "after_hours": sum(1 for _, r, _ in xs if r.get("after")),
            "rows": [{"s": r["key"][4:], "q": r["q"], "d": r.get("rday"), "f": r["filed"], "v": c.get("verdict"), "cp": c.get("p_up"),
                      "p": None if p is None else round(p, 3), "mv": round(r["react"], 2),
                      "gap": None if r.get("gap") is None else round(r["gap"], 2), "sum": c.get("summary")}
                     for c, r, p in sorted(xs, key=lambda x: x[1]["filed"], reverse=True)[:600]]}


def write_alerts(today: date, upcoming: list, newly: list, reported: list | None = None, pead: dict | None = None) -> None:
    """Today's Results Preview alerts (board companies only): a confident call reporting within two days
    ("rp_soon"), and a confident call whose result just came out -- right or wrong ("rp_out")."""
    items = []
    for r in upcoming:
        if r["board"] and r["confidence"] == "high" and r["date"] <= (today + timedelta(days=2)).isoformat():
            items.append({"type": "rp_soon", "scope": "board", "code": r["code"], "symbol": r["symbol"], "name": r["name"],
                          "date": r["date"], "quarter": r["quarter"], "p_profit": r["p_profit"], "p_accel": r["p_accel"],
                          "why": [d["text"] for d in r["drivers"][:3]]})
    for r in newly:
        if r.get("board") and r.get("confidence") == "high":
            hit = r.get("profit_up") is not None and (r["p_profit"] >= .5) == bool(r["profit_up"])
            items.append({"type": "rp_out", "scope": "board", "code": r["code"], "symbol": r["symbol"], "name": r["name"],
                          "date": r["date"], "quarter": r["quarter"], "p_profit": r["p_profit"], "profit_up": r.get("profit_up"),
                          "actual_g": r.get("actual_g"), "react": r.get("react"), "hit": hit})
    for r in reported or []:
        if r["board"] and r["tone"] and not r.get("result"):
            items.append({"type": "rp_post", "scope": "board", "code": r["code"], "symbol": r["symbol"], "name": r["name"],
                          "quarter": r["quarter"], "filed": r["filed"], "verdict": r["verdict"], "tone": r["tone"], "action": r["action"],
                          "sales_g": r["sales_g"], "profit_g": r["profit_g"], "p_up": r["p_up"]})
    for r in (pead or {}).get("to_buy") or []:          # PEAD: qualified tonight, the entry is the next open
        items.append({"type": "rp_pead", "scope": "board", "code": r.get("code"), "symbol": r["symbol"], "name": r["name"],
                      "quarter": r["q"], "sue": r["sue"], "ear": r["ear"], "filed": r["filed"]})
    doc = _load(ALERTS_FILE, {"sessions": []})
    sessions = {x["date"]: x["items"] for x in doc.get("sessions") or []}
    sessions[today.isoformat()] = items
    keep = sorted(sessions)[-15:]
    ALERTS_FILE.write_text(json.dumps({"sessions": [{"date": d, "items": sessions[d]} for d in keep]}, separators=(",", ":"),
                                      ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ the nightly run
def run() -> dict:
    mk = Market()
    ix = Index(mk)
    rows, peers, hist = build(mk, ix)
    count = defaultdict(int)
    for r in rows:
        count[r["q"]] += 1
    # quarters with enough companies to test on (a few companies on file stopped reporting years ago)
    qs = sorted((q for q, n in count.items() if n >= 200), key=qend)
    qs += sorted((q for q in count if q not in qs and qs and qend(q) > qend(qs[-1])), key=qend)   # the season under way

    # ---- walk-forward backtest
    bt = []
    for qq in qs[3:]:
        cut = qend(qq) - timedelta(days=80)
        train = [r for r in rows if r["qe"] < cut]
        test = [r for r in rows if r["q"] == qq]
        ms = {t: fit(train, t) for t in ("accel", "profit_up", "react_up")}
        pa, pp, pr = predict(ms["accel"], test), predict(ms["profit_up"], test), predict(ms["react_up"], test)
        for r, a, p, x in zip(test, pa, pp, pr):
            bt.append({**{k: r.get(k) for k in ("key", "q", "accel", "profit_up", "react", "react_up", "swing", "post20", "post40", "rday", "rd", "g")},
                       "p_accel": a, "p_profit": p, "p_react": x, "board": bool(mk.ent[r["key"]].get("code")),
                       "ret20": r["f"]["ret20"], "hi1m": r["f"]["hi1m"]})
    tr = [r for r in bt if r.get("react") is not None]
    nan = lambda v: v is None or (isinstance(v, float) and math.isnan(v))
    pead_rows = [r for r in rows if r.get("react") is not None and r.get("post20") is not None]
    backtest = {
        "quarters": sorted({r["q"] for r in bt}, key=qend), "n": len(bt), "companies": len({r["key"] for r in bt}),
        "accel": _summary(bt, "p_accel", "accel"), "profit": _summary(bt, "p_profit", "profit_up"), "react": _summary(bt, "p_react", "react_up"),
        "trade_react": {"swing": _thirds(tr, "p_react", "swing"), "react": _thirds(tr, "p_react", "react")},
        "react_dist": react_dist(tr),
        "hist_dist": hist_dist(rows),
        "trade_profit": {"swing": _thirds(tr, "p_profit", "swing"), "react": _thirds(tr, "p_profit", "react")},
        "chart_setup": _bucket_table(tr, "Result-day move (the stock's own), by the chart going in", [
            ("Ran up 10%+ vs market in the 20 sessions before", lambda r: not nan(r["ret20"]) and r["ret20"] >= 10),
            ("Within 2% of its 1-month high", lambda r: not nan(r["hi1m"]) and r["hi1m"] >= -2),
            ("Flat into results (−3% to +3%)", lambda r: not nan(r["ret20"]) and -3 <= r["ret20"] <= 3),
            ("10%+ below its 1-month high", lambda r: not nan(r["hi1m"]) and r["hi1m"] <= -10),
            ("Fell 10%+ vs market before", lambda r: not nan(r["ret20"]) and r["ret20"] <= -10)], "react"),
        "pead": [
            _bucket_table(pead_rows, "After the result: result-day close → 20 sessions later, vs market", [
                ("Rose 5%+ on the result day", lambda r: r["react"] >= 5),
                ("Rose 0–5% on the result day", lambda r: 0 <= r["react"] < 5),
                ("Fell 0–5% on the result day", lambda r: -5 < r["react"] < 0),
                ("Fell 5%+ on the result day", lambda r: r["react"] <= -5),
                ("Profit up and growth faster", lambda r: r["profit_up"] == 1 and r["accel"] == 1),
                ("Profit down and growth slower", lambda r: r["profit_up"] == 0 and r["accel"] == 0),
                ("Rose 5%+ on the day AND profit up, growth faster", lambda r: r["react"] >= 5 and r["profit_up"] == 1 and r["accel"] == 1)], "post20"),
            _bucket_table(pead_rows, "After the result: result-day close → 40 sessions later, vs market", [
                ("Rose 5%+ on the result day", lambda r: r["react"] >= 5),
                ("Fell 5%+ on the result day", lambda r: r["react"] <= -5),
                ("Rose 5%+ on the day AND profit up, growth faster", lambda r: r["react"] >= 5 and r["profit_up"] == 1 and r["accel"] == 1)], "post40")],
        "all_swing_median": round(statistics.median([r["swing"] for r in tr if r.get("swing") is not None]), 2) if tr else None,
    }

    # ---- live predictions: models fitted on everything reported
    ms = {t: fit(rows, t) for t in ("accel", "profit_up", "react_up")}
    cal = _load(DATA / "results_calendar_in" / "latest.json", {})
    today = datetime.now(timezone.utc).date()
    log = _load(PRED_LOG, {})
    want = {}
    for it in (cal.get("events") or []) + (cal.get("items") or []):
        sym = str(it.get("symbol") or "").upper()
        if not it.get("date"):            # half-yearly reporters are handled too (lag_of)
            continue
        key = next((k for k in (f"NSE:{sym}", f"BSE:{sym}") if k in mk.ent), None)
        if key is None and it.get("code"):
            key = next((k for k, e in mk.ent.items() if e.get("code") == str(it["code"])), None)
        if key:
            want[key] = it
    # BSE's forthcoming list fills in companies NSE has no notice for yet (an NSE notice wins when both exist)
    bse_codes = {str(c.get("bse_code")): f"NSE:{str(c.get('nse_code')).upper()}" for c in _load(DATA / "companies_raw.json", [])
                 if c.get("bse_code") and c.get("nse_code")}
    for k, d in bse_forthcoming().items():
        key = k if k in mk.ent else bse_codes.get(k[4:]) if k.startswith("BSE:") else None
        if key and key in mk.ent and key not in want and d >= (today - timedelta(days=1)).isoformat():
            want[key] = {"date": d, "status": "confirmed", "source": "BSE"}
    upcoming, cand = [], []
    for key, it in want.items():
        d = date.fromisoformat(it["date"])
        P = mk.ent[key]["q"].get("periods") or []
        if not standard(P) or not (today - timedelta(days=1) <= d <= today + timedelta(days=UPCOMING_DAYS)):
            continue
        target = next_label(P[-1], lag_of(P))
        if qend(target) >= d or (d - qend(target)).days > 120:   # not ended yet / an old quarter: results on file are stale
            continue
        f = features(mk, ix, key, len(P), qend(target), min(it["date"], today.isoformat() + "~"), peers)
        if f:
            add_history(f, hist.get(key, []), "9999")
            cand.append((key, it, target, f))
    pa = predict(ms["accel"], [{"f": c[3]} for c in cand])
    pp = predict(ms["profit_up"], [{"f": c[3]} for c in cand])
    px = predict(ms["react_up"], [{"f": c[3]} for c in cand])
    for (key, it, target, f), a, p, x in zip(cand, pa, pp, px):
        e = mk.ent[key]
        conf = max(abs((a or .5) - .5), abs((p or .5) - .5))
        rec = {"key": key, "code": e.get("code"), "symbol": e["symbol"], "name": e["name"], "board": bool(e.get("code")),
               "sector": (mk.sector.get(key) or "").replace("-", " "), "quarter": target, "half": lag_of(mk.ent[key]["q"].get("periods") or []) == 2, "date": it["date"], "status": it.get("status"),
               "last_g": round(f["_ctx"]["g1"], 1), "p_accel": round(a, 3), "p_profit": round(p, 3), "p_react": round(x, 3),
               "confidence": "high" if conf >= .2 else "medium" if conf >= .1 else "low",
               "chart": {k: (None if math.isnan(f[k]) else round(f[k], 1)) for k in ("ret20", "ret5", "hi1m", "hi52", "volr")},
               "drivers": drivers(f, mk)}
        upcoming.append(rec)
        lk = f"{key}|{target}"
        if lk not in log or (log[lk].get("date") or "") >= today.isoformat():
            log[lk] = {**{k: rec[k] for k in ("key", "code", "symbol", "name", "board", "quarter", "date", "last_g", "p_accel", "p_profit", "p_react", "confidence")},
                       "made": today.isoformat()}
    upcoming.sort(key=lambda r: (r["date"], not r["board"], -max(abs(r["p_accel"] - .5), abs(r["p_profit"] - .5))))

    # ---- every company's next result (the "Check a stock" box), whether or not its date is announced
    allc, acand = {}, []
    for key, e in mk.ent.items():
        P = e["q"].get("periods") or []
        if not standard(P) or (today - qend(P[-1])).days > 200:
            continue
        target = next_label(P[-1], lag_of(P))
        it = want.get(key)
        if it and date.fromisoformat(it["date"]) <= qend(target):
            it = None                       # that calendar date was the result just reported, not the next one
        d = it["date"] if it else (qend(target) + timedelta(days=45)).isoformat()
        f = features(mk, ix, key, len(P), qend(target), min(d, today.isoformat() + "~"), peers)
        if f:
            add_history(f, hist.get(key, []), "9999")
            acand.append((key, target, d, bool(it), f))
    A_ = predict(ms["accel"], [{"f": c[4]} for c in acand])
    P_ = predict(ms["profit_up"], [{"f": c[4]} for c in acand])
    X_ = predict(ms["react_up"], [{"f": c[4]} for c in acand])
    for (key, target, d, known, f), a, p, x in zip(acand, A_, P_, X_):
        e = mk.ent[key]
        allc[key] = {"key": key, "code": e.get("code"), "symbol": e["symbol"], "name": e["name"], "board": bool(e.get("code")),
                     "sector": (mk.sector.get(key) or "").replace("-", " "), "quarter": target, "half": lag_of(e["q"].get("periods") or []) == 2,
                     "date": d, "date_known": known,
                     "last_g": round(f["_ctx"]["g1"], 1), "p_accel": round(a, 3), "p_profit": round(p, 3), "p_react": round(x, 3),
                     "chart": {k: (None if math.isnan(f[k]) else round(f[k], 1)) for k in ("ret20", "hi1m", "hi52")},
                     "drivers": drivers(f, mk),
                     "alias": e.get("alias") or "",
                     "history": sorted(HIST_DETAIL.get(key, []), key=lambda h: h["date"])[-8:]}
    ALL_FILE.write_text(json.dumps({"asof": today.isoformat(), "companies": allc}, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    # ---- recent result days: who beat the market on the day, and what the walk-forward model had said
    pred = {(r["key"], r["q"]): r for r in bt}
    since = (today - timedelta(days=120)).isoformat()
    recent = []
    for r in rows:
        if r.get("react") is None or not r["rd"] or r["rd"] < since:
            continue
        e, b_ = mk.ent[r["key"]], pred.get((r["key"], r["q"])) or {}
        recent.append({"key": r["key"], "code": e.get("code"), "symbol": e["symbol"], "name": e["name"], "board": bool(e.get("code")),
                       "sector": (mk.sector.get(r["key"]) or "").replace("-", " "), "quarter": r["q"], "date": r.get("rday") or r["rd"],
                       "filed": r.get("filed"), "react": round(r["react"], 2), "raw": round(r["react_raw"], 2), "g": round(r["g"], 1), "accel": r["accel"], "profit_up": r["profit_up"],
                       "p_react": round(b_["p_react"], 3) if b_.get("p_react") is not None else None,
                       "p_profit": round(b_["p_profit"], 3) if b_.get("p_profit") is not None else None})
    recent.sort(key=lambda x: (x["date"], x["react"]), reverse=True)

    # ---- grade logged predictions whose result is in
    newly = []
    for lk, rec in log.items():
        if rec.get("graded") or rec.get("key") not in mk.ent:
            continue
        q = mk.ent[rec["key"]]["q"]
        P, S, NP = q.get("periods") or [], q.get("sales") or [], q.get("net_profit") or []
        if rec["quarter"] not in P:
            continue
        t = P.index(rec["quarter"])
        L = lag_of(P) or 4
        g = _yoy(S[t], S[t - L]) if t >= L else None
        if g is None:
            continue
        filed = mk.result_filed(rec["key"], qend(rec["quarter"])) or f"{rec.get('date')}T00:00:00"
        pxr = mk.prices(rec["key"])
        i = reaction_index(pxr, filed) if pxr else None
        ex = (pxr[i][1] / pxr[i - 1][1] - 1) * 100 if i else None      # the stock's own result-day move
        rec.update({"graded": True, "actual_g": round(g, 1), "accel": int(g > rec["last_g"]),
                    "profit_up": int(NP[t] > NP[t - L]) if NP[t] is not None and NP[t - L] and NP[t - L] > 0 else None,
                    "react": round(ex, 2) if ex is not None else None, "rday": pxr[i][0] if i else None})
        rec["react_up"] = None if rec["react"] is None else int(rec["react"] > 0)
        newly.append(rec)
    graded = [r for r in log.values() if r.get("graded")]
    scorecard = {"accel": _summary(graded, "p_accel", "accel"), "profit": _summary(graded, "p_profit", "profit_up"),
                 "react": _summary(graded, "p_react", "react_up"),
                 "rows": sorted(graded, key=lambda r: r.get("date") or "", reverse=True)[:400]}

    # ---- the overnight read: results filed out of market hours, scored on what was actually reported
    after_rows = []
    for r in rows:
        if r.get("react") is None or not r.get("after"):
            continue
        q = mk.ent[r["key"]]["q"]
        t = (q.get("periods") or []).index(r["q"])
        r["a"] = actual_features(q, t, r["f"]["g1"])
        after_rows.append(r)
    pres = []
    for qq in [q for q in qs if q in {r["q"] for r in after_rows}][2:]:
        cut = qend(qq) - timedelta(days=80)
        m = fit_post([r for r in after_rows if r["qe"] < cut])
        test = [r for r in after_rows if r["q"] == qq]
        if m is None or not test:
            continue
        pres += list(zip([float(x) for x in m.predict_proba(_XP(test))[:, 1]], test))
    pdist = post_dist(pres)
    post_bt = {"n": len(pres), "auc": _auc([p for p, _ in pres], [int(r["react"] > 0) for _, r in pres]),
               "base": round(100 * sum(r["react"] > 0 for _, r in pres) / len(pres), 1) if pres else None,
               "pre_auc": backtest["react"].get("auc"), "fifths": post_fifths(pres), "bins": pdist}
    backtest["post"] = post_bt
    reads = (_load(READ_FILE, {}) or {}).get("reads") or {}
    backtest["claude"] = claude_record(rows, pres, reads)
    # the stocks behind every backtest table: what was predicted (walk-forward, before it happened) and what happened
    def _r(v, n=2):
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, n)
    nm = lambda k: (mk.ent[k]["symbol"], mk.ent[k]["name"], bool(mk.ent[k].get("code")))
    pre_rows = [{"s": nm(r["key"])[0], "n": nm(r["key"])[1], "b": nm(r["key"])[2], "q": r["q"], "d": r.get("rday") or r.get("rd"),
                 "pp": _r(r["p_profit"], 3), "pa": _r(r["p_accel"], 3), "pr": _r(r["p_react"], 3),
                 "pu": r.get("profit_up"), "ac": r.get("accel"), "g": _r(r.get("g"), 1), "mv": _r(r.get("react")), "sw": _r(r.get("swing"))}
                for r in bt]
    post_rows = [{"s": nm(r["key"])[0], "n": nm(r["key"])[1], "b": nm(r["key"])[2], "q": r["q"], "d": r.get("rday"), "f": r.get("filed"),
                  "p": _r(p, 3), "g": _r(r["a"].get("_g"), 1), "pg": _r(r["a"].get("_pg"), 1), "mv": _r(r.get("react")),
                  "gap": _r(r.get("gap")), "oc": _r(r.get("oc")), "o3": _r(r.get("o3"))} for p, r in pres]
    BT_ROWS_FILE.write_text(json.dumps({"asof": today.isoformat(), "pre": pre_rows, "post": post_rows}, separators=(",", ":"),
                                       ensure_ascii=False), encoding="utf-8")
    pm = fit_post(after_rows)
    reported = []
    since3 = (today - timedelta(days=4)).isoformat()
    for key, e in mk.ent.items():
        P = e["q"].get("periods") or []
        if not standard(P) or not key.startswith("NSE:"):
            continue
        filed = mk.result_filed(key, qend(P[-1]))
        if not filed or filed[:10] < since3:
            continue
        t = len(P) - 1
        f = features(mk, ix, key, t, qend(P[-1]), filed[:10], peers)
        if not f or pm is None:
            continue
        add_history(f, hist.get(key, []), filed[:10])
        a = actual_features(e["q"], t, f["g1"])
        p = float(pm.predict_proba(_XP([{"f": f, "a": a}]))[0][1])
        rd = post_read(p, a, pdist)
        px = mk.prices(key)
        i = reaction_index(px, filed) if px else None
        res = None                                   # reaction_index is None until that session is in the data
        if i is not None and i > 0:
            res = {"date": px[i][0], "move": round((px[i][1] / px[i - 1][1] - 1) * 100, 2),
                   "gap": round((px[i][3] / px[i - 1][1] - 1) * 100, 2) if px[i][3] else None}
        tm = filed[11:16]
        reported.append({"key": key, "code": e.get("code"), "symbol": e["symbol"], "name": e["name"], "board": bool(e.get("code")),
                         "sector": (mk.sector.get(key) or "").replace("-", " "), "quarter": P[-1], "half": lag_of(P) == 2, "filed": filed,
                         "in_hours": "09:00" <= tm < "15:30", "p_up": round(p, 3),
                         "sales_g": None if a["_g"] is None else round(a["_g"], 1), "last_g": round(f["g1"], 1),
                         "profit_g": None if a["_pg"] is None else round(a["_pg"], 1), "np": a["_np"], "np0": a["_np0"],
                         "margin_ch": None if a["_om"] is None else round(a["_om"], 1),
                         "verdict": rd["verdict"], "tone": rd["tone"], "action": rd["action"],
                         "exp": {k: rd["bin"].get(k) for k in ("up", "gap", "oc", "o3", "n")} if rd.get("bin") else None,
                         "claude": {k: (reads.get(f"{key[4:]}|{filed}") or {}).get(k) for k in
                                    ("verdict", "p_up", "summary", "positives", "negatives", "quality_flags", "revenue_yoy_pct", "profit_yoy_pct", "basis")}
                         if reads.get(f"{key[4:]}|{filed}") else None,
                         "result": res})
    reported.sort(key=lambda r: r["filed"], reverse=True)
    plog = _load(POST_LOG, {})
    for r in reported:
        lk = f"{r['key']}|{r['quarter']}"
        rec = plog.setdefault(lk, {k: r[k] for k in ("key", "symbol", "name", "board", "quarter", "filed", "p_up", "verdict", "tone",
                                                    "sales_g", "profit_g")})
        if r.get("result") and not rec.get("result"):
            rec["result"] = r["result"]
            rec["hit"] = None if not rec["tone"] else (r["result"]["move"] > 0) == (rec["tone"] == "up")
    POST_LOG.write_text(json.dumps(plog, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    called = [x for x in plog.values() if x.get("result") and x.get("tone")]
    post_bt["live"] = {"graded": sum(1 for x in plog.values() if x.get("result")), "called": len(called),
                       "right": sum(1 for x in called if x.get("hit")),
                       "rows": sorted([x for x in plog.values() if x.get("result")], key=lambda x: x["filed"], reverse=True)[:200]}

    pd = None
    try:
        from . import pead
        pd = pead.build(mk, ix, rows, today)
        PEAD_FILE.write_text(json.dumps(pd, separators=(",", ":"), ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - the strategy page must never break the nightly read
        print(f"results preview: PEAD failed ({type(e).__name__}: {e})")
    write_alerts(today, upcoming, newly, reported, pd)
    out = {"asof": today.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "upcoming": upcoming, "scorecard": scorecard, "backtest": backtest, "recent": recent, "reported": reported,
           "pending": sum(1 for r in log.values() if not r.get("graded")),
           "inputs": {"companies": len(mk.ent), "board": sum(1 for e in mk.ent.values() if e.get("code")),
                      "training_rows": len(rows), "with_guidance": sum(1 for c in mk.guidance.values() if c.get("statements")),
                      "calls_read": sum(len(v) for v in mk.calls.values()), "order_values": sum(len(v) for v in mk.orders.values()),
                      "commodities": len(mk.com), "with_prices": sum(1 for r in rows if r.get("react") is not None)}}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False, default=str), encoding="utf-8")
    PRED_LOG.write_text(json.dumps(log, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return out
