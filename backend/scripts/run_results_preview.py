"""Results Preview, the nightly run (app/results_preview.py): refit the models on every reported quarter,
rerun the walk-forward backtest, predict the companies reporting in the next three weeks, and grade the
predictions whose results are now out.

    cd backend
    python scripts/run_results_preview.py

Runs in .github/workflows/daily.yml after the Deals history step (it reads that history's capacity and
order filings). Writes data/results_preview/latest.json (the page) and predictions.json (the scorecard log).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import results_preview  # noqa: E402


def main() -> int:
    t = time.time()
    out = results_preview.run()
    b = out["backtest"]
    print(f"results preview: {len(out['upcoming'])} upcoming, {out['pending']} logged awaiting results, "
          f"backtest {b['n']} company-quarters ({', '.join(b['quarters'])}) -- accelerate hit {b['accel'].get('hit')}% "
          f"vs {b['accel'].get('always_majority')}% always-majority, profit hit {b['profit'].get('hit')}% "
          f"vs {b['profit'].get('always_majority')}%, in {time.time() - t:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
