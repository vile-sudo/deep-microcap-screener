"""Build the Alerts bell's "Insider clusters" -- several different insiders (promoters/directors/KMPs)
buying, or selling, the same stock within a rolling week -- from the insider trading history
scripts/run_insider_trades.py already collects.

    cd backend
    python scripts/run_insider_clusters.py

Reads data/insider_trades/latest.json and, for context only, data/announcements/latest.json and
chart_data/universe.json (today's close/chg% where available). Writes data/insider_trades/clusters.json:
{"fetched_at": ..., "sessions": [{"date": ..., "items": [...]}, ...]} for the last SESSIONS_KEPT sessions
present in the insider trading history -- the same shape data/deals/clusters.json uses, merged in by
app/routers/alerts.py, so these show up in the existing Alerts bell alongside deal clusters, new
highs/lows and breakouts. See app/insider_clusters.py for the rules.

Runs in .github/workflows/daily.yml and deals_evening.yml right after the insider trading step, so a
newly-crossed threshold is flagged in the same run that fetched it. Safe to re-run any time
(workflow_dispatch): pure computation over files already on disk.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
from app import insider_clusters  # noqa: E402

TRADES_FILE = BACKEND_DIR / "data" / "insider_trades" / "latest.json"
ANNOUNCEMENTS_FILE = BACKEND_DIR / "data" / "announcements" / "latest.json"
UNIVERSE_FILE = BACKEND_DIR / "chart_data" / "universe.json"
OUT_FILE = BACKEND_DIR / "data" / "insider_trades" / "clusters.json"
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
    trades = _load(TRADES_FILE, {}).get("trades") or []
    if not trades:
        print("run_insider_clusters: no insider trading history on file yet; nothing to do")
        return 0
    announcements = _load(ANNOUNCEMENTS_FILE, {}).get("announcements") or []
    market = _market_lookup()

    by_day = insider_clusters.build(trades, announcements, market)
    days = sorted(by_day)[-SESSIONS_KEPT:]
    sessions = [{"date": d, "items": by_day[d]} for d in days]

    payload = {"sessions": sessions}
    prev = _load(OUT_FILE, {})
    if {k: v for k, v in prev.items() if k != "fetched_at"} == payload:
        print("run_insider_clusters: nothing changed")
        return 0
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload},
                                   separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    latest = sessions[-1] if sessions else {"date": None, "items": []}
    n = len(latest["items"])
    by_type = {}
    for it in latest["items"]:
        by_type[it["type"]] = by_type.get(it["type"], 0) + 1
    print(f"run_insider_clusters: {sum(len(s['items']) for s in sessions)} items across {len(sessions)} sessions; "
          f"latest ({latest['date']}): {n} items {by_type}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
