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
HIDDEN_KEY = "HIDDEN_CODES"


def hidden_codes(db: Session) -> set[str]:
    row = db.get(MetaKV, HIDDEN_KEY)
    return set(row.value or []) if row else set()

SORTABLE = {
    "final_score", "score", "market_cap_cr", "pe", "roce_pct", "roe_pct",
    "promoter_pct", "fii_pct", "dii_pct", "num_shareholders",
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


def _apply_filters(
    q,
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
    q = db.query(Company)
    q = _apply_filters(q, sector, theme, screen, rubric, min_score, max_pe, min_roce, search, codes)

    sort_col = getattr(Company, sort_by if sort_by in SORTABLE else "final_score")
    order = desc(sort_col) if sort_dir == "desc" else asc(sort_col)
    # NULLs last regardless of direction, matching the frontend's own sort behaviour
    q = q.order_by(sort_col.is_(None), order)

    hidden = hidden_codes(db)
    if hidden:
        q = q.filter(Company.code.notin_(hidden))
    if offset:
        q = q.offset(offset)
    if limit:
        q = q.limit(limit)

    return [c.data for c in q.all()]


@router.get("/export.csv")
def export_csv(
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
    q = db.query(Company)
    q = _apply_filters(q, sector, theme, screen, rubric, min_score, max_pe, min_roce, search, codes)
    hidden = hidden_codes(db)
    rows = [c for c in q.all() if c.code not in hidden]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for label, _ in CSV_COLUMNS])
    for c in rows:
        d = c.data
        writer.writerow([
            ("yes" if d.get(key) is True else "" if isinstance(d.get(key), bool) else d.get(key, ""))
            for _, key in CSV_COLUMNS
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=screener-{len(rows)}-companies.csv"},
    )


@router.get("/hidden")
def list_hidden(admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    codes = sorted(hidden_codes(db))
    names = {c.code: c.name for c in db.query(Company).filter(Company.code.in_(codes))} if codes else {}
    return [{"code": c, "name": names.get(c)} for c in codes]


@router.post("/{code}/{action}")
def hide_company(code: str, action: str, admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    """Admins: take an auto-added company off the board ("remove") or put it back ("restore")."""
    if action not in ("remove", "restore"):
        raise HTTPException(status_code=404, detail="Unknown action")
    company = db.get(Company, code)
    if not company:
        raise HTTPException(status_code=404, detail=f"No company with code '{code}'")
    if company.screen != "auto":
        raise HTTPException(status_code=400, detail="Only auto-added companies can be removed here; researched companies are edited in the data files")
    codes = hidden_codes(db)
    if action == "remove":
        codes.add(code)
    else:
        codes.discard(code)
    row = db.get(MetaKV, HIDDEN_KEY)
    if row:
        row.value = sorted(codes)
    else:
        db.add(MetaKV(key=HIDDEN_KEY, value=sorted(codes)))
    db.commit()
    return {"code": code, "hidden": code in codes}


@router.get("/{code}")
def get_company(code: str, db: Session = Depends(get_db)):
    company = db.get(Company, code)
    if not company or company.code in hidden_codes(db):
        raise HTTPException(status_code=404, detail=f"No company with code '{code}'")
    return company.data
