"""india_pead_screener -- command line.

    python main.py --config config.yaml                 # CSVs in data/raw (sample templates are written if missing)
    python main.py --config config.yaml --source app    # the screener app's own data (run from backend/india_pead_screener)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from india_pead_screener.src import data_loader, reporting, screener, store  # noqa: E402


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SRWD SUE x EAR PEAD screen for Indian equities")
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--source", choices=["csv", "app"], default="csv")
    ap.add_argument("--metric", choices=["eps", "net_profit"], help="override srwd.metric")
    ap.add_argument("--db", help="SQLite file (default: output.sqlite in the config)")
    ap.add_argument("--export-csv", help="with --source app: also write the three input CSVs to this folder")
    a = ap.parse_args(argv)
    cfg = load_config(Path(a.config))
    base = Path(a.config).resolve().parent
    names = sectors = None
    if a.source == "app":
        d = data_loader.from_app(HERE.parent)
        fin, px, bench, names = d["fin"], d["px"], d["bench"], d["names"]
        sectors = dict(zip(fin["symbol"], fin["sector"]))
        print(f"app data: {fin['symbol'].nunique()} companies, {len(fin)} quarters, {px['symbol'].nunique()} price series "
              f"({d['excluded']} financials/utilities left out)")
        if a.export_csv:
            data_loader.export_csvs(d, Path(a.export_csv))
            print(f"input CSVs written to {a.export_csv}")
    else:
        raw = base / Path(cfg["data_paths"]["quarterly_financials"]).parent
        if not all((base / p).exists() for p in cfg["data_paths"].values()):
            print(f"input CSVs missing -- writing SYNTHETIC sample templates to {raw}")
            data_loader.write_sample_data(raw)
        fin, px, bench = data_loader.load_csvs(cfg["data_paths"], base)
        if "company_name" in fin.columns:        # CSVs exported from the app (fetch_data.py)
            names = dict(zip(fin["symbol"], fin["company_name"]))
            sectors = dict(zip(fin["symbol"], fin["sector"])) if "sector" in fin.columns else None
    if a.metric:
        cfg["srwd"]["metric"] = a.metric
    res, dq, fc = screener.run(cfg, fin, px, bench)
    out = base / cfg["output"]["dir"]
    reporting.write_outputs(res, dq, out)
    con = store.connect(Path(a.db) if a.db else base / cfg["output"]["sqlite"])
    saved = store.save(con, res, fc, dq, cfg, names, sectors)
    con.close()
    s = reporting.summary(res, dq)
    reporting.print_summary(s)
    print(f"  SQLite                  : {saved['rows']} scores stored, {saved['history_rows_added']} new/changed history rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
