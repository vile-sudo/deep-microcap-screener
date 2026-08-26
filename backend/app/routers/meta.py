"""
Board-wide metadata: version history, screen badge legend, theme
abbreviations, freshness stamp, and the new-listings candidate queue.
Everything the frontend needs that isn't a per-company field.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Company, MetaKV

router = APIRouter(prefix="/api/meta", tags=["meta"])


def _kv(db: Session, key: str, default):
    row = db.get(MetaKV, key)
    return row.value if row else default


@router.get("")
def get_meta(db: Session = Depends(get_db)):
    themes = [t for (t,) in db.query(Company.theme).distinct() if t]
    return {
        "build": _kv(db, "BUILD", {}),
        "screens": _kv(db, "SCREENS", {}),
        "short": _kv(db, "SHORT", {}),
        "build_stamp": _kv(db, "BUILD_STAMP", None),
        "candidates": _kv(db, "CANDIDATES", []),
        "build_new": _kv(db, "BUILD_NEW", None),
        "themes": themes,
        "company_count": db.query(Company).count(),
    }


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    total = db.query(Company).count()
    count = lambda **filters: db.query(Company).filter_by(**filters).count()  # noqa: E731
    return {
        "total": total,
        "capex_overhang": count(capex_overhang=True),
        "guidance_over15": count(guidance_over15=True),
        "guidance_flag": count(guidance_flag=True),
        "pat_turnaround": count(pat_turnaround=True),
        "pending_lens_data": count(has_lens_data=False),
        "themes": db.query(Company.theme).distinct().count(),
    }
