"""Backtest: does sector breadth (how many of a sector's stocks are rising together) predict returns?

    python scripts/backtest_sector_strength.py --bundle DIR --idx DIR --out DIR [--events events.csv]

Research only -- nothing here feeds the dashboard. It answers one question before the Sector Strength tab
is built (SECTOR_STRENGTH_SPEC.md, section 8): after a sector shows strong breadth over the last 1-20
trading days, do its leading stocks (or the sector as a whole) beat the market over the next 5/10/20
trading days, after costs?

Data
  --bundle  the universe candle bundle (universe-charts.tar.gz from the `charts` release, extracted):
            split/bonus-adjusted daily OHLCV for every actively traded NSE stock, ~3 years.
            Survivorship: stocks delisted since are missing, which flatters every bucket about equally.
  --idx     NSE index constituent lists (niftyindices.com): Nifty Total Market + Microcap 250 give each
            stock's NSE industry; Nifty India Defence lists the defence names.
  --events  optional: order-win filings (symbol,date) from the order-win backtest, for the
            "strong sector + order win" vs "order win alone" comparison.

Method (as the spec, with the benchmark the order-win backtest used)
  * Liquid: 20-day average turnover >= Rs 1 crore and traded on >= 18 of the last 20 sessions.
  * Benchmark: the median return of all liquid NSE stocks over the same window (no index file is on
    hand; an equal-weight median is the fairer yardstick for equal-weight picks anyway).
  * Sector metrics per date and window W: % of liquid members up > 1%, median return minus benchmark,
    % above the 20-day average, % at a 20-day high, turnover vs its 20-day average.
  * Strength score 0-100: each metric's percentile within the sector's own last 250 days (min 60),
    weighted 30/30/15/15/10. Buckets: Strong >= 70, Weak <= 30, Neutral between.
  * Stock score inside a sector: 0.4 pctile(rel 5d) + 0.3 pctile(rel 20d) + 15 if above 20DMA + 15 if
    at a 20-day high. The top 5 are "the leaders".
  * Trade: enter at the NEXT day's open, exit at the close h trading days after the signal day;
    excess = return - median return of all liquid stocks over the same open-to-close span - 0.3% cost.
  * Overlap: a signal fires every day, so consecutive trades overlap. Significance uses one
    non-overlapping sample (every h-th signal day); consistency uses calendar quarters.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

WINDOWS = [1, 2, 3, 5, 10, 20]
HORIZONS = [5, 10, 20]
COST = 0.003
MIN_TURNOVER_CR = 1.0
MIN_SESSIONS = 18
LOOKBACK, MIN_HISTORY = 250, 60
WEIGHTS = {"pct_up_gt_1": 0.30, "rel_ret": 0.30, "pct_above_20dma": 0.15, "pct_20d_high": 0.15, "turnover_ratio": 0.10}
TOP_N = 5
RAILWAYS = ["RVNL", "IRCON", "IRCTC", "IRFC", "RAILTEL", "RITES", "TITAGARH", "JWL", "TEXRAIL", "BEML", "CONCOR",
            "HBLENGINE", "KERNEX", "ORIENTRAIL", "RKEC", "KEC", "SIEMENS", "MEDHA", "SHRIRAMPPS", "GPTINFRA", "PATELENG"]
RENEWABLE = r"solar|wind|renew|green|waaree|premier energ|suzlon|inox|websol|borosil renew"


def load_prices(bundle: str):
    o, c, v = {}, {}, {}
    for f in glob.glob(os.path.join(bundle, "NSE-*.json")):
        d = json.load(open(f, encoding="utf-8"))
        rows = d.get("rows") or []
        if len(rows) < 80:
            continue
        sym = d["symbol"]
        idx = pd.to_datetime([r[0] for r in rows])
        o[sym] = pd.Series([r[1] for r in rows], index=idx)
        c[sym] = pd.Series([r[4] for r in rows], index=idx)
        v[sym] = pd.Series([r[5] for r in rows], index=idx)
    O, C, V = pd.DataFrame(o).sort_index(), pd.DataFrame(c).sort_index(), pd.DataFrame(v).sort_index()
    # a session where almost nothing traded is a data gap, not a market day
    keep = C.notna().sum(axis=1) >= 0.5 * C.notna().sum(axis=1).median()
    return O[keep], C[keep], V[keep]


def sectors(idx: str, C: pd.DataFrame) -> dict[str, list[str]]:
    rows = []
    for f in ("ind_niftytotalmarket_list.csv", "ind_niftymicrocap250_list.csv"):
        rows += list(csv.DictReader(open(os.path.join(idx, f), encoding="utf-8-sig")))
    ind = {r["Symbol"].strip(): (r["Industry"].strip(), r["Company Name"].strip()) for r in rows}
    defence = {r["Symbol"].strip() for r in csv.DictReader(open(os.path.join(idx, "ind_niftyindiadefence_list.csv"), encoding="utf-8-sig"))}
    try:   # the board's own Defence & Aerospace theme widens the list beyond the index's 22
        board = json.load(open(Path(__file__).resolve().parent.parent / "data" / "companies_raw.json", encoding="utf-8"))
        defence |= {str(b.get("nse_code") or b["code"]) for b in board if (b.get("theme") or "").startswith("Defence")}
    except (OSError, ValueError):
        pass
    import re
    out = {
        "Defence & Aerospace": sorted(defence),
        "Capital Goods / Engineering": sorted(s for s, (i, n) in ind.items() if i == "Capital Goods" and s not in defence
                                             and not re.search(RENEWABLE, n, re.I)),
        "Infrastructure & Construction": sorted(s for s, (i, _) in ind.items() if i in ("Construction", "Construction Materials")),
        "Power & Renewables": sorted(s for s, (i, n) in ind.items() if i == "Power" or (i == "Capital Goods" and re.search(RENEWABLE, n, re.I))),
        "Railways": sorted(RAILWAYS),
    }
    return {k: [s for s in v if s in C.columns] for k, v in out.items()}


def pctile_hist(s: pd.Series) -> pd.Series:
    """Percentile (0-100) of each value within that series' own trailing LOOKBACK values."""
    def f(x):
        h = x[:-1][~np.isnan(x[:-1])]
        if np.isnan(x[-1]) or len(h) < MIN_HISTORY:
            return np.nan
        return 100.0 * ((h < x[-1]).sum() + 0.5 * (h == x[-1]).sum()) / len(h)
    return s.rolling(LOOKBACK + 1, min_periods=MIN_HISTORY + 1).apply(f, raw=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--idx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--events", default="")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    O, C, V = load_prices(a.bundle)
    print(f"prices: {C.shape[1]} NSE stocks, {C.shape[0]} sessions {C.index[0].date()} .. {C.index[-1].date()}")
    turn = C * V / 1e7
    traded = V.fillna(0) > 0
    liquid = (turn.rolling(20, min_periods=15).mean() >= MIN_TURNOVER_CR) & (traded.rolling(20).sum() >= MIN_SESSIONS)
    daily = C / C.shift(1) - 1
    bad = daily.abs() > 0.5                       # an unadjusted corporate action slipping through
    ma20 = C.rolling(20, min_periods=18).mean()
    above20 = C > ma20
    high20 = C >= C.rolling(20, min_periods=18).max()

    ret = {}
    for w in sorted(set(WINDOWS) | {5, 20}):
        r = C / C.shift(w) - 1
        r[bad.rolling(w, min_periods=1).max().astype(bool)] = np.nan
        ret[w] = r
    bench = {w: ret[w].where(liquid).median(axis=1) for w in ret}

    fwd, fbench = {}, {}
    for h in HORIZONS:
        f = C.shift(-h) / O.shift(-1) - 1
        f[bad.shift(-h).rolling(h, min_periods=1).max().fillna(False).astype(bool)] = np.nan
        fwd[h] = f
        fbench[h] = f.where(liquid).median(axis=1)

    secs = sectors(a.idx, C)
    for k, v in secs.items():
        print(f"  {k}: {len(v)} stocks with prices, {int(liquid[v].iloc[-1].sum())} liquid today")

    # ---- sector metrics and strength score
    recs = []
    for name, mem in secs.items():
        L = liquid[mem]
        tr = turn[mem].where(L).sum(axis=1)
        tr_ratio = tr / tr.rolling(20, min_periods=15).mean()
        p20 = above20[mem].where(L).mean(axis=1)
        h20 = high20[mem].where(L).mean(axis=1)
        for w in WINDOWS:
            r = ret[w][mem].where(L)
            n = r.notna().sum(axis=1)
            m = pd.DataFrame({
                "n": n, "pct_up": (r > 0).sum(axis=1) / n, "pct_up_gt_1": (r > 0.01).sum(axis=1) / n,
                "median_ret": r.median(axis=1), "rel_ret": r.median(axis=1) - bench[w],
                "pct_above_20dma": p20, "pct_20d_high": h20, "turnover_ratio": tr_ratio,
            })
            m.loc[m["n"] < 5] = np.nan
            score = sum(WEIGHTS[k] * pctile_hist(m[k]) for k in WEIGHTS)
            m["score"], m["sector"], m["window"] = score, name, w
            recs.append(m)
    S = pd.concat(recs).reset_index().rename(columns={"index": "date"})
    S["bucket"] = np.where(S["score"] >= 70, "Strong", np.where(S["score"] <= 30, "Weak", np.where(S["score"].notna(), "Neutral", None)))
    S.to_csv(out / "sector_daily.csv", index=False)

    # ---- leaders inside each sector, and their forward excess returns
    rel5, rel20 = ret[5].sub(bench[5], axis=0), ret[20].sub(bench[20], axis=0)
    trades = []
    for name, mem in secs.items():
        L = liquid[mem]
        r5, r20 = rel5[mem].where(L), rel20[mem].where(L)
        ss = 0.4 * r5.rank(axis=1, pct=True) * 100 + 0.3 * r20.rank(axis=1, pct=True) * 100 \
            + 15 * above20[mem].where(L).fillna(False).astype(float) + 15 * high20[mem].where(L).fillna(False).astype(float)
        ss = ss.where(r5.notna() & r20.notna())
        top = ss.apply(lambda row: list(row.dropna().nlargest(TOP_N).index), axis=1)
        for h in HORIZONS:
            ex = fwd[h][mem].sub(fbench[h], axis=0) - COST
            for dt, picks in top.items():
                if not picks:
                    continue
                e_top = ex.loc[dt, picks].dropna()
                e_all = ex.loc[dt].where(L.loc[dt]).dropna()
                if len(e_top):
                    trades.append({"date": dt, "sector": name, "h": h, "leaders": e_top.mean(), "leaders_n": len(e_top),
                                   "leaders_hit": (e_top > 0).mean(), "sector_all": e_all.mean() if len(e_all) else np.nan})
    T = pd.DataFrame(trades)
    J = T.merge(S[["date", "sector", "window", "score", "bucket", "pct_up"]], on=["date", "sector"])
    J = J[J["bucket"].notna()]
    J.to_csv(out / "trades.csv", index=False)

    # ---- summary
    lines = ["# Sector strength backtest", "",
             f"Prices {C.index[0].date()} – {C.index[-1].date()} ({C.shape[1]} NSE stocks, adjusted). Scored dates start after {MIN_HISTORY} days of each sector's own history.",
             "Excess = next-day-open entry → close h days after the signal, minus the median liquid NSE stock over the same span, minus 0.3% cost.",
             "Mean/median/hit are over all signal days (overlapping); **t** uses non-overlapping days (every h-th); **Q win** = share of calendar quarters where Strong beat Weak.", ""]

    def block(df, col, title):
        lines.append(f"## {title}")
        lines.append("")
        lines.append("| window | hold | bucket | days | mean | median | hit | t (non-overlap) | Strong−Weak | Q win |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for w in WINDOWS:
            for h in HORIZONS:
                g = df[(df["window"] == w) & (df["h"] == h)]
                if g.empty:
                    continue
                daily_b = g.groupby(["bucket", "date"])[col].mean()
                spread, qwin = "", ""
                if "Strong" in daily_b.index.get_level_values(0) and "Weak" in daily_b.index.get_level_values(0):
                    s_, w_ = daily_b["Strong"], daily_b["Weak"]
                    q = pd.DataFrame({"s": s_.groupby(s_.index.to_period("Q")).mean(), "w": w_.groupby(w_.index.to_period("Q")).mean()}).dropna()
                    spread = f"{(s_.mean() - w_.mean()) * 100:+.2f}%"
                    qwin = f"{(q['s'] > q['w']).sum()}/{len(q)}"
                for b in ("Strong", "Neutral", "Weak"):
                    if b not in daily_b.index.get_level_values(0):
                        continue
                    x = daily_b[b]
                    nov = x.iloc[::h]
                    t = nov.mean() / (nov.std(ddof=1) / np.sqrt(len(nov))) if len(nov) > 2 and nov.std() > 0 else np.nan
                    hit = (x > 0).mean()
                    lines.append(f"| {w}D | {h}D | {b} | {len(x)} | {x.mean()*100:+.2f}% | {x.median()*100:+.2f}% | {hit*100:.0f}% | {t:+.1f} | "
                                 f"{spread if b == 'Strong' else ''} | {qwin if b == 'Strong' else ''} |")
        lines.append("")

    block(J, "leaders", "Top-5 leaders of each sector, by the sector's strength bucket")
    block(J, "sector_all", "Whole sector (every liquid member, equal weight), by strength bucket")

    # the user's plain rule: X% of the sector's stocks up over the window, no percentile scoring
    lines.append("## The plain rule: share of the sector's stocks up over the window")
    lines.append("")
    lines.append("| window | hold | % of stocks up | days | leaders mean | whole sector mean | leaders hit |")
    lines.append("|---|---|---|---|---|---|---|")
    for w in (1, 2, 3, 5):
        for h in HORIZONS:
            g = J[(J["window"] == w) & (J["h"] == h)]
            for lo, hi, lab in ((0.7, 1.01, "≥ 70%"), (0.3, 0.7, "30–70%"), (0.0, 0.3, "< 30%")):
                x = g[(g["pct_up"] >= lo) & (g["pct_up"] < hi)]
                if len(x):
                    lines.append(f"| {w}D | {h}D | {lab} | {x['date'].nunique()} | {x['leaders'].mean()*100:+.2f}% | {x['sector_all'].mean()*100:+.2f}% | {(x['leaders']>0).mean()*100:.0f}% |")
    lines.append("")

    # per sector, 3D window, 10D hold
    lines.append("## By sector (3D window, 10D hold, leaders)")
    lines.append("")
    lines.append("| sector | Strong mean | Neutral mean | Weak mean | Strong days |")
    lines.append("|---|---|---|---|---|")
    g = J[(J["window"] == 3) & (J["h"] == 10)]
    for name in secs:
        x = g[g["sector"] == name].groupby("bucket")["leaders"].agg(["mean", "count"])
        f = lambda b: f"{x.loc[b, 'mean']*100:+.2f}%" if b in x.index else "—"
        lines.append(f"| {name} | {f('Strong')} | {f('Neutral')} | {f('Weak')} | {int(x.loc['Strong','count']) if 'Strong' in x.index else 0} |")
    lines.append("")

    # order wins: in a strong sector vs anywhere
    if a.events and os.path.exists(a.events):
        ev = pd.read_csv(a.events, usecols=["symbol", "date"])
        ev["date"] = pd.to_datetime(ev["date"])
        member = {s: n for n, m in secs.items() for s in m}
        Sx = S[S["window"] == 5].set_index(["date", "sector"])["bucket"]
        rows = []
        for _, e in ev.iterrows():
            if e["symbol"] not in C.columns:
                continue
            pos = C.index.searchsorted(e["date"])
            if pos >= len(C.index):
                continue
            d0 = C.index[pos]
            sec = member.get(e["symbol"])
            b = Sx.get((d0, sec)) if sec else None
            for h in HORIZONS:
                v = fwd[h].at[d0, e["symbol"]] - fbench[h].at[d0] - COST
                if pd.notna(v):
                    rows.append({"h": h, "in_sector": bool(sec), "bucket": b or ("—" if sec else "not in the 5"), "x": v})
        E = pd.DataFrame(rows)
        lines.append("## Order wins: does a strong sector help? (5D strength on the filing day)")
        lines.append("")
        lines.append("| hold | group | wins | mean | median | hit |")
        lines.append("|---|---|---|---|---|---|")
        for h in HORIZONS:
            g = E[E["h"] == h]
            for lab, x in (("all order wins", g), ("in the 5 sectors", g[g["in_sector"]]), ("… sector Strong", g[g["bucket"] == "Strong"]),
                           ("… sector Neutral", g[g["bucket"] == "Neutral"]), ("… sector Weak", g[g["bucket"] == "Weak"])):
                if len(x):
                    lines.append(f"| {h}D | {lab} | {len(x)} | {x['x'].mean()*100:+.2f}% | {x['x'].median()*100:+.2f}% | {(x['x']>0).mean()*100:.0f}% |")
        lines.append("")

    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
