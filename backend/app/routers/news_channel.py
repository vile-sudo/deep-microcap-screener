"""
News Channel endpoints.

GET /api/news-channel                  the latest fetched feed
POST /api/admin/news-channel/run-now    admin: dispatch the fetch right now
                                        instead of waiting for the next
                                        scheduled run

Written by scripts/run_news_channel.py into backend/data/news_channel/latest.json
(see .github/workflows/news_channel.yml for the schedule, app/news_channel.py
for the rules); this router only reads it.
"""
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import github_dispatch, news_channel
from ..database import get_db
from ..models import MetaKV
from .auth import require_admin

router = APIRouter(tags=["news-channel"])
REFRESH_COOLDOWN = 15 * 60   # 3 newsdata.io requests -- normally done in well under a minute


@router.get("/api/news-channel")
def latest():
    return news_channel.load()


@router.post("/api/admin/news-channel/run-now")
def run_now(admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """Admin only: dispatch scripts/run_news_channel.py right now via GitHub
    Actions. Rate-limited server-side so repeat clicks don't chip away at
    the 200-request/day newsdata.io quota."""
    if not github_dispatch.configured():
        raise HTTPException(status_code=503, detail="Not set up: add GH_DISPATCH_TOKEN on the server (see README) to enable Run now.")

    row = db.get(MetaKV, "NEWS_CHANNEL_REFRESH")
    last = float(((row.value if row else None) or {}).get("last", 0))
    now = time.time()
    if now - last < REFRESH_COOLDOWN:
        wait = int(REFRESH_COOLDOWN - (now - last))
        raise HTTPException(status_code=429, detail=f"Already refreshed recently -- try again in about {max(1, wait // 60)} min.")

    try:
        github_dispatch.dispatch("news_channel.yml", {})
    except github_dispatch.DispatchError as e:
        raise HTTPException(status_code=502, detail=f"Could not start the refresh: {e}") from e

    if row is None:
        db.add(MetaKV(key="NEWS_CHANNEL_REFRESH", value={"last": now}))
    else:
        row.value = {"last": now}
    db.commit()
    return {"status": "queued", "cooldown_seconds": REFRESH_COOLDOWN}
