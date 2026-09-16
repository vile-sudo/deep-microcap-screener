"""
Movers: every NSE stock that closed 4%+ up or down, with why.

Ported from a standalone tool (github.com/vile-sudo/... "Project Trident")
into this dashboard largely unchanged -- the scan, corporate-action
adjustment, filing/news/deal attribution and verdict logic are the same,
proven modules. What changed crossing over:

  * bhavcopy.py's cache moved under backend/.movers_cache/ (gitignored,
    rebuildable), separate from the Chart gallery's own bhavcopy cache;
  * the universe is every equity series NSE's bhavcopy carries that day
    (main board, trade-for-trade and SME) -- no Nifty Total Market CSV
    to keep re-downloading;
  * company names for the news search come from this dashboard's own
    company records (board + Screen any Chart's universe) instead of a
    separate CSV;
  * SQLite is gone -- the daily JSON snapshot (backend/data/movers/) is
    the only record, matching how Sector Research and Reports store theirs;
  * the static-site generator is gone -- backend/app/routers/movers.py
    and the dashboard's own Movers page serve the snapshots instead.
"""
