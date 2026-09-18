"""
Upcoming IPO report endpoints.

GET /api/ipo-reports          every IPO report on record (index.json)
GET /api/ipo-reports/{symbol} that one report

Written by scripts/ipo_reports.py into backend/data/ipo_reports/ (one file
per issue -- unlike the quarterly deep-dives, an IPO report is a one-time
thing, not a recurring series), shipped with the deploy; this router only
reads files.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import BASE_DIR

router = APIRouter(prefix="/api/ipo-reports", tags=["ipo-reports"])
DIR = BASE_DIR / "data" / "ipo_reports"
SAFE = re.compile(r"^[A-Za-z0-9&._-]{1,40}$")


@lru_cache(maxsize=256)
def _read(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load(path: Path) -> dict | None:
    try:
        return _read(str(path), path.stat().st_mtime)
    except (OSError, ValueError):
        return None


@router.get("")
def ipo_report_index():
    return _load(DIR / "index.json") or {"reports": []}


@router.get("/{symbol}")
def ipo_report(symbol: str):
    if not SAFE.match(symbol):
        raise HTTPException(status_code=404, detail="No report")
    report = _load(DIR / f"{symbol.upper()}.json")
    if not report:
        raise HTTPException(status_code=404, detail=f"No IPO report for '{symbol}' yet")
    return report
