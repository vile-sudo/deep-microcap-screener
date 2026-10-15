"""The pipeline: SUE -> EAR -> filters -> quintiles -> signal -> forward CAR (research only)."""
from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

from .ear import compute_ear, forward_car
from .quintiles import assign_quintiles, signal
from .srwd_sue import compute_sue, forecast_next, qindex
from .validation import check_inputs, check_scores

RESULT_COLS = ["symbol", "announcement_date", "fiscal_year", "fiscal_quarter", "actual_eps", "expected_eps", "drift",
               "raw_surprise", "forecast_error_std", "SUE", "stock_return_3d", "benchmark_return_3d", "EAR",
               "sue_quintile", "ear_quintile", "qualifies_pead_q5", "pead_signal", "forward_car_21d", "forward_car_61d",
               "data_quality_flag"]


def _liquidity(px_sym: pd.DataFrame | None, t0, min_turnover: float, min_price: float) -> str:
    """Why a stock is filtered out on the session before t0 ('' when it passes)."""
    if px_sym is None or pd.isna(t0):
        return ""
    d = list(px_sym.index)
    i = bisect.bisect_left(d, t0)
    if i == 0:
        return ""
    before = px_sym.iloc[max(0, i - 60):i]
    why = []
    if min_price and before["adjusted_close_price"].iloc[-1] < min_price:
        why.append(f"price under Rs {min_price:g}")
    if min_turnover and "turnover" in before and before["turnover"].notna().any():
        med = before["turnover"].median()
        if med < min_turnover:
            why.append(f"median daily turnover Rs {med / 1e7:.2f} cr < Rs {min_turnover / 1e7:.2f} cr")
    return "; ".join(why)


def run(cfg: dict, fin: pd.DataFrame, px: pd.DataFrame, bench: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(results, data-quality report, next-quarter forecasts)."""
    s, e, q, st, flt = cfg["srwd"], cfg["ear"], cfg["quintiles"], cfg["strategy"], cfg["filters"]
    metric = s.get("metric", "eps")
    dq = check_inputs(fin, px)
    sue = compute_sue(fin, metric, s["drift_window"], s["min_quarters_history"], s["min_forecast_errors"],
                      s.get("sigma_window", 12), s["sue_winsorization"], flt.get("exclude_negative_eps", True))
    extra = [c for c in ("announcement_time", "actual_source") if c in fin]
    if extra:
        sue = sue.merge(fin[["symbol", "quarter_end_date", *extra]], on=["symbol", "quarter_end_date"], how="left")
    ear = compute_ear(sue, px, bench, tuple(e["window_days"]), e.get("after_close_time", "15:30"))
    res = pd.concat([sue.reset_index(drop=True), ear.drop(columns=["symbol", "announcement_date"]).reset_index(drop=True)], axis=1)

    by = {k: g.set_index("date") for k, g in px.groupby("symbol")}
    res["liquidity_flag"] = [_liquidity(by.get(r.symbol), r.announcement_date_used, flt.get("min_median_daily_turnover", 0),
                                        flt.get("min_price", 0)) for r in res.itertuples(index=False)]
    res["filtered_out"] = res["liquidity_flag"] != ""
    res = assign_quintiles(res, q.get("group_by", "date"), q["min_universe_size"], q["number_of_buckets"])
    res["pead_signal"] = signal(res, st["mode"], q["number_of_buckets"])

    bd = bench.set_index("date")["benchmark_close"].to_dict()
    lag = st.get("entry_lag_days", 2)
    for w in st["forward_windows"]:
        res[f"forward_car_{w}d"] = [forward_car(by[r.symbol]["adjusted_close_price"], bd, r.announcement_date_used, lag, w)
                                    if r.symbol in by and pd.notna(r.announcement_date_used) else np.nan
                                    for r in res.itertuples(index=False)]
    dq += check_scores(res, tuple(s["sue_winsorization"]))
    res["data_quality_flag"] = [
        "; ".join(x for x in (r.sue_reason, r.ear_reason, r.liquidity_flag) if x) for r in res.itertuples(index=False)]
    res["actual_eps"], res["expected_eps"] = res["actual"], res["expected"]   # named for the spec; `metric` says which

    # next quarter's expectation per stock (the calculator scores a result against it the moment it is out)
    col = {"eps": "reported_eps", "net_profit": "net_profit"}[metric]
    fc = []
    for sym, g in fin.groupby("symbol"):
        vals = {qindex(r.quarter_end_date): float(getattr(r, col)) for r in g.itertuples(index=False) if pd.notna(getattr(r, col))}
        if not vals:
            continue
        t = max(vals) + 1
        f = forecast_next(vals, t, s["drift_window"], s.get("sigma_window", 12), s["min_forecast_errors"])
        y, m = divmod(t, 4)
        fc.append({"symbol": sym, "quarter_end_date": pd.Timestamp(y, m * 3 + 3, 1) + pd.offsets.MonthEnd(0), "metric": metric, **f})
    return res, pd.DataFrame(dq, columns=["issue", "symbol", "date", "explanation"]), pd.DataFrame(fc)
