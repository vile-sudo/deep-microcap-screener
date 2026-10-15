# india_pead_screener

A SRWD-SUE × EAR screen for **post-earnings-announcement drift (PEAD)** in Indian equities. It powers the
dashboard's *SRWD SUE & EAR calculators* (Results Preview → Earnings drift strategy) and stores every score
in SQLite.

## What PEAD is

Prices under-react to earnings news. Stocks whose results beat their own trend by the most, *and* that the
market bid up hardest around the announcement, tend to keep drifting up for weeks (Bernard & Thomas 1989;
[Quantpedia](https://quantpedia.com/strategies/post-earnings-announcement-effect)). The screen buys the
intersection: SUE quintile 5 and EAR quintile 5.

## SRWD SUE

There are no analyst estimates: the expectation is a seasonal random walk with drift.

```
drift_t      = (1/N) Σ_{j=1..N} (E_{t-j} − E_{t-j-4})      N = 4 by default (2–8)
expected_t   = E_{t-4} + drift_t
raw_surprise = E_t − expected_t
σ_t          = sample std (ddof=1) of the stock's own earlier SRWD forecast errors
               (E_k − expected_k for quarters k < t announced before quarter t; at least 4)
SUE_t        = raw_surprise / σ_t, winsorized to [−10, 10]
```

- **Metric.** EPS is the default. Net-profit SUE is supported and labelled `metric = net_profit` in every output. The app uses net profit, because NSE's XBRL history gives net profit for every quarter back to 2019, while split- and bonus-consistent EPS is not available for most companies.
- **Quarters.** Quarters sit on a continuous quarterly index, so a missing quarter returns NaN with a reason instead of pairing the wrong periods.
- **History required.** Stocks need 16 quarters of history by default (`min_quarters_history`).
- **Negative earnings.** With `exclude_negative_eps`, a loss in the quarter, or in the same quarter a year earlier, gives NaN.

## EAR

```
stock_return_3d     = adj_close[t0+1] / adj_close[t0−1] − 1
benchmark_return_3d = bench[t0+1]     / bench[t0−1]     − 1
EAR                 = stock_return_3d − benchmark_return_3d
```

`t0` is the first session that can trade on the result:
- a non-trading announcement day maps to the next trading day;
- an `announcement_time` at or after 15:30 maps to the next session. Most Indian results are filed after the close.

All three dates need prices. Only adjusted closes are used: rows without one are dropped with a warning and never replaced by raw prices.

## Why quintiles are cross-sectional

A surprise is "big" relative to what other companies reported at the same time, not against a fixed
threshold. `group_by` sets that universe:

- `date`: the strict default, one group per `announcement_date_used`.
- `week`: same-week reporters.
- `season`: the fiscal quarter.

A group needs at least 20 eligible stocks, otherwise it gets no quintiles. Inside it, SUE and EAR are ranked separately and cut into five equal groups with `pd.qcut` on ranks. The app uses `week`: on most Indian results days fewer than 20 eligible stocks report (after the ₹1 cr turnover and ₹100 price filters). Weekly groups rank about 94% of scored results, against about 44% for daily groups.

## Inputs (data/raw/)

| file | required columns |
|---|---|
| quarterly_financials.csv | symbol, fiscal_year, fiscal_quarter, quarter_end_date, announcement_date, reported_eps, net_profit, shares_outstanding, exceptional_items, tax_adjustments (optional: announcement_time HH:MM) |
| daily_prices.csv | symbol, date, close_price, adjusted_close_price (optional: turnover in ₹ for the liquidity filter) |
| benchmark_prices.csv | date, benchmark_close |

If they are missing, `main.py` writes **synthetic** templates. These are random data for testing, not market data.

## Run

```
pip install -r requirements.txt
python main.py --config config.yaml                       # CSVs
python main.py --config config_app.yaml --source app      # the screener app's own data (from backend/)
python -m pytest -q tests
```

Nightly, `backend/scripts/run_pead_engine.py` runs the app configuration. It then publishes the SQLite as a release asset, which the dashboard's calculators read.

## Output

| file | contents |
|---|---|
| `sue_ear_results.csv` (+ `.parquet` when pyarrow is installed) | every symbol-quarter: SUE and its parts, EAR and its window, quintiles, `qualifies_pead_q5`, `pead_signal`, forward CARs, `data_quality_flag` |
| `pead_q5_candidates.csv` | the intersection only |
| `data_quality_report.csv` | issue, symbol, date, explanation |
| SQLite | described below |

SQLite tables:
- `scores`: the latest values.
- `sue_history`: a new row whenever a score first appears or changes, which is the point-in-time record.
- `forecasts`: next quarter's expected value and σ. Profit needed for a SUE of *k* = expected + *k*·σ.
- `cutoffs`: the Q5 entry levels per group.
- `names`, `data_quality`, `runs`, `meta`.

**Signals.**
- `long_only`: only `LONG_Q5` is produced.
- `long_short`: `SHORT_Q1` (bottom quintile on both) is produced as well.

**Forward returns.** `forward_car_21d` and `forward_car_61d` measure stock minus benchmark from the close of t0+1 to the close of t0+21 and t0+61, which is the holding period with entry on day +2. They are for research only and never feed the signal.

## Known limitations (India)

- **Filing times decide t0.** An after-hours filing reacts the next session. Wrong or missing times shift EAR by a day.
- **Data sources.** Quarterly numbers come from screener.in, plus NSE XBRL before Sep 2023. The XBRL is kept only where it matches screener on the overlapping quarters. Restatements change history; `sue_history` keeps what was seen when.
- **Prices.** Prices are split- and bonus-adjusted, and the price checks flag remaining jumps over 40% (`adjusted_price_gap`). Rights issues are only partly adjusted.
- **Exclusions.** Banks, NBFCs, insurers and utilities are excluded, because their earnings follow different accounting.
- **Seasonality.** SRWD assumes the same quarter repeats. Lumpy order-book businesses and one-offs (exceptional items, tax write-backs) inflate σ or the surprise. Use `data_quality_flag` and the result itself before acting.
- **Short history.** Only about three years of announcements can be scored, because 16 quarters of history are needed.
- **Survivorship bias.** Delisted companies are missing.
