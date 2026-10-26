"""Output files and the end-of-run summary."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .screener import RESULT_COLS
from .table import box, kv

EXTRA = ["metric", "actual_source", "quarter_end_date", "announcement_time", "announcement_date_used", "price_date_minus1", "price_date_0",
         "price_date_plus1", "n_forecast_errors", "quarters_history", "SUE_raw", "sue_percentile", "ear_percentile",
         "quintile_group", "sue_reason", "ear_reason", "liquidity_flag"]


def write_outputs(res: pd.DataFrame, dq: pd.DataFrame, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = RESULT_COLS + [c for c in EXTRA if c in res.columns]
    full = res[cols].sort_values(["announcement_date", "symbol"])
    paths = {"results": out_dir / "sue_ear_results.csv", "q5": out_dir / "pead_q5_candidates.csv",
             "dq": out_dir / "data_quality_report.csv"}
    full.to_csv(paths["results"], index=False)
    full[full["qualifies_pead_q5"]].to_csv(paths["q5"], index=False)
    dq.to_csv(paths["dq"], index=False)
    try:
        full.to_parquet(out_dir / "sue_ear_results.parquet", index=False)
        paths["parquet"] = out_dir / "sue_ear_results.parquet"
    except (ImportError, ValueError):
        pass                                 # parquet needs pyarrow/fastparquet; the CSV is the main output
    return paths


def summary(res: pd.DataFrame, dq: pd.DataFrame) -> dict:
    q5 = res[res["qualifies_pead_q5"]]
    allq = res[res["sue_quintile"].notna()]
    return {
        "announcements": int(res["announcement_date"].notna().sum()),
        "valid_sue": int(res["SUE"].notna().sum()),
        "valid_ear": int(res["EAR"].notna().sum()),
        "ranked": int(len(allq)),
        "q5_candidates": int(len(q5)),
        "q5_list": [(r.symbol, str(r.announcement_date)[:10], r.SUE, r.EAR)
                    for r in q5.sort_values(["announcement_date", "symbol"]).itertuples()],
        "q5_avg_car_21d": float(q5["forward_car_21d"].mean()) if len(q5) else float("nan"),
        "q5_avg_car_61d": float(q5["forward_car_61d"].mean()) if len(q5) else float("nan"),
        "all_avg_car_61d": float(allq["forward_car_61d"].mean()) if len(allq) else float("nan"),
        "dq_issues": int(len(dq)),
    }


def print_summary(s: dict) -> None:
    pct = lambda v: "n/a" if v != v else f"{v * 100:+.2f}%"
    print("india_pead_screener summary")
    print(kv([("announcements processed", f"{s['announcements']:,}"), ("valid SUE calculations", f"{s['valid_sue']:,}"),
              ("valid EAR calculations", f"{s['valid_ear']:,}"), ("ranked into quintiles", f"{s['ranked']:,}"),
              ("Q5 x Q5 candidates", f"{s['q5_candidates']:,}"),
              ("avg forward CAR, Q5 (t+2..t+21)", pct(s["q5_avg_car_21d"])),
              ("avg forward CAR, Q5 (t+2..t+61)", pct(s["q5_avg_car_61d"])),
              ("avg forward CAR, every ranked stock (t+2..t+61)", pct(s["all_avg_car_61d"])),
              ("data-quality issues", f"{s['dq_issues']:,}")]))
    lst = s.get("q5_list", [])
    if lst:
        more = f"; {len(lst) - 25} earlier ones in pead_q5_candidates.csv" if len(lst) > 25 else ""
        print(f"\nQ5 x Q5 candidates (latest {min(len(lst), 25)}{more})")
        print(box(pd.DataFrame([(i, sym, date, f"{sue:.2f}", f"{ear * 100:+.2f}%") for i, (sym, date, sue, ear) in
                                enumerate(lst[-25:], 1)], columns=["#", "symbol", "announced", "SUE", "EAR"])))
