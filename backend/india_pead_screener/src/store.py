"""SQLite store: every SUE / EAR score the engine computes, kept across runs.

Tables
  scores       one row per symbol-quarter-metric: the latest SUE, EAR, quintiles, signal and forward CARs
  sue_history  a new row whenever a symbol-quarter's SUE / EAR / quintile first appears or changes
               (restated results, a later price) -- the point-in-time record
  forecasts    each stock's next quarter: SRWD expectation, drift and sigma (for the calculator)
  cutoffs      the Q5 entry level (80th percentile) of SUE and EAR per quintile group
  names        symbol -> company name
  data_quality the latest run's issues
  runs, meta   run log
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
  symbol TEXT, quarter_end_date TEXT, metric TEXT, fiscal_year INTEGER, fiscal_quarter INTEGER,
  announcement_date TEXT, announcement_time TEXT, announcement_date_used TEXT,
  actual REAL, expected REAL, drift REAL, raw_surprise REAL, sigma REAL, n_errors INTEGER, quarters_history INTEGER,
  sue_raw REAL, sue REAL, sue_reason TEXT,
  price_date_minus1 TEXT, price_date_plus1 TEXT, stock_ret3 REAL, bench_ret3 REAL, ear REAL, ear_reason TEXT,
  quintile_group TEXT, sue_q INTEGER, ear_q INTEGER, sue_pct REAL, ear_pct REAL, qualifies INTEGER, signal TEXT,
  car21 REAL, car61 REAL, dq_flag TEXT, first_seen TEXT, updated_at TEXT,
  PRIMARY KEY (symbol, quarter_end_date, metric));
CREATE INDEX IF NOT EXISTS scores_ann ON scores(announcement_date_used);
CREATE INDEX IF NOT EXISTS scores_q5 ON scores(qualifies, announcement_date_used);
CREATE TABLE IF NOT EXISTS sue_history (
  symbol TEXT, quarter_end_date TEXT, metric TEXT, sue REAL, ear REAL, sue_q INTEGER, ear_q INTEGER, qualifies INTEGER,
  recorded_at TEXT);
CREATE INDEX IF NOT EXISTS hist_sym ON sue_history(symbol, quarter_end_date);
CREATE TABLE IF NOT EXISTS forecasts (
  symbol TEXT, metric TEXT, quarter_end_date TEXT, base REAL, expected REAL, drift REAL, sigma REAL, n_errors INTEGER,
  updated_at TEXT, PRIMARY KEY (symbol, metric));
CREATE TABLE IF NOT EXISTS cutoffs (quintile_group TEXT PRIMARY KEY, n INTEGER, sue_q5 REAL, ear_q5 REAL, last_date TEXT);
CREATE TABLE IF NOT EXISTS names (symbol TEXT PRIMARY KEY, name TEXT, sector TEXT);
CREATE TABLE IF NOT EXISTS data_quality (issue TEXT, symbol TEXT, date TEXT, explanation TEXT);
CREATE TABLE IF NOT EXISTS runs (run_at TEXT, rows INTEGER, q5 INTEGER, config TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.executescript(SCHEMA)
    return con


def _s(v):
    """SQLite-friendly scalar."""
    if v is None or v is pd.NaT or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.strftime("%Y-%m-%d")
    if hasattr(v, "item"):
        v = v.item()
        return None if isinstance(v, float) and v != v else v
    return v


def save(con: sqlite3.Connection, res: pd.DataFrame, fc: pd.DataFrame, dq: pd.DataFrame, cfg: dict,
         names: dict | None = None, sectors: dict | None = None) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    old = {(r[0], r[1], r[2]): r[3:] for r in con.execute(
        "SELECT symbol, quarter_end_date, metric, sue, ear, sue_q, ear_q, first_seen FROM scores")}
    new_hist = 0
    rows = []
    for r in res.itertuples(index=False):
        k = (r.symbol, _s(r.quarter_end_date), r.metric)
        vals = (_s(r.SUE), _s(r.EAR), _s(r.sue_quintile), _s(r.ear_quintile))
        prev = old.get(k)
        rd = lambda x: None if x is None else round(float(x), 6)
        if prev is None or tuple(map(rd, prev[:4])) != tuple(map(rd, vals)):
            if vals[0] is not None or vals[1] is not None:
                con.execute("INSERT INTO sue_history VALUES (?,?,?,?,?,?,?,?,?)", (*k, *vals, int(bool(r.qualifies_pead_q5)), now))
                new_hist += 1
        rows.append((*k, _s(r.fiscal_year), _s(r.fiscal_quarter), _s(r.announcement_date), _s(getattr(r, "announcement_time", None)),
                     _s(r.announcement_date_used), _s(r.actual), _s(r.expected), _s(r.drift), _s(r.raw_surprise),
                     _s(r.forecast_error_std), _s(r.n_forecast_errors), _s(r.quarters_history), _s(r.SUE_raw), _s(r.SUE),
                     r.sue_reason, _s(r.price_date_minus1), _s(r.price_date_plus1), _s(r.stock_return_3d), _s(r.benchmark_return_3d),
                     _s(r.EAR), r.ear_reason, _s(r.quintile_group), _s(r.sue_quintile), _s(r.ear_quintile), _s(r.sue_percentile),
                     _s(r.ear_percentile), int(bool(r.qualifies_pead_q5)), r.pead_signal, _s(r.forward_car_21d), _s(r.forward_car_61d),
                     r.data_quality_flag, (prev[4] if prev else now), now))
    con.executemany(f"INSERT OR REPLACE INTO scores VALUES ({','.join('?' * 36)})", rows)
    con.execute("DELETE FROM forecasts")
    con.executemany("INSERT INTO forecasts VALUES (?,?,?,?,?,?,?,?,?)",
                    [(r.symbol, r.metric, _s(r.quarter_end_date), _s(r.base), _s(r.expected), _s(r.drift), _s(r.forecast_error_std),
                      _s(r.n_forecast_errors), now) for r in fc.itertuples(index=False)])
    con.execute("DELETE FROM cutoffs")
    ranked = res[res["sue_quintile"].notna()]
    for g, x in ranked.groupby("quintile_group"):
        con.execute("INSERT INTO cutoffs VALUES (?,?,?,?,?)", (g, len(x), float(x["SUE"].quantile(0.8)), float(x["EAR"].quantile(0.8)),
                                                               _s(pd.to_datetime(x["announcement_date_used"]).max())))
    if names:
        con.executemany("INSERT OR REPLACE INTO names VALUES (?,?,?)", [(k, v, (sectors or {}).get(k)) for k, v in names.items()])
    con.execute("DELETE FROM data_quality")
    con.executemany("INSERT INTO data_quality VALUES (?,?,?,?)", [(r.issue, r.symbol, _s(pd.Timestamp(r.date)) if pd.notna(r.date) else None,
                                                                   r.explanation) for r in dq.itertuples(index=False)])
    con.execute("INSERT INTO runs VALUES (?,?,?,?)", (now, len(res), int(res["qualifies_pead_q5"].sum()), json.dumps(cfg)))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('updated_at', ?)", (now,))
    con.commit()
    return {"rows": len(rows), "history_rows_added": new_hist}
