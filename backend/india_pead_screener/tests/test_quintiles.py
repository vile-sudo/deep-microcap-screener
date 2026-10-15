import numpy as np
import pandas as pd

from india_pead_screener.src.quintiles import assign_quintiles, signal


def frame(n=25, date="2026-01-07"):
    rng = np.random.default_rng(0)
    return pd.DataFrame({"symbol": [f"S{i}" for i in range(n)], "announcement_date_used": pd.Timestamp(date),
                         "fiscal_year": 2026, "fiscal_quarter": 3, "SUE": rng.normal(size=n), "EAR": rng.normal(size=n)})


def test_quintiles_equal_groups_and_top_bottom():
    df = assign_quintiles(frame(25))
    assert sorted(df["sue_quintile"].value_counts().tolist()) == [5] * 5
    assert df.loc[df["SUE"].idxmax(), "sue_quintile"] == 5
    assert df.loc[df["SUE"].idxmin(), "sue_quintile"] == 1


def test_small_group_gets_no_quintiles():
    df = assign_quintiles(frame(19))
    assert df["sue_quintile"].isna().all() and not df["qualifies_pead_q5"].any()


def test_nan_scores_are_left_out_of_the_ranking():
    f = frame(26)
    f.loc[0, "SUE"] = np.nan
    df = assign_quintiles(f)
    assert np.isnan(df.loc[0, "sue_quintile"]) and df["sue_quintile"].notna().sum() == 25


def test_q5_qualification_and_signals():
    f = frame(25)
    f.loc[3, ["SUE", "EAR"]] = [9, 9]
    f.loc[4, ["SUE", "EAR"]] = [-9, -9]
    df = assign_quintiles(f)
    assert df.loc[3, "qualifies_pead_q5"]
    assert df["qualifies_pead_q5"].sum() == ((df["sue_quintile"] == 5) & (df["ear_quintile"] == 5)).sum()
    assert signal(df, "long_only").loc[4] == "NONE"
    assert signal(df, "long_short").loc[4] == "SHORT_Q1"


def test_groups_are_per_announcement_date():
    df = assign_quintiles(pd.concat([frame(25, "2026-01-07"), frame(25, "2026-01-08")], ignore_index=True))
    assert df.groupby("quintile_group")["sue_quintile"].apply(lambda s: (s == 5).sum()).tolist() == [5, 5]
