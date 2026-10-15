"""SUE from a Seasonal Random Walk with Drift (SRWD) -- no analyst estimates.

For stock i and quarter t (metric = EPS by default, or net profit):

    drift_t       = (1/N) * sum_{j=1..N} (E_{t-j} - E_{t-j-4})
    expected_t    = E_{t-4} + drift_t
    raw_surprise  = E_t - expected_t
    sigma_t       = sample std (ddof=1) of the stock's own earlier SRWD forecast errors
                    (E_k - expected_k for quarters k < t announced BEFORE quarter t)
    SUE_t         = raw_surprise / sigma_t, winsorized

Quarters are placed on a continuous quarterly index (year*4 + quarter), so a missing quarter breaks the
chain instead of silently pairing the wrong periods.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

OUT_COLS = ["symbol", "fiscal_year", "fiscal_quarter", "quarter_end_date", "announcement_date", "metric",
            "actual", "expected", "drift", "raw_surprise", "forecast_error_std", "n_forecast_errors",
            "quarters_history", "SUE_raw", "SUE", "sue_reason"]


def qindex(d: pd.Timestamp) -> int:
    """Continuous quarter number of a quarter-end date."""
    return d.year * 4 + (d.month - 1) // 3


def drift(values: dict[int, float], t: int, n: int) -> float | None:
    """Average of the last n year-on-year changes before quarter t, or None if any is missing."""
    ch = []
    for j in range(1, n + 1):
        a, b = values.get(t - j), values.get(t - j - 4)
        if a is None or b is None:
            return None
        ch.append(a - b)
    return sum(ch) / n


def expected(values: dict[int, float], t: int, n: int) -> tuple[float, float] | None:
    """(expected E_t, drift_t) -- SRWD: same quarter last year plus drift."""
    base = values.get(t - 4)
    d = drift(values, t, n)
    if base is None or d is None:
        return None
    return base + d, d


def forecast_errors(values: dict[int, float], t: int, n: int, ann: dict[int, pd.Timestamp] | None = None,
                    window: int = 12) -> list[float]:
    """Earlier SRWD forecast errors usable at quarter t: quarters k < t whose own result was announced before
    quarter t's (look-ahead guard), most recent `window` of them."""
    cut = (ann or {}).get(t)
    errs = []
    for k in sorted(x for x in values if x < t):
        if cut is not None and ann is not None and ann.get(k) is not None and not ann[k] < cut:
            continue
        e = expected(values, k, n)
        if e is not None:
            errs.append(values[k] - e[0])
    return errs[-window:]


def sue_one(values: dict[int, float], t: int, n: int = 4, ann: dict[int, pd.Timestamp] | None = None,
            min_errors: int = 4, window: int = 12, min_history: int = 16, winsor: tuple[float, float] = (-10, 10),
            exclude_negative: bool = True) -> dict:
    """SUE for quarter t of one stock, with every intermediate and a reason when it can't be computed."""
    hist = sum(1 for k in values if k <= t)
    out = {"actual": values.get(t), "expected": np.nan, "drift": np.nan, "raw_surprise": np.nan,
           "forecast_error_std": np.nan, "n_forecast_errors": 0, "quarters_history": hist,
           "SUE_raw": np.nan, "SUE": np.nan, "sue_reason": ""}
    if values.get(t) is None:
        out["sue_reason"] = "no actual value"
        return out
    e = expected(values, t, n)
    if e is None:
        out["sue_reason"] = "missing quarters for drift / same quarter last year"
        return out
    out["expected"], out["drift"] = e
    out["raw_surprise"] = values[t] - e[0]
    errs = forecast_errors(values, t, n, ann, window)
    out["n_forecast_errors"] = len(errs)
    if hist < min_history:
        out["sue_reason"] = f"only {hist} quarters of history (< {min_history})"
        return out
    if len(errs) < min_errors:
        out["sue_reason"] = f"only {len(errs)} earlier forecast errors (< {min_errors})"
        return out
    sd = float(np.std(errs, ddof=1))
    out["forecast_error_std"] = sd
    if not sd > 0 or math.isnan(sd):
        out["sue_reason"] = "zero forecast-error sigma"
        return out
    if exclude_negative and (values[t] <= 0 or values[t - 4] <= 0):
        out["sue_reason"] = "negative or zero earnings (this quarter or a year ago)"
        return out
    raw = out["raw_surprise"] / sd
    out["SUE_raw"] = raw
    out["SUE"] = float(min(max(raw, winsor[0]), winsor[1]))
    return out


def compute_sue(fin: pd.DataFrame, metric: str = "eps", n: int = 4, min_history: int = 16, min_errors: int = 4,
                window: int = 12, winsor=(-10, 10), exclude_negative: bool = True) -> pd.DataFrame:
    """SUE for every symbol-quarter in `fin` (one row per symbol and quarter_end_date)."""
    if not 2 <= n <= 8:
        raise ValueError("drift_window must be between 2 and 8")
    col = {"eps": "reported_eps", "net_profit": "net_profit"}[metric]
    rows = []
    for sym, g in fin.sort_values("quarter_end_date").groupby("symbol", sort=False):
        g = g.dropna(subset=["quarter_end_date"])
        idx = {qindex(r.quarter_end_date): r for r in g.itertuples(index=False)}
        values = {k: float(getattr(r, col)) for k, r in idx.items() if pd.notna(getattr(r, col))}
        ann = {k: r.announcement_date for k, r in idx.items() if pd.notna(r.announcement_date)}
        for k, r in sorted(idx.items()):
            res = sue_one(values, k, n, ann, min_errors, window, min_history, tuple(winsor), exclude_negative)
            rows.append({"symbol": sym, "fiscal_year": r.fiscal_year, "fiscal_quarter": r.fiscal_quarter,
                         "quarter_end_date": r.quarter_end_date, "announcement_date": r.announcement_date,
                         "metric": metric, **res})
    return pd.DataFrame(rows, columns=OUT_COLS)


def forecast_next(values: dict[int, float], t_next: int, n: int = 4, window: int = 12, min_errors: int = 4) -> dict:
    """For a quarter not reported yet: the SRWD expectation and sigma, so a result can be scored the moment
    it is out (profit needed for a given SUE = expected + SUE * sigma)."""
    e = expected(values, t_next, n)
    errs = forecast_errors(values, t_next, n, None, window)
    sd = float(np.std(errs, ddof=1)) if len(errs) >= max(2, min_errors) else np.nan
    return {"expected": e[0] if e else np.nan, "drift": e[1] if e else np.nan, "forecast_error_std": sd,
            "n_forecast_errors": len(errs), "base": values.get(t_next - 4)}
