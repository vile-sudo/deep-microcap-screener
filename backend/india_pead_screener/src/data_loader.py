"""Inputs: three CSVs (see README), sample templates when they are missing, and -- inside the screener app --
an exporter that builds the same three tables from the app's own data (no CSVs needed)."""
from __future__ import annotations

import gzip
import json
import warnings
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

FIN_COLS = ["symbol", "fiscal_year", "fiscal_quarter", "quarter_end_date", "announcement_date", "reported_eps",
            "net_profit", "shares_outstanding", "exceptional_items", "tax_adjustments"]
PX_COLS = ["symbol", "date", "close_price", "adjusted_close_price"]
BENCH_COLS = ["date", "benchmark_close"]


class InputError(ValueError):
    pass


def _need(df: pd.DataFrame, cols: list[str], name: str) -> None:
    miss = [c for c in cols if c not in df.columns]
    if miss:
        raise InputError(f"{name}: missing required columns {miss}")


def fiscal(qe: pd.Timestamp) -> tuple[int, int]:
    """Indian fiscal year (April-March, named by the year it ends) and quarter (Jun = Q1 ... Mar = Q4)."""
    fy = qe.year + (1 if qe.month > 3 else 0)
    return fy, {6: 1, 9: 2, 12: 3, 3: 4}.get(qe.month, (qe.month + 8) // 3 % 4 + 1)


def load_csvs(paths: dict, base: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    fin = pd.read_csv(base / paths["quarterly_financials"])
    px = pd.read_csv(base / paths["daily_prices"])
    bench = pd.read_csv(base / paths["benchmark_prices"])
    _need(fin, FIN_COLS, "quarterly_financials.csv")
    _need(px, PX_COLS, "daily_prices.csv")
    _need(bench, BENCH_COLS, "benchmark_prices.csv")
    return prepare(fin, px, bench)


def prepare(fin: pd.DataFrame, px: pd.DataFrame, bench: pd.DataFrame):
    """Types, and the adjusted-price rule: rows without an adjusted close are dropped with a warning --
    raw closes are never used in their place (splits and bonuses would show up as crashes)."""
    fin = fin.copy()
    fin["quarter_end_date"] = pd.to_datetime(fin["quarter_end_date"])
    fin["announcement_date"] = pd.to_datetime(fin["announcement_date"])
    for c in ("reported_eps", "net_profit", "shares_outstanding", "exceptional_items", "tax_adjustments"):
        fin[c] = pd.to_numeric(fin[c], errors="coerce")
    px = px.copy()
    px["date"] = pd.to_datetime(px["date"])
    px["adjusted_close_price"] = pd.to_numeric(px["adjusted_close_price"], errors="coerce")
    miss = px["adjusted_close_price"].isna()
    if miss.any():
        warnings.warn(f"{int(miss.sum())} price rows have no adjusted close and are dropped -- raw prices are not "
                      f"used for returns ({px.loc[miss, 'symbol'].nunique()} symbols affected)", stacklevel=2)
        px = px[~miss]
    bench = bench.copy()
    bench["date"] = pd.to_datetime(bench["date"])
    bench["benchmark_close"] = pd.to_numeric(bench["benchmark_close"], errors="coerce")
    return fin, px.sort_values(["symbol", "date"]), bench.dropna().sort_values("date")


def write_sample_data(raw: Path, seed: int = 7) -> None:
    """SYNTHETIC templates (clearly not market data) so the project runs end to end out of the box:
    30 made-up symbols, 20 quarters, daily prices and a benchmark."""
    rng = np.random.default_rng(seed)
    raw.mkdir(parents=True, exist_ok=True)
    days = pd.bdate_range("2021-04-01", "2026-06-30")
    bench = 10000 * np.cumprod(1 + rng.normal(0.0004, 0.009, len(days)))
    pd.DataFrame({"date": days.date, "benchmark_close": bench.round(2)}).to_csv(raw / "benchmark_prices.csv", index=False)
    qends = pd.date_range("2021-06-30", periods=20, freq="QE-DEC")
    fin, px = [], []
    for s in range(30):
        sym = f"SYNTH{s:02d}"
        eps, g = rng.uniform(5, 40), rng.normal(0.02, 0.03)
        season = rng.normal(0, 0.08, 4)
        anns = []
        for k, qe in enumerate(qends):
            eps_k = eps * (1 + g) ** k * (1 + season[qe.quarter - 1]) + rng.normal(0, eps * 0.06)
            # most companies report on one of two "big days" per quarter, so same-day groups reach the minimum size
            ann = qe + pd.Timedelta(days=40 if s < 24 else int(rng.integers(20, 50)))
            ann = ann if ann.weekday() < 5 else ann + pd.Timedelta(days=7 - ann.weekday())
            anns.append(ann)
            fy, fq = fiscal(qe)
            fin.append({"symbol": sym, "fiscal_year": fy, "fiscal_quarter": fq, "quarter_end_date": qe.date(),
                        "announcement_date": ann.date(), "reported_eps": round(eps_k, 2), "net_profit": round(eps_k * 10, 2),
                        "shares_outstanding": 10_000_000, "exceptional_items": 0, "tax_adjustments": 0})
        p = 100 * np.cumprod(1 + rng.normal(0.0005, 0.018, len(days)))
        for i, d in enumerate(days):
            px.append({"symbol": sym, "date": d.date(), "close_price": round(p[i], 2), "adjusted_close_price": round(p[i], 2),
                       "turnover": round(p[i] * rng.integers(100_000, 2_000_000), 0)})
    pd.DataFrame(fin, columns=FIN_COLS).to_csv(raw / "quarterly_financials.csv", index=False)
    pd.DataFrame(px).to_csv(raw / "daily_prices.csv", index=False)


def from_app(backend_dir: Path) -> dict:
    """The three tables from the screener app's data: quarterly results (screener.in, extended back with NSE
    XBRL -- data/results_preview/pead_history.json.gz), filing times (NSE announcements), split-adjusted daily
    prices and the Nifty 500. Financials and utilities are left out (app/pead.py's exclusion list).
    Returns {"fin", "px", "bench", "names", "excluded"}; fin also carries announcement_time and sector."""
    import sys
    sys.path.insert(0, str(backend_dir))
    from app import pead as P
    from app import results_preview as RP

    mk = RP.Market()
    bm = P.Bench()
    H = P._history()
    for d, v in (H.get("nifty500") or {}).items():
        bm.lvl.setdefault(d, v)
    sectors = json.loads(P.SECTORS.read_text(encoding="utf-8")).get("sectors") or []
    excluded = {f"NSE:{s.get('symbol')}" for x in sectors if x.get("group") == "nse" for s in x.get("stocks") or []
                if x["slug"] in P.EXCLUDE_SECTORS or s.get("industry") in P.EXCLUDE_INDUSTRIES}
    fin, px, names = [], [], {}
    for key, e in mk.ent.items():
        if not key.startswith("NSE:") or key in excluded:
            continue
        sym = key[4:]
        P0 = e["q"].get("periods") or []
        if RP.lag_of(P0) != 4:
            continue
        rec: dict[str, dict] = {}
        h = (H.get("companies") or {}).get(sym)
        if h and not h["check"][1]:          # XBRL history only where it agrees with screener on the overlap
            for lab, v in h["q"].items():
                if v.get("np") is not None:
                    rec[lab] = {"np": v["np"], "eps": None, "filed": v.get("filed")}
        for i, lab in enumerate(P0):
            np_ = (e["q"].get("net_profit") or [None] * len(P0))[i] if i < len(e["q"].get("net_profit") or []) else None
            eps = (e["q"].get("eps") or [None] * len(P0))[i] if i < len(e["q"].get("eps") or []) else None
            if np_ is not None:
                rec[lab] = {"np": np_, "eps": eps, "filed": (rec.get(lab) or {}).get("filed")}
        if not rec:
            continue
        for lab, v in rec.items():
            qe = RP.qend(lab)
            filed = (mk.result_filed(key, qe) if qe >= date(2023, 9, 30) else None) or v["filed"]
            fy, fq = fiscal(pd.Timestamp(qe))
            fin.append({"symbol": sym, "fiscal_year": fy, "fiscal_quarter": fq, "quarter_end_date": qe,
                        "announcement_date": filed[:10] if filed else None, "announcement_time": filed[11:16] if filed else None,
                        "reported_eps": v["eps"], "net_profit": v["np"], "shares_outstanding": None,
                        "exceptional_items": None, "tax_adjustments": None, "sector": mk.sector.get(key)})
        rows = P._prices(mk.prices(key), (H.get("prices") or {}).get(sym))
        for d, c, vol, o in rows:
            px.append((sym, d, c, c, c * vol if vol else np.nan))
        names[sym] = e["name"]
    fin = pd.DataFrame(fin)
    pxd = pd.DataFrame(px, columns=["symbol", "date", "close_price", "adjusted_close_price", "turnover"])
    bench = pd.DataFrame(sorted(bm.lvl.items()), columns=["date", "benchmark_close"])
    f, p, b = prepare(fin, pxd, bench)
    f["announcement_time"] = fin["announcement_time"].values
    f["sector"] = fin["sector"].values
    return {"fin": f, "px": p, "bench": b, "names": names, "excluded": len(excluded)}
