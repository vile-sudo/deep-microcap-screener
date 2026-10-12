"""Sector Strength, through the day: a 5-minute record of every sector while the market is open.

The live view (app/sector_live.py) only ever holds "now". This keeps a snapshot every five minutes -- each
sector's median move, share of stocks up and value traded so far, and the Nifty 500's move, breadth and value
-- so the page can show how the day unfolded (a gap-up that faded, a steady climb, a late surge), and any past
day kept here can be looked at again.

It needs Zerodha connected (the daily login in app/kite.py). A background loop in main.py calls tick() every
minute; tick() refreshes the live snapshot when it's due (sector_live.snapshot() already makes sure only one
gunicorn worker asks Zerodha) and appends a point when a new 5-minute slot has started, plus one at 15:30 from
the closing prices. One file per day in data/sector_intraday/ -- on the server's data volume, never in git
(each day is ~50 KB).
"""
from __future__ import annotations

import json
import os
import statistics
import time
from datetime import datetime
from pathlib import Path

from . import kite
from .sector_strength import BACKEND_DIR

DIR = BACKEND_DIR / "data" / "sector_intraday"
SLOT_MINUTES = 5
CLOSE = "15:30"
LOCK = DIR / ".lock"


def _slot(now: datetime) -> str:
    m = (now.minute // SLOT_MINUTES) * SLOT_MINUTES
    return f"{now.hour:02d}:{m:02d}"


def _point(snap: dict) -> dict | None:
    """One point: per sector [median move %, % of stocks up, value Rs cr so far], and the Nifty 500 the same way."""
    from .sector_live import _members
    _, _, _, groups = _members()
    moves, svalue = snap.get("moves") or {}, snap.get("sector_value") or {}
    if not moves:
        return None
    sectors = {}
    for slug, keys in groups.items():
        v = [moves[k] for k in keys if k in moves]
        if v:
            sectors[slug] = [round(statistics.median(v), 2), round(100 * sum(x > 0 for x in v) / len(v), 1), round(svalue.get(slug) or 0, 1)]
    b = snap.get("benchmark") or {}
    br = b.get("breadth") or {}
    n500 = [b.get("chg"), round(100 * br["up"] / br["n"], 1) if br.get("n") else None, round(b.get("value_cr") or 0, 1)]
    return {"sectors": sectors, "nifty500": n500}


def _write(path: Path, day: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(day, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def record(snap: dict, now: datetime | None = None) -> str | None:
    """Add the snapshot to today's file if its 5-minute slot (or the 15:30 close) isn't there yet; returns the
    slot written, or None."""
    now = (now or datetime.now(kite.IST)).astimezone(kite.IST)
    if snap.get("status") not in ("live", "closed") or snap.get("date") != now.date().isoformat():
        return None
    slot = _slot(now) if snap["status"] == "live" else CLOSE
    if slot > CLOSE:
        slot = CLOSE
    DIR.mkdir(parents=True, exist_ok=True)
    try:                                             # one worker at a time
        if time.time() - LOCK.stat().st_mtime > 120:
            LOCK.unlink()
    except OSError:
        pass
    try:
        os.close(os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return None
    try:
        path = DIR / f"{now.date().isoformat()}.json"
        try:
            day = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            day = {"date": now.date().isoformat(), "times": [], "sectors": {}, "nifty500": []}
        if slot in day["times"] or (day["times"] and day["times"][-1] >= CLOSE):
            return None
        pt = _point(snap)
        if not pt:
            return None
        k = len(day["times"])
        day["times"].append(slot)
        for slug, row in pt["sectors"].items():
            day["sectors"].setdefault(slug, [None] * k).append(row)
        for slug, rows in day["sectors"].items():       # a sector without a reading this slot keeps the series aligned
            if len(rows) < k + 1:
                rows.append(None)
        day["nifty500"].append(pt["nifty500"])
        _write(path, day)
        return slot
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


def tick() -> str | None:
    """Called every minute: during the session (and for a few minutes after 15:30, for the close) refresh the
    live snapshot if it's due and record it."""
    now = datetime.now(kite.IST)
    mins = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not (9 * 60 + 15 <= mins <= 15 * 60 + 40) or not kite.configured() or not kite.session():
        return None
    from . import sector_live
    return record(sector_live.snapshot(), now)


def days() -> list[str]:
    if not DIR.exists():
        return []
    return sorted(p.stem for p in DIR.glob("????-??-??.json"))


def load(date: str) -> dict | None:
    try:
        return json.loads((DIR / f"{date}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
