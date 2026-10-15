import math

import numpy as np
import pandas as pd
import pytest

from india_pead_screener.src.srwd_sue import compute_sue, drift, expected, forecast_errors, sue_one


def series(vals, start=2019 * 4 + 1):
    """{quarter index: value} for consecutive quarters."""
    return {start + i: float(v) for i, v in enumerate(vals)}


def test_drift_is_average_of_last_n_yoy_changes():
    v = series([10, 11, 12, 13, 14, 16, 18, 20, 22])
    t = max(v)
    want = np.mean([v[t - j] - v[t - j - 4] for j in range(1, 5)])
    assert drift(v, t, 4) == pytest.approx(want)


def test_expected_is_same_quarter_last_year_plus_drift():
    v = series([100, 90, 120, 110, 108, 98, 128, 118, 0])
    t = max(v)
    d = drift(v, t, 4)
    assert d == pytest.approx(8.0)                          # every quarter grew by 8 year on year
    assert expected(v, t, 4) == pytest.approx((116.0, 8.0))  # 108 (a year ago) + 8


def test_missing_quarter_breaks_the_chain():
    v = series([100, 90, 120, 110, 108, 98, 128, 118, 120])
    t = max(v)
    del v[t - 2]
    assert expected(v, t, 4) is None


def test_sigma_uses_sample_std_of_earlier_forecast_errors():
    rng = np.random.default_rng(1)
    vals = [100 + 5 * i + (10 if i % 4 == 0 else 0) + rng.normal(0, 3) for i in range(20)]
    v = series(vals)
    t = max(v)
    errs = forecast_errors(v, t, 4, window=12)
    r = sue_one(v, t, 4, min_errors=4, window=12, min_history=16, winsor=(-10, 10))
    assert len(errs) >= 4
    assert r["forecast_error_std"] == pytest.approx(np.std(errs, ddof=1))
    assert r["SUE"] == pytest.approx(r["raw_surprise"] / r["forecast_error_std"])


def test_sue_nan_with_reason_when_history_is_short():
    v = series([10, 11, 12, 13, 14, 15, 16, 17, 18])
    r = sue_one(v, max(v), 4, min_history=16)
    assert math.isnan(r["SUE"]) and "quarters of history" in r["sue_reason"]


def test_zero_sigma_gives_nan():
    v = series([100 + 10 * i for i in range(20)])            # perfectly linear: every forecast error is 0
    r = sue_one(v, max(v), 4)
    assert math.isnan(r["SUE"]) and r["sue_reason"] == "zero forecast-error sigma"


def test_winsorization():
    rng = np.random.default_rng(2)
    vals = [100 + 5 * i + rng.normal(0, 1) for i in range(19)] + [500]
    v = series(vals)
    r = sue_one(v, max(v), 4, winsor=(-10, 10))
    assert r["SUE_raw"] > 10 and r["SUE"] == 10


def test_lookahead_guard_drops_errors_announced_after_the_current_result():
    rng = np.random.default_rng(3)
    v = series([100 + 4 * i + rng.normal(0, 3) for i in range(20)])
    t = max(v)
    ann = {k: pd.Timestamp("2020-01-01") + pd.Timedelta(days=91 * (k - min(v))) for k in v}
    full = forecast_errors(v, t, 4, ann)
    late = dict(ann)
    late[t - 1] = ann[t] + pd.Timedelta(days=1)              # quarter t-1 "announced" after quarter t
    assert len(forecast_errors(v, t, 4, late)) == len(full) - 1


def test_compute_sue_dataframe_and_drift_window_bounds():
    rows = []
    for i, qe in enumerate(pd.date_range("2019-06-30", periods=20, freq="QE-DEC")):
        rows.append({"symbol": "AAA", "fiscal_year": 0, "fiscal_quarter": 0, "quarter_end_date": qe,
                     "announcement_date": qe + pd.Timedelta(days=40), "reported_eps": 10 + i + (i % 3), "net_profit": 1})
    df = pd.DataFrame(rows)
    out = compute_sue(df, "eps", 4)
    assert len(out) == 20 and out["SUE"].notna().sum() >= 1
    with pytest.raises(ValueError):
        compute_sue(df, "eps", 9)
