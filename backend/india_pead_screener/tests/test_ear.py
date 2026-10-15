import math

import pandas as pd
import pytest

from india_pead_screener.src.ear import ear_one, forward_car


def px(vals, start="2026-01-05"):
    return pd.Series(vals, index=pd.bdate_range(start, periods=len(vals)))


def test_ear_known_window():
    s = px([100, 100, 110, 121, 121])                       # Mon..Fri
    b = {d: v for d, v in zip(s.index, [1000, 1000, 1010, 1020, 1020])}
    r = ear_one(s, b, pd.Timestamp("2026-01-07"))          # t0 = Wed
    assert r["price_date_minus1"] == pd.Timestamp("2026-01-06")
    assert r["price_date_plus1"] == pd.Timestamp("2026-01-08")
    assert r["stock_return_3d"] == pytest.approx(0.21)
    assert r["benchmark_return_3d"] == pytest.approx(0.02)
    assert r["EAR"] == pytest.approx(0.19)


def test_weekend_announcement_maps_to_next_trading_day():
    s = px([100, 101, 102, 103, 104, 105, 106])            # Mon 5 .. Tue 13 Jan
    b = {d: 1000.0 for d in s.index}
    r = ear_one(s, b, pd.Timestamp("2026-01-10"))          # Saturday
    assert r["announcement_date_used"] == pd.Timestamp("2026-01-12")


def test_after_close_filing_reacts_next_session():
    s = px([100, 101, 102, 103, 104])
    b = {d: 1000.0 for d in s.index}
    r = ear_one(s, b, pd.Timestamp("2026-01-06"), "18:30")
    assert r["announcement_date_used"] == pd.Timestamp("2026-01-07")


def test_incomplete_window_is_nan():
    s = px([100, 101, 102])
    r = ear_one(s, {d: 1.0 for d in s.index}, pd.Timestamp("2026-01-07"))
    assert math.isnan(r["EAR"]) and r["ear_reason"] == "window not complete yet"


def test_forward_car_excludes_the_signal_window():
    s = px([100] * 3 + [110] * 30)
    b = {d: 1000.0 for d in s.index}
    t0 = s.index[1]
    # the window t0+2..t0+21 starts from the close of t0+1: a jump ON t0+2 counts, one on t0+1 doesn't
    assert forward_car(s, b, t0, 2, 21) == pytest.approx(0.10)
    assert forward_car(s, b, s.index[2], 2, 21) == pytest.approx(0.0)
