"""Cross-sectional quintiles: within each announcement group, rank SUE and EAR separately.

Quintiles are relative to the stocks announcing at the same time -- not an absolute SUE threshold -- so a
"top fifth" surprise means top fifth among that day's (or week's / season's) reporters. Stocks with a
missing SUE or EAR are left out of that ranking; a group smaller than `min_size` gets no quintiles.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def group_key(df: pd.DataFrame, group_by: str = "date") -> pd.Series:
    d = pd.to_datetime(df["announcement_date_used"])
    if group_by == "date":
        return d.dt.strftime("%Y-%m-%d")
    if group_by == "week":
        return d.dt.strftime("%G-W%V")
    if group_by == "season":
        return df["fiscal_year"].astype(str) + "-Q" + df["fiscal_quarter"].astype(str)
    raise ValueError(f"unknown group_by {group_by!r}")


def _bucket(x: pd.Series, n: int, min_size: int) -> tuple[pd.Series, pd.Series]:
    ok = x.dropna()
    q = pd.Series(np.nan, index=x.index)
    pct = pd.Series(np.nan, index=x.index)
    if len(ok) < min_size:
        return q, pct
    # rank first so ties never collapse a bucket; qcut on ranks gives equal-count groups
    r = ok.rank(method="first")
    q.loc[ok.index] = pd.qcut(r, n, labels=list(range(1, n + 1))).astype(int)
    pct.loc[ok.index] = ok.rank(pct=True)
    return q, pct


def assign_quintiles(df: pd.DataFrame, group_by: str = "date", min_size: int = 20, n: int = 5) -> pd.DataFrame:
    """Adds group, sue_quintile, ear_quintile, sue_percentile, ear_percentile, qualifies_pead_q5."""
    df = df.copy()
    df["quintile_group"] = group_key(df, group_by).where(df["announcement_date_used"].notna())
    for c in ("sue_quintile", "ear_quintile", "sue_percentile", "ear_percentile"):
        df[c] = np.nan
    for _, g in df.groupby("quintile_group"):
        # the eligible universe for this group: rows with both scores (and not filtered out)
        elig = g[g["SUE"].notna() & g["EAR"].notna() & ~g.get("filtered_out", pd.Series(False, index=g.index)).astype(bool)]
        sq, sp = _bucket(elig["SUE"], n, min_size)
        eq, ep = _bucket(elig["EAR"], n, min_size)
        df.loc[elig.index, "sue_quintile"], df.loc[elig.index, "sue_percentile"] = sq, sp
        df.loc[elig.index, "ear_quintile"], df.loc[elig.index, "ear_percentile"] = eq, ep
    df["qualifies_pead_q5"] = (df["sue_quintile"] == n) & (df["ear_quintile"] == n)
    return df


def signal(df: pd.DataFrame, mode: str = "long_only", n: int = 5) -> pd.Series:
    """LONG_Q5 / SHORT_Q1 / NONE (SHORT_Q1 only in long_short mode)."""
    s = pd.Series("NONE", index=df.index)
    s[df["qualifies_pead_q5"]] = "LONG_Q5"
    if mode == "long_short":
        s[(df["sue_quintile"] == 1) & (df["ear_quintile"] == 1)] = "SHORT_Q1"
    elif mode != "long_only":
        raise ValueError("strategy mode must be long_only or long_short")
    return s
