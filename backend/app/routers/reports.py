"""
Deep-dive research report endpoints.

GET /api/reports                  the latest report per company (index.json)
GET /api/reports/{code}           that company's latest report, plus its quarter list
GET /api/reports/{code}/{period}  one quarter's report, e.g. FY27-Q1

The reports are written by scripts/deep_reports.py into backend/reports/
and shipped with the deploy, so these endpoints only read files.
"""
import json
import os
import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import BASE_DIR

router = APIRouter(prefix="/api/reports", tags=["reports"])
REPORTS = Path(os.environ.get("REPORTS_DIR") or BASE_DIR / "reports")
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
def report_index():
    return _load(REPORTS / "index.json") or {}


def _periods(code: str) -> list[str]:
    return sorted((p.stem for p in (REPORTS / code).glob("*.json")), reverse=True)


@router.get("/{code}")
def latest_report(code: str):
    if not SAFE.match(code):
        raise HTTPException(status_code=404, detail="No report")
    periods = _periods(code)
    report = _load(REPORTS / code / f"{periods[0]}.json") if periods else None
    if not report:
        raise HTTPException(status_code=404, detail=f"No deep-dive report for '{code}' yet")
    return {**report, "periods": periods}


@router.get("/{code}/{period}")
def report_for_period(code: str, period: str):
    if not SAFE.match(code) or not re.match(r"^FY\d{2}-Q[1-4]$", period):
        raise HTTPException(status_code=404, detail="No report")
    report = _load(REPORTS / code / f"{period}.json")
    if not report:
        raise HTTPException(status_code=404, detail=f"No {period} report for '{code}'")
    return {**report, "periods": _periods(code)}
