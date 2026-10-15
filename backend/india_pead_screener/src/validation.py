"""Data-quality checks -> one row per issue: issue type, symbol, date, explanation."""
from __future__ import annotations

import pandas as pd

DQ_COLS = ["issue", "symbol", "date", "explanation"]
GAP = 0.40            # a one-day adjusted move beyond +-40% is more likely an unadjusted corporate action


def check_inputs(fin: pd.DataFrame, px: pd.DataFrame) -> list[dict]:
    out = []
    dup = px[px.duplicated(["symbol", "date"], keep=False)]
    for r in dup.drop_duplicates(["symbol", "date"]).itertuples(index=False):
        out.append({"issue": "duplicate_price_row", "symbol": r.symbol, "date": r.date, "explanation": "more than one price row for the date"})
    dupq = fin[fin.duplicated(["symbol", "quarter_end_date"], keep=False)]
    for r in dupq.drop_duplicates(["symbol", "quarter_end_date"]).itertuples(index=False):
        out.append({"issue": "duplicate_quarter", "symbol": r.symbol, "date": r.quarter_end_date, "explanation": "more than one row for the quarter"})
    for r in fin[fin["announcement_date"].isna()].itertuples(index=False):
        out.append({"issue": "missing_announcement_date", "symbol": r.symbol, "date": r.quarter_end_date,
                    "explanation": "no announcement date: no EAR and no quintile for this quarter"})
    bad = fin[fin["announcement_date"].notna() & (fin["announcement_date"] <= fin["quarter_end_date"])]
    for r in bad.itertuples(index=False):
        out.append({"issue": "announcement_before_quarter_end", "symbol": r.symbol, "date": r.announcement_date,
                    "explanation": f"announced on/before the quarter ended ({r.quarter_end_date.date()})"})
    p = px.sort_values(["symbol", "date"])
    ret = p.groupby("symbol")["adjusted_close_price"].pct_change()
    for r in p[ret.abs() > GAP].itertuples(index=False):
        out.append({"issue": "adjusted_price_gap", "symbol": r.symbol, "date": r.date,
                    "explanation": f"one-day move beyond {int(GAP * 100)}% -- check for an unadjusted split/bonus/rights issue"})
    return out


def check_scores(res: pd.DataFrame, winsor=(-10, 10)) -> list[dict]:
    out = []
    for r in res.itertuples(index=False):
        reason = r.sue_reason or ""
        if reason.startswith("only") and "quarters" in reason:
            out.append({"issue": "insufficient_history", "symbol": r.symbol, "date": r.quarter_end_date, "explanation": reason})
        elif reason.startswith("only") and "forecast errors" in reason:
            out.append({"issue": "insufficient_forecast_errors", "symbol": r.symbol, "date": r.quarter_end_date, "explanation": reason})
        elif reason == "zero forecast-error sigma":
            out.append({"issue": "zero_sigma", "symbol": r.symbol, "date": r.quarter_end_date, "explanation": reason})
        elif reason.startswith("negative"):
            out.append({"issue": "negative_earnings", "symbol": r.symbol, "date": r.quarter_end_date, "explanation": reason})
        if r.ear_reason and r.ear_reason not in ("window not complete yet", "announcement after the last price", "no announcement date"):
            out.append({"issue": "missing_prices_around_announcement", "symbol": r.symbol, "date": r.announcement_date, "explanation": r.ear_reason})
        if pd.notna(r.SUE_raw) and not winsor[0] <= r.SUE_raw <= winsor[1]:
            out.append({"issue": "extreme_sue", "symbol": r.symbol, "date": r.quarter_end_date,
                        "explanation": f"SUE {r.SUE_raw:.1f} winsorized to {r.SUE:.0f}"})
        if getattr(r, "liquidity_flag", "") :
            out.append({"issue": "low_liquidity", "symbol": r.symbol, "date": r.announcement_date, "explanation": r.liquidity_flag})
    return out
