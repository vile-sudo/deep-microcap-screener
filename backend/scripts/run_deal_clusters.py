"""Build the Alerts bell's "Deal clusters" -- unusually one-sided or unusually heavy bulk/block deal
activity in a stock -- from the deal history scripts/run_deals.py already collects.

    cd backend
    python scripts/run_deal_clusters.py

Reads data/deals/latest.json (the rolling deal history) and, for context only, data/announcements/
latest.json and chart_data/universe.json (today's close/chg% where available). Writes
data/deals/clusters.json: {"asof": ..., "sessions": [{"date": ..., "items": [...]}, ...]} for the last
SESSIONS_KEPT sessions present in the deal history -- the same shape chart_data/alerts.json uses, merged
in by app/routers/alerts.py, so these show up in the existing Alerts bell alongside new highs/lows and
breakouts. See app/deal_clusters.py for the rules.

Runs in .github/workflows/daily.yml right after the deals and announcements steps, so a burst of deals
today is flagged in the same run that fetched it -- no separate schedule needed, and nothing here re-hits
NSE. Safe to re-run any time (workflow_dispatch): pure computation over files already on disk.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
from app import deal_clusters  # noqa: E402

DEALS_FILE = BACKEND_DIR / "data" / "deals" / "latest.json"
ANNOUNCEMENTS_FILE = BACKEND_DIR / "data" / "announcements" / "latest.json"
UNIVERSE_FILE = BACKEND_DIR / "chart_data" / "universe.json"
OUT_FILE = BACKEND_DIR / "data" / "deals" / "clusters.json"
SESSIONS_KEPT = 15


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _market_lookup() -> dict:
    stocks = (_load(UNIVERSE_FILE, {}) or {}).get("stocks") or {}
    out = {}
    for key, v in stocks.items():
        if key.startswith("NSE-") or key.startswith("BOARD-"):
            sym = v.get("symbol")
            if sym and sym not in out:
                out[sym] = {"close": v.get("last"), "chg_pct": v.get("chg_pct")}
    return out


def main() -> int:
    deals = _load(DEALS_FILE, {}).get("deals") or []
    if not deals:
        print("run_deal_clusters: no deal history on file yet; nothing to do")
        return 0
    announcements = _load(ANNOUNCEMENTS_FILE, {}).get("announcements") or []
    market = _market_lookup()

    by_day = deal_clusters.build(deals, announcements, market)
    days = sorted(by_day)[-SESSIONS_KEPT:]
    sessions = [{"date": d, "items": by_day[d]} for d in days]

    payload = {"sessions": sessions}
    prev = _load(OUT_FILE, {})
    if {k: v for k, v in prev.items() if k != "fetched_at"} == payload:
        print("run_deal_clusters: nothing changed")
        return 0
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload},
                                   separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    latest = sessions[-1] if sessions else {"date": None, "items": []}
    n = len(latest["items"])
    by_type = {}
    for it in latest["items"]:
        by_type[it["type"]] = by_type.get(it["type"], 0) + 1
    print(f"run_deal_clusters: {sum(len(s['items']) for s in sessions)} items across {len(sessions)} sessions; "
          f"latest ({latest['date']}): {n} items {by_type}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
