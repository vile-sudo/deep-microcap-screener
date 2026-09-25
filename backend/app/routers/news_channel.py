"""
News Channel endpoints.

GET /api/news-channel                  the latest fetched feed (last 48 hours)
GET /api/news-channel/dates            the days the archive holds, newest first, with a count each
GET /api/news-channel/archive/{date}   every item published that India (IST) day, YYYY-MM-DD
GET /api/news-channel/archive?days=N   the last N days together (1-90), for searching history
POST /api/admin/news-channel/run-now    admin: dispatch the fetch right now
                                        instead of waiting for the next
                                        scheduled run
POST /api/cron/news-channel             shared-secret: same dispatch, for an
                                        external cron pinger -- see cron_key
                                        in config.py for why this exists

Written by scripts/run_news_channel.py into backend/data/news_channel/latest.json
(see .github/workflows/news_channel.yml for the schedule, app/news_channel.py
for the rules); this router only reads it.
"""
import hmac
import json
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import github_dispatch, news_channel
from ..config import get_settings
from ..database import get_db
from ..models import MetaKV
from .auth import require_admin

router = APIRouter(tags=["news-channel"])
REFRESH_COOLDOWN = 15 * 60   # human clicks: 3 newsdata.io requests, normally done in well under a minute
CRON_COOLDOWN = 4 * 60       # machine pings: a little under the external timer's own 5-minute interval,
                             # so a slightly-early tick still goes through but a misconfigured hammer can't


def _dispatch_if_due(db: Session, kv_key: str, cooldown: int) -> dict:
    if not github_dispatch.configured():
        raise HTTPException(status_code=503, detail="Not set up: add GH_DISPATCH_TOKEN on the server (see README) to enable this.")

    row = db.get(MetaKV, kv_key)
    last = float(((row.value if row else None) or {}).get("last", 0))
    now = time.time()
    if now - last < cooldown:
        wait = int(cooldown - (now - last))
        raise HTTPException(status_code=429, detail=f"Already refreshed recently -- try again in about {max(1, wait // 60)} min.")

    try:
        github_dispatch.dispatch("news_channel.yml", {})
    except github_dispatch.DispatchError as e:
        raise HTTPException(status_code=502, detail=f"Could not start the refresh: {e}") from e

    if row is None:
        db.add(MetaKV(key=kv_key, value={"last": now}))
    else:
        row.value = {"last": now}
    db.commit()
    return {"status": "queued", "cooldown_seconds": cooldown}


@router.get("/api/news-channel")
def latest():
    return news_channel.load()


_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_cache: dict[str, tuple[float, dict]] = {}     # path -> (mtime, parsed): the day files are ~100 KB each


def _day_file(day: str) -> dict | None:
    path = news_channel.ARCHIVE_DIR / f"{day}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _cache.get(day)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    _cache[day] = (mtime, data)
    return data


@router.get("/api/news-channel/dates")
def archive_dates():
    days = sorted((p.stem for p in news_channel.ARCHIVE_DIR.glob("*.json") if _DAY.match(p.stem)), reverse=True)
    out = []
    for d in days:
        data = _day_file(d)
        if data:
            out.append({"date": d, "count": data.get("count", len(data.get("items") or []))})
    return {"days": out}


@router.get("/api/news-channel/archive")
def archive_range(days: int = Query(7, ge=1, le=90)):
    today = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    items = []
    for i in range(days):
        data = _day_file((today - timedelta(days=i)).strftime("%Y-%m-%d"))
        if data:
            items.extend(data.get("items") or [])
    items.sort(key=lambda x: x.get("published") or "", reverse=True)
    return {"days": days, "count": len(items), "items": items}


@router.get("/api/news-channel/archive/{day}")
def archive_day(day: str):
    if not _DAY.match(day):
        raise HTTPException(status_code=400, detail="Use a date like 2026-09-25")
    data = _day_file(day)
    if data is None:
        return {"date": day, "count": 0, "items": []}
    return data


@router.post("/api/admin/news-channel/run-now")
def run_now(admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """Admin only: dispatch scripts/run_news_channel.py right now via GitHub
    Actions. Rate-limited server-side so repeat clicks don't chip away at
    the 200-request/day newsdata.io quota."""
    return _dispatch_if_due(db, "NEWS_CHANNEL_REFRESH", REFRESH_COOLDOWN)


@router.post("/api/cron/news-channel")
def cron_ping(key: str = "", db: Session = Depends(get_db)):
    """For an external cron service, not the dashboard -- GitHub's own
    `schedule:` trigger in news_channel.yml doesn't fire reliably on a tight
    interval (see cron_key in config.py), so a real external timer calls
    this instead, every 5 minutes. Same dispatch as admin "Refresh now",
    gated by a shared secret instead of a login."""
    configured = get_settings().cron_key
    if not configured:
        raise HTTPException(status_code=503, detail="Not set up: add CRON_KEY on the server (see README) to enable the external pinger.")
    if not hmac.compare_digest(key or "", configured):
        raise HTTPException(status_code=403, detail="Wrong or missing key")
    return _dispatch_if_due(db, "NEWS_CHANNEL_CRON", CRON_COOLDOWN)
