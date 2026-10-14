"""Results Preview: what each board company's next quarterly result is likely to show, why, and how often
this has been right.

For every company with results due soon (data/results_calendar_in), a small model gives:
  - the chance sales growth speeds up against last quarter's year-on-year growth ("accelerates")
  - the chance net profit is higher than the same quarter last year
  - an expected year-on-year sales growth figure
and the reasons behind it, from information that existed before the quarter ended:
  - last quarter's growth and its trailing-4-quarter trend (a quarter well below its trend tends to bounce back)
  - management's revenue-growth guidance for the fiscal year (data/guidance_in, from earnings calls)
  - new capacity: commissioning / capacity-addition filings 2-12 months before the quarter ended
  - order inflow: order-win filings in the two quarters before, valued from the filing PDFs, as a share of a
    year's sales (the Deals history, app/disclosures_archive.py, table "orders")
  - margins and profit: last quarter's operating-margin change and profit growth

The model is a plain logistic regression (and a linear one for the growth figure), refitted every night on
every company-quarter already reported. The backtest is walk-forward: each quarter is predicted by a model
fitted only on the quarters before it, then compared with what was reported and with the stock's move around
the result date against the market (equal-weight board index).

Every live prediction is logged (data/results_preview/predictions.json) and graded once the result is out,
so the scorecard is a true out-of-sample record. A research preview, not a buy signal: backtests of order wins,
sector breadth and this model found no dependable trading edge -- see the backtest numbers on the page.
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import charts

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA = BACKEND_DIR / "data"
OUT_DIR = DATA / "results_preview"
LATEST = OUT_DIR / "latest.json"
PRED_LOG = OUT_DIR / "predictions.json"
UPCOMING_DAYS = 21          # companies reporting in the next three weeks
MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
FEATURES = ["g1", "trail", "gap", "has_trail", "guide_gap", "has_guide", "cap", "order_pct", "has_order",
            "opm_ch", "pg1", "base"]


# ------------------------------------------------------------------ helpers
def qend(label: str) -> date:
    m, y = label.split()
    m, y = MON[m], int(y)
    return date(y, m, 30 if m in (6, 9) else 31)


def next_label(label: str) -> str:
    qe = qend(label) + timedelta(days=92)
    return f"{[k for k, v in MON.items() if v == qe.month][0]} {qe.year}"


def _yoy(a, b):
    if a is None or b is None or b <= 0:
        return None
    return (a / b - 1) * 100


def _clip(v, lo=-100.0, hi=150.0):
    return None if v is None else max(lo, min(hi, v))


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ------------------------------------------------------------------ inputs
class Inputs:
    def __init__(self):
        self.results = _load(DATA / "results_in" / "latest.json", {}).get("companies") or {}
        self.guidance = _load(DATA / "guidance_in" / "latest.json", {}).get("companies") or {}
        raw = _load(DATA / "companies_raw.json", [])
        self.nse = {str(c["code"]): str(c.get("nse_code") or "").upper() for c in raw if c.get("code")}
        self.names = {str(c["code"]): c.get("name") or str(c["code"]) for c in raw if c.get("code")}
        self.cap, self.orders, self.res_dates = defaultdict(list), defaultdict(list), defaultdict(list)
        try:
            from . import disclosures_archive as A
            path = A.archive_path()
        except Exception:  # noqa: BLE001 - the model still runs on results and guidance alone
            path = None
        if path:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
            try:
                for sym, d, s in con.execute("SELECT symbol, date, summary FROM ann WHERE kind='capacity or plant' OR category IN "
                                             "('Commencement of commercial production/operations','Capacity addition')"):
                    self.cap[sym].append((d, s))
                for sym, d, v in con.execute("SELECT symbol, date, order_cr FROM orders WHERE not_order=0 AND order_cr IS NOT NULL"):
                    self.orders[sym].append((d, v))
                for sym, d, cat in con.execute("SELECT symbol, date, category FROM ann WHERE kind='results' AND category IN "
                                               "('Financial Result Updates','Outcome of Board Meeting')"):
                    self.res_dates[sym].append((d, cat))
            finally:
                con.close()

    def result_date(self, code: str, qe: date) -> str | None:
        sym = self.nse.get(code)
        lo, hi = (qe + timedelta(days=1)).isoformat(), (qe + timedelta(days=75)).isoformat()
        rows = self.res_dates.get(sym or "", [])
        fr = sorted(d for d, c in rows if lo <= d <= hi and c == "Financial Result Updates")
        return (fr or sorted(d for d, c in rows if lo <= d <= hi) or [None])[0]

    def guided(self, code: str, qe: date):
        """The latest fiscal-year revenue-growth guidance for the year holding this quarter, said before it ended."""
        fy = qe.year + (1 if qe.month > 3 else 0)
        best = None
        for s in (self.guidance.get(code) or {}).get("statements", []):
            if s.get("metric") != "revenue_growth" or (s.get("horizon") or {}).get("fy") != fy or s.get("call", "9") >= qe.isoformat()[:7]:
                continue
            v = ((s.get("low") or 0) + (s.get("high") or s.get("low") or 0)) / 2
            if best is None or s["call"] > best[0]:
                best = (s["call"], v, s.get("quote") or "")
        return best


def features(inp: Inputs, code: str, t: int, qe: date) -> dict | None:
    """Everything known before quarter t (ending qe) closed, for company `code`; t indexes its quarter lists
    (t may be one past the end: the quarter not yet reported)."""
    q = (inp.results.get(code) or {}).get("quarters") or {}
    S, NP, OPM = q.get("sales") or [], q.get("net_profit") or [], q.get("opm") or []
    if t < 5 or t - 1 >= len(S):
        return None
    g1 = _yoy(S[t - 1], S[t - 5])
    if g1 is None:
        return None
    trail = _yoy(sum(S[t - 4:t]), sum(S[t - 8:t - 4])) if t >= 8 else None
    base = _yoy(S[t - 4], S[t - 8]) if t >= 8 else None
    pg1 = _yoy(NP[t - 1], NP[t - 5]) if NP[t - 5] and NP[t - 5] > 0 else None
    opm_ch = (OPM[t - 1] - OPM[t - 5]) if OPM[t - 1] is not None and OPM[t - 5] is not None else None
    sym = inp.nse.get(code) or ""
    g = inp.guided(code, qe)
    lo12, hi2 = (qe - timedelta(days=365)).isoformat(), (qe - timedelta(days=60)).isoformat()
    caps = [(d, s) for d, s in inp.cap.get(sym, []) if lo12 < d <= hi2]
    six = (qe - timedelta(days=182)).isoformat()
    ords = [(d, v) for d, v in inp.orders.get(sym, []) if six < d <= qe.isoformat()]
    yr_sales = sum(x for x in S[max(0, t - 4):t] if x)
    order_cr = sum(v for _, v in ords)
    order_pct = 100 * order_cr / yr_sales if ords and yr_sales > 0 else None
    return {
        "g1": _clip(g1), "trail": _clip(trail) if trail is not None else 0.0, "has_trail": 1.0 if trail is not None else 0.0,
        "gap": _clip(g1 - trail, -80, 80) if trail is not None else 0.0,
        "guide": g[1] if g else None, "guide_quote": g[2] if g else None, "guide_call": g[0] if g else None,
        "guide_gap": _clip(g[1] - g1, -80, 80) if g else 0.0, "has_guide": 1.0 if g else 0.0,
        "cap": 1.0 if caps else 0.0, "cap_items": caps[-3:],
        "order_pct": min(order_pct, 200.0) if order_pct is not None else 0.0, "has_order": 1.0 if ords else 0.0,
        "order_cr": order_cr, "order_n": len(ords),
        "opm_ch": _clip(opm_ch, -30, 30) if opm_ch is not None else 0.0, "opm_last": OPM[t - 1] if t - 1 < len(OPM) else None,
        "pg1": _clip(pg1, -100, 200) if pg1 is not None else 0.0, "base": _clip(base) if base is not None else 0.0,
        "g1_raw": g1, "trail_raw": trail,
    }


def standard(P: list[str]) -> bool:
    """Quarters ending Mar/Jun/Sep/Dec, one after another -- "the same quarter last year" is 4 back only then."""
    return bool(P) and all(p.split()[0] in ("Mar", "Jun", "Sep", "Dec") for p in P) and         all((qend(b) - qend(a)).days in range(88, 95) for a, b in zip(P, P[1:]))


def history(inp: Inputs) -> list[dict]:
    """Every reported company-quarter with its features and outcomes."""
    out = []
    for code, c in inp.results.items():
        q = c.get("quarters") or {}
        P, S, NP = q.get("periods") or [], q.get("sales") or [], q.get("net_profit") or []
        if not standard(P):
            continue
        for t in range(5, len(P)):
            qe = qend(P[t])
            f = features(inp, code, t, qe)
            g = _yoy(S[t], S[t - 4])
            if not f or g is None:
                continue
            pg = (NP[t] > NP[t - 4]) if NP[t] is not None and NP[t - 4] is not None and NP[t - 4] > 0 else None
            out.append({"code": code, "q": P[t], "qe": qe, "f": f, "g": _clip(g), "accel": 1 if g > f["g1_raw"] else 0,
                        "profit_up": None if pg is None else int(pg), "rd": inp.result_date(code, qe)})
    return out


# ------------------------------------------------------------------ models (plain python)
def _vec(f: dict, mu: dict, sd: dict) -> list[float]:
    return [1.0] + [((f[k] or 0.0) - mu[k]) / sd[k] for k in FEATURES]


def _norm(rows: list[dict]):
    mu = {k: statistics.mean(r["f"][k] or 0.0 for r in rows) for k in FEATURES}
    sd = {k: (statistics.pstdev([r["f"][k] or 0.0 for r in rows]) or 1.0) for k in FEATURES}
    return mu, sd


def fit_logit(rows: list[dict], target: str, l2: float = 1.0, iters: int = 300) -> dict | None:
    rows = [r for r in rows if r.get(target) is not None]
    if len(rows) < 80:
        return None
    mu, sd = _norm(rows)
    X = [_vec(r["f"], mu, sd) for r in rows]
    y = [r[target] for r in rows]
    w = [0.0] * len(X[0])
    n, lr = len(X), 0.5
    for _ in range(iters):
        grad = [0.0] * len(w)
        for xi, yi in zip(X, y):
            z = sum(a * b for a, b in zip(w, xi))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            for j, xv in enumerate(xi):
                grad[j] += (p - yi) * xv
        for j in range(len(w)):
            w[j] -= lr * (grad[j] / n + (l2 * w[j] / n if j else 0))
    return {"w": w, "mu": mu, "sd": sd}


def p_logit(m: dict, f: dict) -> float:
    z = sum(a * b for a, b in zip(m["w"], _vec(f, m["mu"], m["sd"])))
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def fit_linear(rows: list[dict], ridge: float = 5.0) -> dict | None:
    """Expected growth = last quarter's growth + a predicted change; the change is fitted capped at +/-30 pts so a
    few huge swings (a tiny base quarter, an acquisition) don't steer it."""
    if len(rows) < 80:
        return None
    mu, sd = _norm(rows)
    X = [_vec(r["f"], mu, sd) for r in rows]
    y = [max(-30.0, min(30.0, r["g"] - r["f"]["g1"])) for r in rows]
    k = len(X[0])
    A = [[sum(X[i][a] * X[i][b] for i in range(len(X))) + (ridge if a == b and a else 0) for b in range(k)] for a in range(k)]
    v = [sum(X[i][a] * y[i] for i in range(len(X))) for a in range(k)]
    for c in range(k):                                    # Gauss-Jordan
        piv = max(range(c, k), key=lambda r: abs(A[r][c]))
        A[c], A[piv], v[c], v[piv] = A[piv], A[c], v[piv], v[c]
        if abs(A[c][c]) < 1e-12:
            continue
        for r in range(k):
            if r != c:
                fac = A[r][c] / A[c][c]
                A[r] = [a - fac * b for a, b in zip(A[r], A[c])]
                v[r] -= fac * v[c]
    w = [v[i] / A[i][i] if abs(A[i][i]) > 1e-12 else 0.0 for i in range(k)]
    return {"w": w, "mu": mu, "sd": sd}


def p_linear(m: dict, f: dict) -> float:
    return f["g1"] + sum(a * b for a, b in zip(m["w"], _vec(f, m["mu"], m["sd"])))


# ------------------------------------------------------------------ prices: the move around a result, against the market
class Prices:
    def __init__(self, codes):
        self.px = {}
        daily = defaultdict(list)
        for c in codes:
            s = charts.load_series(c) or {}
            rows = [(r[0], r[4]) for r in s.get("rows") or [] if r[4]]
            self.px[c] = rows
            for i in range(1, len(rows)):
                daily[rows[i][0]].append(rows[i][1] / rows[i - 1][1] - 1)
        self.lvl, acc = {}, 1.0
        for d in sorted(d for d in daily if len(daily[d]) >= 100):
            acc *= 1 + statistics.median(daily[d])
            self.lvl[d] = acc

    def excess(self, code, d, before, after):
        rows = self.px.get(code) or []
        idx = next((i for i, (dd, _) in enumerate(rows) if dd >= d), None)
        if idx is None or idx - before < 0 or idx + after >= len(rows):
            return None
        a, b = rows[idx - before], rows[idx + after]
        la, lb = self.lvl.get(a[0]), self.lvl.get(b[0])
        if not (la and lb):
            return None
        return round(((b[1] / a[1]) - (lb / la)) * 100, 2)


# ------------------------------------------------------------------ reasons a reader can check
def drivers(f: dict, inp: Inputs, code: str) -> list[dict]:
    out = []
    g1, tr = f["g1_raw"], f["trail_raw"]
    out.append({"k": "trend", "tone": "up" if g1 > 15 else "dn" if g1 < 0 else "",
                "text": f"Last quarter sales {g1:+.0f}% YoY" + (f" (trailing year {tr:+.0f}%)" if tr is not None else "")})
    if tr is not None and g1 < tr - 10:
        out.append({"k": "bounce", "tone": "up", "text": f"Last quarter ran {tr - g1:.0f} pts below its own trend — tends to bounce back"})
    elif tr is not None and g1 > tr + 15:
        out.append({"k": "fade", "tone": "dn", "text": f"Last quarter ran {g1 - tr:.0f} pts above its own trend — hard to repeat"})
    if f["guide"] is not None:
        tone = "up" if f["guide"] > g1 + 5 else "dn" if f["guide"] < g1 - 5 else ""
        out.append({"k": "guide", "tone": tone, "text": f"Management guided ~{f['guide']:.0f}% revenue growth for the year ({f['guide_call']} call)",
                    "quote": (f.get("guide_quote") or "")[:220]})
    if f["cap"]:
        last = f["cap_items"][-1]
        out.append({"k": "capacity", "tone": "up", "text": f"New capacity / commissioning filed {last[0]} — revenue from it can start showing",
                    "quote": (last[1] or "")[:200]})
    if f["has_order"]:
        out.append({"k": "orders", "tone": "up" if f["order_pct"] >= 10 else "",
                    "text": f"{f['order_n']} order win{'s' if f['order_n'] > 1 else ''} worth ₹{f['order_cr']:,.0f} cr in the 6 months before — {f['order_pct']:.0f}% of a year's sales"})
    if abs(f["opm_ch"]) >= 2:
        out.append({"k": "margin", "tone": "up" if f["opm_ch"] > 0 else "dn",
                    "text": f"Operating margin {'up' if f['opm_ch'] > 0 else 'down'} {abs(f['opm_ch']):.1f} pts YoY last quarter"})
    return out


# ------------------------------------------------------------------ the nightly run
def _summary(rows: list[dict], pkey: str, ykey: str) -> dict:
    rows = [r for r in rows if r.get(ykey) is not None and r.get(pkey) is not None]
    if not rows:
        return {"n": 0}
    hits = sum(1 for r in rows if (r[pkey] >= 0.5) == bool(r[ykey]))
    buckets = []
    for lo, hi in ((0, .35), (.35, .5), (.5, .65), (.65, 1.01)):
        b = [r for r in rows if lo <= r[pkey] < hi]
        if b:
            buckets.append({"range": f"{int(lo * 100)}–{min(100, int(hi * 100))}%", "n": len(b),
                            "actual": round(100 * sum(r[ykey] for r in b) / len(b), 1)})
    base = 100 * sum(r[ykey] for r in rows) / len(rows)
    return {"n": len(rows), "hit": round(100 * hits / len(rows), 1), "base": round(base, 1),
            "always_majority": round(max(base, 100 - base), 1), "buckets": buckets}


def _trade(rows: list[dict], key: str) -> list[dict]:
    """Stocks split by the predicted chance into thirds, per quarter; their move vs the market."""
    out = []
    for name, sel in (("Top third (most likely)", 2), ("Middle third", 1), ("Bottom third (least likely)", 0)):
        xs, rs = [], []
        for qq in {r["q"] for r in rows}:
            grp = sorted([r for r in rows if r["q"] == qq and r.get(key) is not None], key=lambda r: r[key])
            if len(grp) < 9:
                continue
            third = grp[sel * len(grp) // 3:(sel + 1) * len(grp) // 3]
            xs += [r["swing"] for r in third if r.get("swing") is not None]
            rs += [r["react"] for r in third if r.get("react") is not None]
        if xs:
            out.append({"group": name, "n": len(xs), "swing_median": round(statistics.median(xs), 2),
                        "swing_mean": round(statistics.mean(xs), 2), "swing_beat": round(100 * sum(x > 0 for x in xs) / len(xs)),
                        "react_median": round(statistics.median(rs), 2) if rs else None})
    return out


def run() -> dict:
    inp = Inputs()
    hist = history(inp)
    qs = sorted({r["q"] for r in hist}, key=qend)
    prices = Prices(list(inp.results))

    # ---- walk-forward backtest: quarter Q predicted by models fitted on quarters reported before it
    bt = []
    for qq in qs[3:]:
        train = [r for r in hist if qend(r["q"]) < qend(qq) - timedelta(days=80)]   # reported before Q ended
        ma, mp, ml = fit_logit(train, "accel"), fit_logit(train, "profit_up"), fit_linear(train)
        if not (ma and mp and ml):
            continue
        for r in hist:
            if r["q"] != qq:
                continue
            row = {"q": qq, "code": r["code"], "p_accel": p_logit(ma, r["f"]), "p_profit": p_logit(mp, r["f"]),
                   "exp_g": p_linear(ml, r["f"]), "g": r["g"], "accel": r["accel"], "profit_up": r["profit_up"], "rd": r["rd"]}
            if r["rd"]:
                row["swing"] = prices.excess(r["code"], r["rd"], 5, 3)    # 5 sessions before -> 3 after
                row["react"] = prices.excess(r["code"], r["rd"], 1, 1)    # day before -> day after
            bt.append(row)
    naive_err = [abs(r["g"] - next(h["f"]["g1"] for h in hist if h["code"] == r["code"] and h["q"] == r["q"])) for r in bt]
    model_err = [abs(r["g"] - r["exp_g"]) for r in bt]
    backtest = {
        "quarters": sorted({r["q"] for r in bt}, key=qend), "n": len(bt),
        "accel": _summary(bt, "p_accel", "accel"), "profit": _summary(bt, "p_profit", "profit_up"),
        "growth_error": {"model_median": round(statistics.median(model_err), 1) if model_err else None,
                         "naive_median": round(statistics.median(naive_err), 1) if naive_err else None},
        "trade_accel": _trade(bt, "p_accel"), "trade_profit": _trade(bt, "p_profit"),
        "trade_all": {"n": sum(1 for r in bt if r.get("swing") is not None),
                      "swing_median": round(statistics.median([r["swing"] for r in bt if r.get("swing") is not None]), 2)
                      if any(r.get("swing") is not None for r in bt) else None},
    }

    # ---- live predictions for companies reporting soon (models fitted on everything reported)
    ma, mp, ml = fit_logit(hist, "accel"), fit_logit(hist, "profit_up"), fit_linear(hist)
    cal = _load(DATA / "results_calendar_in" / "latest.json", {}).get("items") or []
    today = datetime.now(timezone.utc).date()
    log = _load(PRED_LOG, {})
    upcoming = []
    for it in cal:
        code = str(it.get("code") or "")
        if not code or not it.get("date") or "half" in str(it.get("result") or "").lower():
            continue
        d = date.fromisoformat(it["date"])
        q = (inp.results.get(code) or {}).get("quarters") or {}
        P = q.get("periods") or []
        if not standard(P) or not (today - timedelta(days=1) <= d <= today + timedelta(days=UPCOMING_DAYS)):
            continue
        target = next_label(P[-1])
        if qend(target) >= d:            # the quarter being reported must have ended before the result date
            continue
        f = features(inp, code, len(P), qend(target))
        if not f or not (ma and mp and ml):
            continue
        pa, pp, eg = p_logit(ma, f), p_logit(mp, f), p_linear(ml, f)
        conf = max(abs(pa - .5), abs(pp - .5))
        rec = {"code": code, "symbol": inp.nse.get(code), "name": inp.names.get(code, code), "quarter": target,
               "date": it["date"], "status": it.get("status"), "last_g": round(f["g1_raw"], 1),
               "exp_g": round(eg, 1), "p_accel": round(pa, 3), "p_profit": round(pp, 3),
               "confidence": "high" if conf >= .2 else "medium" if conf >= .1 else "low",
               "drivers": drivers(f, inp, code)}
        upcoming.append(rec)
        key = f"{code}|{target}"
        if key not in log or (log[key].get("date") or "") >= today.isoformat():   # keep updating until the result day
            log[key] = {**{k: rec[k] for k in ("code", "symbol", "name", "quarter", "date", "last_g", "exp_g", "p_accel", "p_profit", "confidence")},
                        "made": today.isoformat()}
    upcoming.sort(key=lambda r: (r["date"], -max(abs(r["p_accel"] - .5), abs(r["p_profit"] - .5))))

    # ---- grade logged predictions whose result is now in
    for key, rec in log.items():
        if rec.get("graded"):
            continue
        q = (inp.results.get(rec["code"]) or {}).get("quarters") or {}
        P, S, NP = q.get("periods") or [], q.get("sales") or [], q.get("net_profit") or []
        if rec["quarter"] not in P:
            continue
        t = P.index(rec["quarter"])
        g = _yoy(S[t], S[t - 4]) if t >= 4 else None
        if g is None:
            continue
        rec.update({"graded": True, "actual_g": round(g, 1), "accel": int(g > rec["last_g"]),
                    "profit_up": int(NP[t] > NP[t - 4]) if NP[t] is not None and NP[t - 4] and NP[t - 4] > 0 else None})
        rd = inp.result_date(rec["code"], qend(rec["quarter"])) or rec.get("date")
        rec["react"] = prices.excess(rec["code"], rd, 1, 1)
        rec["swing"] = prices.excess(rec["code"], rd, 5, 3)
    graded = [r for r in log.values() if r.get("graded")]
    scorecard = {"accel": _summary(graded, "p_accel", "accel"), "profit": _summary(graded, "p_profit", "profit_up"),
                 "rows": sorted(graded, key=lambda r: r.get("date") or "", reverse=True)[:300]}

    out = {"asof": today.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "upcoming": upcoming, "scorecard": scorecard, "backtest": backtest, "pending": sum(1 for r in log.values() if not r.get("graded")),
           "inputs": {"companies": len(inp.results), "with_guidance": sum(1 for c in inp.guidance.values() if c.get("statements")),
                      "order_values": sum(len(v) for v in inp.orders.values()), "training_quarters": len(hist)}}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False, default=str), encoding="utf-8")
    PRED_LOG.write_text(json.dumps(log, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    return out
