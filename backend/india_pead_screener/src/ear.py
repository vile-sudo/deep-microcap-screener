"""EAR -- the earnings-announcement return: the stock's move around the announcement minus the benchmark's.

    stock_return_3d     = adj_close[t0+1] / adj_close[t0-1] - 1
    benchmark_return_3d = bench[t0+1]     / bench[t0-1]     - 1
    EAR                 = stock_return_3d - benchmark_return_3d

t0 (announcement_date_used) is the first session that could trade on the result: the announcement date if
it is a trading day and the filing came before the close, else the next trading day. Indian companies mostly
file after 3:30 PM, so an `announcement_time` at/after the close moves t0 to the next session.
"""
from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

OUT_COLS = ["symbol", "announcement_date", "announcement_date_used", "price_date_minus1", "price_date_0",
            "price_date_plus1", "stock_return_3d", "benchmark_return_3d", "EAR", "ear_reason"]


def reaction_date(dates: list, ann: pd.Timestamp, ann_time: str | None = None, after_close: str = "15:30"):
    """t0: the first trading date on/after the announcement (the next one for an after-close filing)."""
    i = bisect.bisect_left(dates, ann)
    if i < len(dates) and dates[i] == ann and ann_time and ann_time >= after_close:
        i += 1
    return i if i < len(dates) else None


def ear_one(stock: pd.Series, bench: pd.Series, ann: pd.Timestamp, ann_time: str | None = None,
            window=(-1, 0, 1), after_close: str = "15:30") -> dict:
    """EAR for one announcement. `stock` and `bench`: adjusted closes indexed by trading date (sorted)."""
    out = {"announcement_date_used": pd.NaT, "price_date_minus1": pd.NaT, "price_date_0": pd.NaT, "price_date_plus1": pd.NaT,
           "stock_return_3d": np.nan, "benchmark_return_3d": np.nan, "EAR": np.nan, "ear_reason": ""}
    dates = list(stock.index)
    i = reaction_date(dates, ann, ann_time, after_close)
    if i is None:
        out["ear_reason"] = "announcement after the last price"
        return out
    lo, hi = i + min(window), i + max(window)
    out["announcement_date_used"] = dates[i]
    if lo < 0 or hi >= len(dates):
        out["ear_reason"] = "window not complete yet" if hi >= len(dates) else "no price before the announcement"
        return out
    d0, d1 = dates[lo], dates[hi]
    out.update({"price_date_minus1": d0, "price_date_0": dates[i], "price_date_plus1": d1})
    p0, p1 = stock.iloc[lo], stock.iloc[hi]
    if not (p0 > 0 and p1 > 0):
        out["ear_reason"] = "missing price in the window"
        return out
    b0, b1 = bench.get(d0), bench.get(d1)
    if b0 is None or b1 is None or not (b0 > 0 and b1 > 0):
        out["ear_reason"] = "missing benchmark in the window"
        return out
    out["stock_return_3d"] = p1 / p0 - 1
    out["benchmark_return_3d"] = b1 / b0 - 1
    out["EAR"] = out["stock_return_3d"] - out["benchmark_return_3d"]
    return out


def compute_ear(events: pd.DataFrame, prices: pd.DataFrame, bench: pd.DataFrame, window=(-1, 0, 1),
                after_close: str = "15:30") -> pd.DataFrame:
    """EAR for every row of `events` (symbol, announcement_date[, announcement_time])."""
    b = bench.set_index("date")["benchmark_close"].sort_index()
    bd = b.to_dict()
    by = {s: g.set_index("date")["adjusted_close_price"].sort_index() for s, g in prices.groupby("symbol")}
    rows = []
    for r in events.itertuples(index=False):
        s = by.get(r.symbol)
        base = {"symbol": r.symbol, "announcement_date": r.announcement_date}
        if s is None or s.empty or pd.isna(r.announcement_date):
            rows.append({**base, **{k: (pd.NaT if "date" in k else np.nan) for k in OUT_COLS[2:9]},
                         "ear_reason": "no prices" if s is None or s.empty else "no announcement date"})
            continue
        rows.append({**base, **ear_one(s, bd, r.announcement_date, getattr(r, "announcement_time", None), window, after_close)})
    return pd.DataFrame(rows, columns=OUT_COLS)


def forward_car(stock: pd.Series, bench: dict, t0: pd.Timestamp, start: int, end: int) -> float:
    """Research only: stock minus benchmark return from the close of t0+start-1 to the close of t0+end
    (i.e. holding sessions t0+start .. t0+end). Never feeds the signal."""
    dates = list(stock.index)
    i = bisect.bisect_left(dates, t0)
    if i >= len(dates) or dates[i] != t0 or i + end >= len(dates):
        return np.nan
    a, b = dates[i + start - 1], dates[i + end]
    ba, bb = bench.get(a), bench.get(b)
    if not (ba and bb) or not (stock.iloc[i + start - 1] > 0):
        return np.nan
    return (stock.iloc[i + end] / stock.iloc[i + start - 1] - 1) - (bb / ba - 1)
