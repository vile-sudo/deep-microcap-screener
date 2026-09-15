"""
Sector research endpoints.

GET /api/sectors                       every sector: name, latest edition, headline, key stats
GET /api/sectors/{slug}                the latest edition, its edition list, live company numbers
                                       and the daily latest developments
GET /api/sectors/{slug}/{edition}      one edition, e.g. 2026-09
POST /api/sectors/{slug}/refresh       admin: fire the daily latest-developments
                                       check right now instead of waiting for 7 AM

Research lives in backend/sectors/<slug>/<edition>.json (written by hand or by
scripts/sector_research.py); company numbers in backend/sectors/<slug>/numbers.json
(refreshed daily by scripts/sector_numbers.py); latest developments in
backend/sectors/<slug>/latest.json (daily, scripts/sector_latest.py).
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
from ..config import BASE_DIR, get_settings
from ..database import get_db
from ..models import MetaKV
from .auth import require_admin

router = APIRouter(prefix="/api/sectors", tags=["sectors"])
SECTORS = Path(os.environ.get("SECTORS_DIR") or BASE_DIR / "sectors")
SLUG = re.compile(r"^[a-z0-9-]{2,60}$")
EDITION = re.compile(r"^\d{4}-\d{2}$")
REFRESH_COOLDOWN = 10 * 60   # a run itself takes several minutes; stop repeat clicks queuing more


@lru_cache(maxsize=64)
def _read(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load(path: Path):
    try:
        return _read(str(path), path.stat().st_mtime)
    except (OSError, ValueError):
        return None


def _editions(slug: str) -> list[str]:
    return sorted((p.stem for p in (SECTORS / slug).glob("*.json") if EDITION.match(p.stem)), reverse=True)


@router.get("")
def list_sectors():
    out = []
    for d in sorted(p for p in SECTORS.iterdir() if p.is_dir() and SLUG.match(p.name)) if SECTORS.exists() else []:
        eds = _editions(d.name)
        if not eds:
            continue
        r = _load(d / f"{eds[0]}.json") or {}
        lt = _load(d / "latest.json") or {}
        out.append({"slug": d.name, "name": r.get("name"), "edition": eds[0], "updated": r.get("updated"),
                    "icon": r.get("icon"), "one_line": (r.get("summary") or {}).get("one_line"),
                    "kpis": (r.get("kpis") or [])[:4], "companies": len(r.get("companies") or []),
                    "sources": len(r.get("sources") or []),
                    "latest": {"updated": lt.get("updated"), "items": (lt.get("items") or [])[:2],
                               "count": len(lt.get("items") or [])} if lt.get("items") else None})
    planned = _load(SECTORS / "planned.json") or []
    return {"sectors": out, "planned": planned}


def _with_numbers(slug: str, report: dict) -> dict:
    return {**report, "editions": _editions(slug), "numbers": _load(SECTORS / slug / "numbers.json")}


def _refresh_wait(db: Session, slug: str) -> int:
    row = db.get(MetaKV, "SECTOR_REFRESH")
    last = ((row.value if row else None) or {}).get(slug, 0)
    return max(0, int(REFRESH_COOLDOWN - (time.time() - last)))


@router.get("/{slug}")
def latest(slug: str, db: Session = Depends(get_db)):
    if not SLUG.match(slug) or not _editions(slug):
        raise HTTPException(status_code=404, detail="No research for this sector yet")
    return {**_with_numbers(slug, _load(SECTORS / slug / f"{_editions(slug)[0]}.json")),
            "latest": _load(SECTORS / slug / "latest.json"),
            "refresh_wait_seconds": _refresh_wait(db, slug)}


@router.get("/{slug}/{edition}")
def edition(slug: str, edition: str):
    if not SLUG.match(slug) or not EDITION.match(edition):
        raise HTTPException(status_code=404, detail="No such edition")
    r = _load(SECTORS / slug / f"{edition}.json")
    if not r:
        raise HTTPException(status_code=404, detail="No such edition")
    return _with_numbers(slug, r)


@router.post("/{slug}/refresh")
def refresh(slug: str, admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """Admin only: dispatch the "latest developments" GitHub Action for one
    sector right now. Rate-limited server-side so repeat clicks (or several
    admins) don't queue up several runs back to back."""
    if not SLUG.match(slug) or not _editions(slug):
        raise HTTPException(status_code=404, detail="No research for this sector yet")
    if not github_dispatch.configured():
        raise HTTPException(status_code=503, detail="Not set up: add GH_DISPATCH_TOKEN on the server (see README) to enable Refresh now.")

    row = db.get(MetaKV, "SECTOR_REFRESH")
    state = (row.value if row else None) or {}
    now = time.time()
    last = state.get(slug, 0)
    if now - last < REFRESH_COOLDOWN:
        wait = int(REFRESH_COOLDOWN - (now - last))
        raise HTTPException(status_code=429, detail=f"Already refreshing -- try again in about {max(1, wait // 60)} min.")

    try:
        github_dispatch.dispatch("sector-research.yml", {"job": "latest developments", "sector": slug, "force": "true"})
    except github_dispatch.DispatchError as e:
        raise HTTPException(status_code=502, detail=f"Could not start the refresh: {e}") from e

    state[slug] = now
    if row is None:
        db.add(MetaKV(key="SECTOR_REFRESH", value=state))
    else:
        row.value = state
    db.commit()
    return {"status": "queued", "cooldown_seconds": REFRESH_COOLDOWN, "run_url": f"https://github.com/{get_settings().github_repo}/actions"}
