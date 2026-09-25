"""
Company data endpoints.

GET /api/companies with no query params returns every company's full
research record, verbatim -- this is what the dashboard's frontend loads
once on page load and then filters/sorts/searches entirely client-side
(same interaction model as the original single-file board, just fed by
an API instead of an embedded array).

The same endpoint also accepts optional query parameters so the data is
independently useful to anyone hitting the API directly (a notebook, a
script, another app) without having to replicate the client-side
filtering logic.
"""
import csv
import io
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Company, MetaKV
from .auth import require_admin

router = APIRouter(prefix="/api/companies", tags=["companies"])

# Auto-added companies an admin took off the board. They stay in
# companies_raw.json (the server can't edit the repo) and are left out here;
# the daily auto-screen never re-adds a company it has added once.
# Per-market: India keeps the original unprefixed key (no forced reseed of
# an existing production MetaKV row), other markets get a prefixed one.
HIDDEN_KEY = "HIDDEN_CODES"


def hidden_key(market: str) -> str:
    return HIDDEN_KEY if market == "IN" else f"{market}_{HIDDEN_KEY}"


def hidden_codes(db: Session, market: str = "IN") -> set[str]:
    row = db.get(MetaKV, hidden_key(market))
    return set(row.value or []) if row else set()

SORTABLE = {
    "final_score", "score", "market_cap_cr", "market_cap_usd", "pe", "roce_pct", "roe_pct",
    "promoter_pct", "fii_pct", "dii_pct", "insider_pct", "inst_pct", "num_shareholders",
    "cwip_pct_net_block", "guidance_pct", "rank", "name",
}

CSV_COLUMNS = [
    ("Rank", "rank"),
    ("Company", "name"),
    ("Ticker", "code"),
    ("Screen", "screen"),
    ("Theme", "theme"),
    ("Sector", "sector"),
    ("Score", "final_score"),
    ("Rubric", "rubric"),
    ("Claim grade", "claim_grade"),
    ("Market cap cr", "market_cap_cr"),
    ("P/E", "pe"),
    ("CWIP cr", "cwip_cr"),
    ("CWIP % net block", "cwip_pct_net_block"),
    ("Guidance %", "guidance_pct"),
    ("Promoter %", "promoter_pct"),
    ("FII %", "fii_pct"),
    ("DII %", "dii_pct"),
    ("Retail holders", "num_shareholders"),
    ("ROCE %", "roce_pct"),
    ("ROE %", "roe_pct"),
    ("High P/E + heavy CWIP", "capex_overhang"),
    ("Guides above 15%", "guidance_over15"),
    ("PAT turned positive", "pat_turnaround"),
]

# US column set is deliberately different, not a reuse of the India one --
# CWIP/promoter/FII/DII aren't US filing concepts, see
# backend/scripts/us_auto_screen.py for what insider_pct/inst_pct mean.
CSV_COLUMNS_US = [
    ("Rank", "rank"),
    ("Company", "name"),
    ("Ticker", "code"),
    ("Screen", "screen"),
    ("Theme", "theme"),
    ("Sector", "sector"),
    ("Score", "final_score"),
    ("Rubric", "rubric"),
    ("Claim grade", "claim_grade"),
    ("Market cap $m", "market_cap_usd"),
    ("P/E", "pe"),
    ("Guidance %", "guidance_pct"),
    ("Insider %", "insider_pct"),
    ("Institutional %", "inst_pct"),
    ("ROE %", "roe_pct"),
    ("Guides above 15%", "guidance_over15"),
    ("Any forward growth statement", "guidance_flag"),
    ("PAT turned positive", "pat_turnaround"),
    ("CWIP $m", "cwip_usd_m"),
    ("CWIP % of net PP&E", "cwip_pct_net_block"),
    ("PE+CWIP flag", "capex_overhang"),
    ("Heavy capex (CWIP >= 25%)", "capex_heavy"),
    ("Capacity utilization ramp", "capacity_util_flag"),
    ("Product-mix pivot", "product_pivot_flag"),
]


def _apply_filters(
    q,
    market: str,
    sector: Optional[str],
    theme: Optional[str],
    screen: Optional[str],
    rubric: Optional[str],
    min_score: Optional[float],
    max_pe: Optional[float],
    min_roce: Optional[float],
    search: Optional[str],
    codes: Optional[str],
):
    q = q.filter(Company.market == market)
    if sector:
        q = q.filter(Company.sector == sector)
    if theme:
        q = q.filter(Company.theme == theme)
    if screen:
        q = q.filter(Company.screen == screen)
    if rubric:
        q = q.filter(Company.rubric == rubric)
    if min_score is not None:
        q = q.filter(Company.final_score >= min_score)
    if max_pe is not None:
        q = q.filter(Company.pe <= max_pe)
    if min_roce is not None:
        q = q.filter(Company.roce_pct >= min_roce)
    if codes:
        wanted = {c.strip() for c in codes.split(",") if c.strip()}
        if wanted:
            q = q.filter(Company.code.in_(wanted))
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(Company.name.ilike(like))
    return q


@router.get("")
def list_companies(
    market: str = Query("in", pattern="^(in|us)$"),
    sector: Optional[str] = None,
    theme: Optional[str] = None,
    screen: Optional[str] = None,
    rubric: Optional[str] = None,
    min_score: Optional[float] = None,
    max_pe: Optional[float] = None,
    min_roce: Optional[float] = None,
    search: Optional[str] = Query(None, description="Case-insensitive substring match on company name"),
    codes: Optional[str] = Query(None, description="Comma-separated list of company codes to restrict to"),
    sort_by: str = Query("final_score", description=f"One of: {', '.join(sorted(SORTABLE))}"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    limit: Optional[int] = Query(None, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    mkt = market.upper()
    q = db.query(Company)
    q = _apply_filters(q, mkt, sector, theme, screen, rubric, min_score, max_pe, min_roce, search, codes)

    sort_col = getattr(Company, sort_by if sort_by in SORTABLE else "final_score")
    order = desc(sort_col) if sort_dir == "desc" else asc(sort_col)
    # NULLs last regardless of direction, matching the frontend's own sort behaviour
    q = q.order_by(sort_col.is_(None), order)

    hidden = hidden_codes(db, mkt)
    if hidden:
        q = q.filter(Company.code.notin_(hidden))
    if offset:
        q = q.offset(offset)
    if limit:
        q = q.limit(limit)

    return [c.data for c in q.all()]


@router.get("/export.csv")
def export_csv(
    market: str = Query("in", pattern="^(in|us)$"),
    sector: Optional[str] = None,
    theme: Optional[str] = None,
    screen: Optional[str] = None,
    rubric: Optional[str] = None,
    min_score: Optional[float] = None,
    max_pe: Optional[float] = None,
    min_roce: Optional[float] = None,
    search: Optional[str] = None,
    codes: Optional[str] = None,
    db: Session = Depends(get_db),
):
    mkt = market.upper()
    q = db.query(Company)
    q = _apply_filters(q, mkt, sector, theme, screen, rubric, min_score, max_pe, min_roce, search, codes)
    hidden = hidden_codes(db, mkt)
    rows = [c for c in q.all() if c.code not in hidden]

    cols = CSV_COLUMNS if mkt == "IN" else CSV_COLUMNS_US
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for label, _ in cols])
    for c in rows:
        d = c.data
        writer.writerow([
            ("yes" if d.get(key) is True else "" if isinstance(d.get(key), bool) else d.get(key, ""))
            for _, key in cols
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=screener-{len(rows)}-companies.csv"},
    )


@router.get("/hidden")
def list_hidden(market: str = Query("in", pattern="^(in|us)$"), admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    mkt = market.upper()
    codes = sorted(hidden_codes(db, mkt))
    names = {c.code: c.name for c in db.query(Company).filter(Company.market == mkt, Company.code.in_(codes))} if codes else {}
    return [{"code": c, "name": names.get(c)} for c in codes]


@router.post("/{code}/{action}")
def hide_company(code: str, action: str, market: str = Query("in", pattern="^(in|us)$"),
                  admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """Admins: take an auto-added company off the board ("remove") or put it back ("restore")."""
    if action not in ("remove", "restore"):
        raise HTTPException(status_code=404, detail="Unknown action")
    mkt = market.upper()
    company = db.get(Company, (mkt, code))
    if not company:
        raise HTTPException(status_code=404, detail=f"No company with code '{code}'")
    if company.screen != "auto":
        raise HTTPException(status_code=400, detail="Only auto-added companies can be removed here; researched companies are edited in the data files")
    codes = hidden_codes(db, mkt)
    if action == "remove":
        codes.add(code)
    else:
        codes.discard(code)
    key = hidden_key(mkt)
    row = db.get(MetaKV, key)
    if row:
        row.value = sorted(codes)
    else:
        db.add(MetaKV(key=key, value=sorted(codes)))
    db.commit()
    return {"code": code, "hidden": code in codes}


@router.get("/{code}")
def get_company(code: str, market: str = Query("in", pattern="^(in|us)$"), db: Session = Depends(get_db)):
    mkt = market.upper()
    company = db.get(Company, (mkt, code))
    if not company or company.code in hidden_codes(db, mkt):
        raise HTTPException(status_code=404, detail=f"No company with code '{code}'")
    return company.data
