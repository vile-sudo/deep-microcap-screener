"""US-board alerts, computed on the server so they can reach a phone.

The US page's alert bell (frontend/static/us.js, buildAlerts) works out its list in the
browser from the files the nightly jobs write. This module applies the SAME rules on the
server, and pushes the ones that are new to the devices of the users who have that
company on their US watchlist -- so an earnings date, an insider purchase or a material
8-K on a company you follow reaches the mobile app without opening the site.

Rules (keep in step with buildAlerts in us.js):
  * earnings      the company reports in the next 7 days
  * insider buy   an open-market purchase of $25k or more in the last 14 days
  * 8-K filing    a material 8-K (results, order win, M&A, management change,
                  regulatory or legal, fundraise) in the last 7 days
  * price signal  the chart job's feed (new 52-week high/low, breakout) for a board name

Which alerts have already been pushed is remembered in MetaKV (US_ALERTS_NOTIFIED). The
first run only records what exists, so switching this on never floods anyone with the
backlog. Nothing is sent unless Firebase is configured (push.py) and the user has a
registered device. Runs every 15 minutes from main.py; reading four small files is cheap.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from . import push
from .config import BASE_DIR
from .models import Company, DeviceToken, MetaKV, UserSettings, WatchItem

log = logging.getLogger("deepsweep.us_alerts")

STATE_KEY = "US_ALERTS_NOTIFIED"
KEEP_IDS = 3000
MATERIAL_8K = {"results", "order win", "M&A", "management change", "regulatory or legal", "fundraise"}
DATA = BASE_DIR / "data"
SETUPS = BASE_DIR / "chart_data_us" / "setups.json"
LOCK_FILE = DATA / ".us_alerts.lock"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _days_since(iso: str, today: date) -> int:
    return (today - date.fromisoformat(str(iso)[:10])).days


def _compact(v: float) -> str:
    return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${round(v / 1e3)}k" if v >= 1e3 else f"${round(v)}"


def compute_alerts(board: dict[str, str], today: date | None = None) -> list[dict]:
    """board: code -> company name. Returns [{id, code, kind, title, sub}]."""
    today = today or datetime.now(timezone.utc).date()
    out: list[dict] = []

    for code, e in (_load(DATA / "earnings_us" / "latest.json").get("companies") or {}).items():
        n = (e or {}).get("next")
        if code not in board or not n or not n.get("date"):
            continue
        d = -_days_since(n["date"], today)
        if 0 <= d <= 7:
            when = "today" if d == 0 else "tomorrow" if d == 1 else f"in {d} days"
            out.append({"id": f"earn:{code}:{n['date']}", "code": code, "kind": "Earnings",
                        "title": f"{board[code]} reports {when}", "sub": n["date"]})

    for t in _load(DATA / "insider_us" / "latest.json").get("transactions") or []:
        if t.get("symbol") in board and t.get("is_market") and t.get("side") == "BUY" and (t.get("value_usd") or 0) >= 25000 \
                and _days_since(t["trade_date"], today) <= 14:
            out.append({"id": f"ins:{t.get('accession')}:{t.get('insider')}:{t.get('shares')}", "code": t["symbol"],
                        "kind": "Insider buy", "title": f"{t.get('insider')} bought {_compact(t['value_usd'])} of {t['symbol']}",
                        "sub": t["trade_date"]})

    for a in _load(DATA / "announcements_us" / "latest.json").get("announcements") or []:
        if a.get("symbol") in board and a.get("category") in MATERIAL_8K and _days_since(a["date"], today) <= 7:
            out.append({"id": f"ann:{a.get('accession')}:{a['symbol']}", "code": a["symbol"], "kind": "8-K filing",
                        "title": f"{a.get('name') or a['symbol']}: {a['category']}", "sub": a["date"]})

    setups = _load(SETUPS)
    for f in setups.get("feed") or []:
        if f.get("code") in board:
            out.append({"id": f"feed:{f['code']}:{f.get('kind')}:{setups.get('as_of')}", "code": f["code"],
                        "kind": "Price signal", "title": f"{f.get('name') or f['code']} {f.get('text', '')}".strip(),
                        "sub": str(setups.get("as_of"))})
    return out


def _take_lock() -> bool:
    """Both gunicorn workers run the loop; only one may check at a time or a new alert would be
    pushed twice. A lock older than five minutes is a crashed holder's and is taken over."""
    try:
        os.close(os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        try:
            if time.time() - LOCK_FILE.stat().st_mtime > 300:
                os.utime(LOCK_FILE)
                return True
        except OSError:
            pass
        return False
    except OSError:
        return False


def notify_new(db: Session) -> int:
    """Push the alerts not sent before. Returns how many notifications went out."""
    if not _take_lock():
        return 0
    try:
        return _notify_new(db)
    finally:
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass


def _notify_new(db: Session) -> int:
    board = {c.code: c.name for c in db.query(Company.code, Company.name).filter(Company.market == "US")}
    alerts = compute_alerts(board)
    row = db.get(MetaKV, STATE_KEY)
    if row is None:                                    # first run: remember what exists, send nothing
        db.add(MetaKV(key=STATE_KEY, value=[a["id"] for a in alerts][-KEEP_IDS:]))
        db.commit()
        return 0
    known = set(row.value or [])
    fresh = [a for a in alerts if a["id"] not in known]
    if not fresh:
        return 0
    row.value = (list(row.value or []) + [a["id"] for a in fresh])[-KEEP_IDS:]
    db.commit()
    if not push.configured():
        return 0

    # who follows what, and who has a phone registered and has not switched notifications off
    devices = {uid for (uid,) in db.query(DeviceToken.user_id).distinct()}
    off = {s.user_id for s in db.query(UserSettings) if (s.prefs or {}).get("us_alerts_notify") is False}
    follows: dict[int, set[str]] = {}
    for uid, code in db.query(WatchItem.user_id, WatchItem.code).filter(WatchItem.market == "US"):
        if uid in devices and uid not in off:
            follows.setdefault(uid, set()).add(code)

    sent = 0
    for uid, codes in follows.items():
        mine = [a for a in fresh if a["code"] in codes]
        if not mine:
            continue
        title = mine[0]["title"] if len(mine) == 1 else f"{len(mine)} new alerts on your US watchlist"
        body = mine[0]["sub"] if len(mine) == 1 else "; ".join(a["title"] for a in mine[:3]) + ("…" if len(mine) > 3 else "")
        sent += push.send_to_users(db, [uid], title, body, {"type": "us_alert", "code": mine[0]["code"]})
    log.info("us alerts: %d new, %d notifications sent", len(fresh), sent)
    return sent
