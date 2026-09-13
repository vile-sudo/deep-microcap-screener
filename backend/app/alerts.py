"""
Price alerts for the latest session, written to chart_data/alerts.json by
scripts/update_charts.py and shown under the dashboard's Alerts bell.

For each board company (daily candles, split-adjusted):
  new high over a window  today's high  > the highest high of the previous N sessions
  new low over a window   today's low   < the lowest low  of the previous N sessions
      windows: 1D = 1 session, 1W = 5, 1M = 21, 6M = 126, 1Y = 250
      a company listed more recently than the window uses all its sessions
      ("since listing") when it has at least 5
  closed beyond           the close is also past that level (a decisive break)
  IPO base breakout       the IPO-base screen's breakout on this session (app/setups.py)
  base breakout           the VCP screen's pivot breakout on this session

Across the liquid NSE universe (not just the board): new 6-month and 1-year
highs and lows, and IPO-base breakouts.

The file keeps the last SESSIONS_KEPT sessions, so someone who hasn't opened
the dashboard for a few days still sees what they missed.
"""
from __future__ import annotations

import json
from pathlib import Path

WINDOWS = [("1D", 1), ("1W", 5), ("1M", 21), ("6M", 126), ("1Y", 250)]
MARKET_WINDOWS = [("6M", 126), ("1Y", 250)]
SESSIONS_KEPT = 15
MIN_LISTED = 5


def _extremes(rows, windows, listed):
    """Windows in which the last candle made a new high / low, with the level it broke."""
    t = len(rows) - 1
    if t < 1:
        return None
    d, o, h, l, c, v = rows[t][:6]
    out = {"high": [], "low": [], "high_level": None, "low_level": None}
    for name, n in windows:
        if t >= n:
            prior = rows[t - n:t]
        elif listed and t >= MIN_LISTED and name != "1D":
            prior = rows[:t]            # listed within the window: since listing
        else:
            continue
        ph, pl = max(r[2] for r in prior), min(r[3] for r in prior)
        if h > ph:
            out["high"].append(name)
            out["high_level"] = ph      # the longest window's level (loop runs short -> long)
        if l < pl:
            out["low"].append(name)
            out["low_level"] = pl
    return out


def _item(code, symbol, name, scope, kind, rows, windows=None, level=None, extra=None):
    t = len(rows) - 1
    c, v = rows[t][4], rows[t][5]
    prev = rows[t - 1][4] if t else None
    vols = [r[5] for r in rows[max(0, t - 50):t] if r[5]]
    it = {"code": code, "symbol": symbol, "name": name, "scope": scope, "type": kind,
          "close": round(c, 2), "chg_pct": round((c / prev - 1) * 100, 2) if prev else None,
          "vol_x": round(v / (sum(vols) / len(vols)), 1) if vols and v else None}
    if windows:
        it["windows"] = windows
        it["top"] = windows[-1]
        it["level"] = round(level, 2) if level is not None else None
        it["closed_beyond"] = bool(level is not None and ((kind == "high" and c > level) or (kind == "low" and c < level)))
    if extra:
        it.update(extra)
    return it


def board_alerts(records: list[dict], series: dict, stocks: dict, latest: str) -> list[dict]:
    items = []
    for rec in records:
        code = rec["code"]
        s = series.get(code)
        if not s or not s["rows"] or s["rows"][-1][0] != latest:
            continue
        rows, listed = s["rows"], s.get("listed")
        ext = _extremes(rows, WINDOWS, listed)
        if ext:
            for kind in ("high", "low"):
                if ext[kind]:
                    since = listed and len(rows) - 1 < dict(WINDOWS)[ext[kind][-1]]
                    items.append(_item(code, s["symbol"], rec.get("name") or code, "board", kind, rows, ext[kind],
                                       ext[f"{kind}_level"], {"since_listing": bool(since)} if since else None))
        a = stocks.get(code) or {}
        ipo_bo = (a.get("ipo") or {}).get("breakout")
        if (a.get("ipo") or {}).get("stage") == "fresh" and ipo_bo and ipo_bo.get("sessions") == 0:
            items.append(_item(code, s["symbol"], rec.get("name") or code, "board", "ipo_breakout", rows,
                               extra={"pivot": ipo_bo.get("pivot"), "vol_x": ipo_bo.get("vol_x"), "listed": (a.get("ipo") or {}).get("listed")}))
        bo = a.get("breakout")
        if a.get("stage") == "fresh" and bo and bo.get("sessions") == 0:
            items.append(_item(code, s["symbol"], rec.get("name") or code, "board", "vcp_breakout", rows,
                               extra={"pivot": bo.get("pivot"), "vol_x": bo.get("vol_x")}))
    return items


def market_alerts(universe: dict, names: dict, listed: dict, market_ipo: list[dict], board_symbols: set, latest: str) -> list[dict]:
    items = []
    for key, rows in universe.items():
        if ("NSE", key) in board_symbols or not rows or rows[-1][0] != latest:
            continue
        ext = _extremes(rows, MARKET_WINDOWS, key in listed)
        if not ext:
            continue
        name = (names.get(key) or key).title()
        for kind in ("high", "low"):
            if ext[kind]:
                items.append(_item(None, key, name, "market", kind, rows, ext[kind], ext[f"{kind}_level"]))
    for hit in market_ipo:
        if hit.get("on_board"):
            continue
        rows = universe.get(hit["symbol"])
        if rows:
            items.append(_item(None, hit["symbol"], hit["name"], "market", "ipo_breakout", rows,
                               extra={"pivot": hit.get("pivot"), "vol_x": hit.get("vol_x"), "listed": hit.get("listed")}))
    return items


def write(path: Path, latest: str, items: list[dict]) -> dict:
    """Replace this session in the file and keep the last SESSIONS_KEPT sessions."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    order = {"ipo_breakout": 0, "vcp_breakout": 1, "high": 2, "low": 3}
    rank = {name: i for i, (name, _) in enumerate(WINDOWS)}
    items.sort(key=lambda x: (x["scope"] != "board", order[x["type"]], -rank.get(x.get("top"), 0), -(x.get("vol_x") or 0)))
    sessions = [s for s in doc.get("sessions", []) if s.get("date") != latest]
    sessions.append({"date": latest, "items": items})
    sessions = sorted(sessions, key=lambda s: s["date"])[-SESSIONS_KEPT:]
    doc = {"latest": latest, "windows": [w for w, _ in WINDOWS], "market_windows": [w for w, _ in MARKET_WINDOWS],
           "sessions": sessions}
    path.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    return doc
