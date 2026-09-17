"""
Logic Gates: the numeric thresholds and keyword lists the daily auto-screen
(backend/scripts/auto_screen.py) applies to decide which newly-listed
companies pass the board's rules, plus the extra moat and import-substitution
keywords it looks for, and the legacy_brand_names denylist (known_to_public()
/ LEGACY_BRAND there) for famous old PSU/legacy names that shouldn't be added
even if they clear a moat. Editable on the dashboard (account menu -> Admin ->
Logic Gates) without a code change or a deploy.

GET  /api/meta/logic-gates          the effective gates (defaults + any admin
                                    override) -- public, since these thresholds
                                    are already documented on the board's own
                                    "How this board is built" page, and
                                    auto_screen.py (running in GitHub Actions,
                                    with no database access) reads this over
                                    HTTP to pick up changes without a deploy.
PUT  /api/admin/logic-gates          admin: save an override (merged with the
                                    defaults; only the sections/keys sent are
                                    changed, everything else keeps its value)
POST /api/admin/logic-gates/reset    admin: drop the override, back to defaults
POST /api/admin/logic-gates/run-now  admin: dispatch the auto-screen workflow
                                    right away instead of waiting for 2 AM
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import github_dispatch
from ..config import BASE_DIR
from ..database import get_db
from ..models import MetaKV
from .auth import require_admin

router = APIRouter(tags=["logic-gates"])
DEFAULTS_FILE = BASE_DIR / "data" / "logic_gates_defaults.json"
KEY = "LOGIC_GATES"

NUMERIC_FIELDS = {
    "hard_gates": ["market_cap_min_cr", "market_cap_max_tier1_cr", "market_cap_max_tier2_cr", "promoter_min_pct",
                   "public_max_pct", "roce_min_pct", "roe_min_pct", "institutional_tier1_min_pct",
                   "shareholders_max_tier1", "shareholders_max_tier2"],
    "flags": ["pe_overhang_min", "cwip_overhang_min_pct", "cwip_heavy_min_pct", "guidance_over_pct",
              "pat_turnaround_lookback_periods", "pe_penalty_min"],
}
KEYWORD_CATEGORIES = ["import_substitution", "leading_maker", "market_share", "sole_maker", "first_mover"]


@lru_cache(maxsize=1)
def _defaults(mtime: float) -> dict:
    return json.loads(DEFAULTS_FILE.read_text(encoding="utf-8"))


def defaults() -> dict:
    try:
        return _defaults(DEFAULTS_FILE.stat().st_mtime)
    except OSError:
        return {"hard_gates": {}, "flags": {}, "moat_keywords": {}, "import_substitution_materials": [], "legacy_brand_names": []}


def _merge(base: dict, override: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        elif v is not None:
            out[k] = v
    return out


def effective(db: Session) -> dict:
    row = db.get(MetaKV, KEY)
    return _merge(defaults(), row.value if row else {})


class GatesUpdate(BaseModel):
    hard_gates: dict[str, float] | None = None
    flags: dict[str, float] | None = None
    moat_keywords: dict[str, list[str]] | None = None
    import_substitution_materials: list[str] | None = Field(None, max_length=500)
    legacy_brand_names: list[str] | None = Field(None, max_length=500)


def _validate(body: GatesUpdate) -> list[str]:
    errs = []
    for section, fields in NUMERIC_FIELDS.items():
        vals = getattr(body, section) or {}
        for k, v in vals.items():
            if k not in fields:
                errs.append(f"{section}.{k} is not a known field")
            elif not isinstance(v, (int, float)) or v < 0 or v > 1_000_000:
                errs.append(f"{section}.{k} must be a number between 0 and 1,000,000")
    if body.moat_keywords:
        for cat, phrases in body.moat_keywords.items():
            if cat not in KEYWORD_CATEGORIES:
                errs.append(f"moat_keywords.{cat} is not a known category ({', '.join(KEYWORD_CATEGORIES)})")
            elif not isinstance(phrases, list) or len(phrases) > 200 or any(not isinstance(p, str) or len(p) > 200 for p in phrases):
                errs.append(f"moat_keywords.{cat}: at most 200 phrases, each under 200 characters")
    if body.import_substitution_materials is not None:
        if any(not isinstance(p, str) or len(p) > 200 for p in body.import_substitution_materials):
            errs.append("import_substitution_materials: each entry must be under 200 characters")
    if body.legacy_brand_names is not None:
        if any(not isinstance(p, str) or not p.strip() or len(p) > 200 for p in body.legacy_brand_names):
            errs.append("legacy_brand_names: each entry must be non-empty and under 200 characters")
    full = _merge(defaults(), body.model_dump(exclude_none=True))["hard_gates"]
    if full.get("market_cap_min_cr", 0) >= full.get("market_cap_max_tier1_cr", 1):
        errs.append("hard_gates.market_cap_min_cr must be less than market_cap_max_tier1_cr")
    if full.get("market_cap_max_tier1_cr", 0) > full.get("market_cap_max_tier2_cr", 0):
        errs.append("hard_gates.market_cap_max_tier1_cr must not exceed market_cap_max_tier2_cr")
    if full.get("shareholders_max_tier1", 0) > full.get("shareholders_max_tier2", 0):
        errs.append("hard_gates.shareholders_max_tier1 must not exceed shareholders_max_tier2")
    return errs


@router.get("/api/meta/logic-gates")
def get_logic_gates(db: Session = Depends(get_db)):
    row = db.get(MetaKV, KEY)
    return {**effective(db), "defaults": defaults(), "customized": bool(row and row.value)}


@router.put("/api/admin/logic-gates")
def put_logic_gates(body: GatesUpdate, admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    errs = _validate(body)
    if errs:
        raise HTTPException(status_code=422, detail="; ".join(errs))
    row = db.get(MetaKV, KEY)
    current = row.value if row else {}
    updated = _merge(current, body.model_dump(exclude_none=True))
    if row is None:
        db.add(MetaKV(key=KEY, value=updated))
    else:
        row.value = updated
    db.commit()
    return effective(db)


@router.post("/api/admin/logic-gates/reset")
def reset_logic_gates(admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    row = db.get(MetaKV, KEY)
    if row is not None:
        db.delete(row)
        db.commit()
    return effective(db)


@router.post("/api/admin/logic-gates/run-now")
def run_auto_screen_now(admin: dict = Depends(require_admin)):
    if not github_dispatch.configured():
        raise HTTPException(status_code=503, detail="Not set up: add GH_DISPATCH_TOKEN on the server (see README) to enable Run now.")
    try:
        github_dispatch.dispatch("auto-screen.yml", {})
    except github_dispatch.DispatchError as e:
        raise HTTPException(status_code=502, detail=f"Could not start the run: {e}") from e
    from ..config import get_settings
    return {"status": "queued", "run_url": f"https://github.com/{get_settings().github_repo}/actions/workflows/auto-screen.yml"}
