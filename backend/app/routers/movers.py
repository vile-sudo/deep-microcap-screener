"""
Movers endpoints.

GET /api/movers              the latest scanned session, plus the list of
                             every date on record (for a date picker)
GET /api/movers/{date}       one specific session, e.g. 2026-09-10
POST /api/admin/movers/run-now   admin: dispatch tonight's scan right now
                             instead of waiting for the 2 AM run

Snapshots are written by scripts/run_movers.py into backend/data/movers/,
one JSON file per trading day (see app/movers/scan.py for the shape); this
router only reads them.
"""
import json
import os
import re
import time
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import github_dispatch
from ..config import BASE_DIR
from ..database import get_db
from ..models import MetaKV
from .auth import require_admin

router = APIRouter(tags=["movers"])
DATA_DIR = Path(os.environ.get("MOVERS_DIR") or BASE_DIR / "data" / "movers")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REFRESH_COOLDOWN = 30 * 60   # a scan (bhavcopy + filings + news) takes several minutes


@lru_cache(maxsize=64)
def _read(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load(path: Path) -> dict | None:
    try:
        return _read(str(path), path.stat().st_mtime)
    except (OSError, ValueError):
        return None


def _dates() -> list[str]:
    if not DATA_DIR.is_dir():
        return []
    return sorted((p.stem for p in DATA_DIR.glob("????-??-??.json")), reverse=True)


@router.get("/api/movers")
def latest():
    dates = _dates()
    if not dates:
        return {"dates": [], "snapshot": None}
    return {"dates": dates, "snapshot": _load(DATA_DIR / f"{dates[0]}.json")}


@router.get("/api/movers/{trade_date}")
def one_day(trade_date: str):
    if not DATE.match(trade_date):
        raise HTTPException(status_code=404, detail="No such session")
    snapshot = _load(DATA_DIR / f"{trade_date}.json")
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No movers scan for this date")
    return snapshot


@router.post("/api/admin/movers/run-now")
def run_now(admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """Admin only: dispatch scripts/run_movers.py right now via GitHub Actions,
    instead of waiting for the 2 AM run. Rate-limited server-side so repeat
    clicks don't queue up several scans back to back."""
    if not github_dispatch.configured():
        raise HTTPException(status_code=503, detail="Not set up: add GH_DISPATCH_TOKEN on the server (see README) to enable Run now.")

    row = db.get(MetaKV, "MOVERS_REFRESH")
    last = float(((row.value if row else None) or {}).get("last", 0))
    now = time.time()
    if now - last < REFRESH_COOLDOWN:
        wait = int(REFRESH_COOLDOWN - (now - last))
        raise HTTPException(status_code=429, detail=f"Already running -- try again in about {max(1, wait // 60)} min.")

    try:
        github_dispatch.dispatch("movers.yml", {})
    except github_dispatch.DispatchError as e:
        raise HTTPException(status_code=502, detail=f"Could not start the scan: {e}") from e

    if row is None:
        db.add(MetaKV(key="MOVERS_REFRESH", value={"last": now}))
    else:
        row.value = {"last": now}
    db.commit()
    return {"status": "queued", "cooldown_seconds": REFRESH_COOLDOWN}
