"""
Board-wide metadata: version history, screen badge legend, theme
abbreviations, freshness stamp, and the new-listings candidate queue.
Everything the frontend needs that isn't a per-company field.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Company, MetaKV

router = APIRouter(prefix="/api/meta", tags=["meta"])


def _kv(db: Session, key: str, default):
    row = db.get(MetaKV, key)
    return row.value if row else default


def _mkey(key: str, market: str) -> str:
    """India keeps the original unprefixed MetaKV key (no forced reseed of
    an existing production row); other markets get a prefixed one -- see
    backend/app/seed.py."""
    return key if market == "IN" else f"{market}_{key}"


@router.get("")
def get_meta(market: str = Query("in", pattern="^(in|us)$"), db: Session = Depends(get_db)):
    mkt = market.upper()
    themes = [t for (t,) in db.query(Company.theme).filter(Company.market == mkt).distinct() if t]
    return {
        "build": _kv(db, _mkey("BUILD", mkt), {}),
        "screens": _kv(db, _mkey("SCREENS", mkt), {}),
        "short": _kv(db, _mkey("SHORT", mkt), {}),
        "build_stamp": _kv(db, _mkey("BUILD_STAMP", mkt), None),
        "candidates": _kv(db, _mkey("CANDIDATES", mkt), []),
        "build_new": _kv(db, _mkey("BUILD_NEW", mkt), None),
        "themes": themes,
        "company_count": db.query(Company).filter(Company.market == mkt).count(),
    }


@router.get("/stats")
def get_stats(market: str = Query("in", pattern="^(in|us)$"), db: Session = Depends(get_db)):
    mkt = market.upper()
    total = db.query(Company).filter(Company.market == mkt).count()
    count = lambda **filters: db.query(Company).filter_by(market=mkt, **filters).count()  # noqa: E731
    return {
        "total": total,
        "capex_overhang": count(capex_overhang=True),
        "guidance_over15": count(guidance_over15=True),
        "guidance_flag": count(guidance_flag=True),
        "pat_turnaround": count(pat_turnaround=True),
        "pending_lens_data": count(has_lens_data=False),
        "themes": db.query(Company.theme).filter(Company.market == mkt).distinct().count(),
    }
