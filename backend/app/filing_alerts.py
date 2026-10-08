"""Near-live push notifications for new NSE corporate filings, by category, per user.

Each signed-in user picks NSE filing categories ("Bagging/Receiving of orders/contracts", "Credit
Rating", ...) in the Announcements tab's "Get notified" panel; they're saved as `ann_notify` in their
prefs (routers/account.py). This loop polls NSE's corporate-announcements feed itself -- every
MARKET_SECONDS on weekdays during market hours (IST), every OFF_HOURS_SECONDS otherwise, so results
filed in the evening still arrive -- and pushes each user the new filings in their categories, for
every listed company, to every browser they've enabled notifications in (app/webpush.py).

Several gunicorn workers run this loop at once: a lock file lets one of them work at a time, and the
shared "last fetched" time in MetaKV keeps them to one fetch per interval. The very first run only
records what's already there, so turning this on never replays a day's backlog.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .database import SessionLocal

log = logging.getLogger("deepsweep")

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_SECONDS = 180
OFF_HOURS_SECONDS = 1800
MARKET_HOURS = ((9, 0), (16, 0))
STATE_KEY = "FILING_ALERTS"
CATEGORIES_KEY = "FILING_CATEGORIES"
LOCK_FILE = Path(tempfile.gettempdir()) / "deepsweep-filing-alerts.lock"
LOCK_STALE_SECONDS = 600
MAX_LISTED = 4      # filings named in a multi-filing notification before "and N more"


def interval_seconds(now: datetime) -> int:
    t = (now.hour, now.minute)
    return MARKET_SECONDS if now.weekday() < 5 and MARKET_HOURS[0] <= t < MARKET_HOURS[1] else OFF_HOURS_SECONDS


def _lock() -> bool:
    try:
        if time.time() - LOCK_FILE.stat().st_mtime > LOCK_STALE_SECONDS:
            LOCK_FILE.unlink()
    except OSError:
        pass
    try:
        os.close(os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


def _unlock() -> None:
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


def fetch_filings(now: datetime) -> list[dict]:
    """Yesterday's and today's filings (IST), so one filed just before midnight isn't missed."""
    from .movers.announcements import ANNOUNCEMENTS_URL
    from .movers.nse import NseClient, as_records
    start, end = (now - timedelta(days=1)).strftime("%d-%m-%Y"), now.strftime("%d-%m-%Y")
    out = []
    for r in as_records(NseClient(retries=2).get_json(ANNOUNCEMENTS_URL.format(start=start, end=end))):
        seq, symbol = str(r.get("seq_id") or ""), str(r.get("symbol") or "").strip()
        if not seq or not symbol:
            continue
        out.append({"seq": seq, "symbol": symbol, "name": str(r.get("sm_name") or symbol).strip(),
                    "category": str(r.get("desc") or "").strip() or "Other",
                    "text": re.sub(r"\s+", " ", str(r.get("attchmntText") or "")).strip(),
                    "url": str(r.get("attchmntFile") or "").strip()})
    return out


def message_for(filings: list[dict]) -> tuple[str, str, str, str]:
    """(title, body, click URL, tag) for one user's batch of new filings."""
    if len(filings) == 1:
        f = filings[0]
        body = f"{f['name']}: {f['text']}" if f["text"] else f["name"]
        return (f"{f['symbol']} · {f['category']}", body[:180], f["url"] or "/#v=deals&dt=ann", "filing-" + f["seq"])
    by_cat: dict[str, list[str]] = {}
    for f in filings:
        by_cat.setdefault(f["category"], []).append(f["symbol"])
    parts = [f"{cat}: {', '.join(syms[:MAX_LISTED])}{f' +{len(syms) - MAX_LISTED}' if len(syms) > MAX_LISTED else ''}"
             for cat, syms in sorted(by_cat.items(), key=lambda kv: -len(kv[1]))]
    return f"{len(filings)} new filings", " · ".join(parts)[:180], "/#v=deals&dt=ann", "filings"


def tick(now: datetime | None = None) -> int:
    """One pass: fetch if due, push each subscriber their new filings. Returns pushes sent."""
    from . import webpush
    from .models import MetaKV, UserSettings, WebPushSubscription

    now = now or datetime.now(IST)
    if not _lock():
        return 0
    db = SessionLocal()
    try:
        row = db.get(MetaKV, STATE_KEY)
        state = dict(row.value) if row else {}
        if time.time() - float(state.get("last_fetch") or 0) < interval_seconds(now):
            return 0
        state["last_fetch"] = time.time()   # recorded even on failure, so an NSE outage isn't retried every minute
        try:
            filings = fetch_filings(now)
        except Exception:
            if row is None:
                db.add(MetaKV(key=STATE_KEY, value=state))
            else:
                row.value = state
            db.commit()
            raise

        cats = db.get(MetaKV, CATEGORIES_KEY)
        counts = dict(cats.value) if cats else {}
        seen_before = set(state.get("seen") or [])
        new = [f for f in filings if f["seq"] not in seen_before]
        for f in new:
            counts[f["category"]] = counts.get(f["category"], 0) + 1
        first_run = "seen" not in state
        state["seen"] = sorted({f["seq"] for f in filings})   # only the two days fetched: stays small
        if row is None:
            db.add(MetaKV(key=STATE_KEY, value=state))
        else:
            row.value = state
        if cats is None:
            db.add(MetaKV(key=CATEGORIES_KEY, value=counts))
        else:
            cats.value = counts
        db.commit()
        if first_run or not new:
            return 0

        users_with_browsers = {uid for (uid,) in db.query(WebPushSubscription.user_id)
                               .filter(WebPushSubscription.user_id.isnot(None)).distinct()}
        sent = 0
        for s in db.query(UserSettings).filter(UserSettings.user_id.in_(users_with_browsers)).all():
            wanted = set((s.prefs or {}).get("ann_notify") or [])
            mine = [f for f in new if f["category"] in wanted]
            if mine:
                title, body, url, tag = message_for(mine)
                sent += webpush.send_to_user_browsers(db, s.user_id, title, body, url=url, tag=tag)
        if sent:
            log.info("filing alerts: %d new filing(s), %d push(es) sent", len(new), sent)
        return sent
    finally:
        db.close()
        _unlock()


def known_categories() -> list[dict]:
    """Every NSE category seen so far (by the poller, plus the stored announcements feed), most common first."""
    import json

    from .config import BASE_DIR
    from .models import MetaKV

    counts: dict[str, int] = {}
    try:
        feed = json.loads((BASE_DIR / "data" / "announcements" / "latest.json").read_text(encoding="utf-8"))
        for a in feed.get("announcements") or []:
            if a.get("category"):
                counts[a["category"]] = counts.get(a["category"], 0) + 1
    except (OSError, ValueError):
        pass
    db = SessionLocal()
    try:
        row = db.get(MetaKV, CATEGORIES_KEY)
        for c, n in ((row.value if row else None) or {}).items():
            counts[c] = counts.get(c, 0) + int(n)
    finally:
        db.close()
    return [{"category": c, "count": n} for c, n in sorted(counts.items(), key=lambda kv: kv[0].lower())]
