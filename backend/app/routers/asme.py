"""
ASME certification endpoint.

GET /api/asme   every NSE/BSE-listed ASME certificate holder from the latest
                scan, each tagged with the board company it is (board_code),
                or null when that company isn't on the board.

data/asme_raw.json is written by automation/scan-asme.mjs on each successful
scan (see publishToBoard there). The match against the board happens here,
at request time, so it is always against the board's current company list:
a company added to the board that already holds an ASME certificate is
tagged immediately, and one that gains a certificate is tagged after the
next scan -- no hand edit either way.

Matching is exact, never fuzzy: the listing's ticker against the board's
NSE code, board code, or the exchange symbol the chart data resolved for it;
then the normalized company name. A false "ASME certified" tag is worse
than a missed one.
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import charts
from ..database import get_db
from ..models import Company

router = APIRouter(prefix="/api/asme", tags=["asme"])

ASME_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "asme_raw.json"


def _norm(name: str) -> str:
    # ASME and the exchanges write "(I)" and "(India)" interchangeably
    n = (name or "").upper().replace("(I)", "(INDIA)")
    return charts._norm_name(n)


@router.get("")
def asme(db: Session = Depends(get_db)):
    try:
        raw = json.loads(ASME_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {"scanned_on": None, "companies": []}

    chart_symbols = {code: s.get("symbol") for code, s in charts.load_index().get("companies", {}).items()}
    by_symbol, by_name = {}, {}
    for code, data in db.query(Company.code, Company.data):
        data = data or {}
        for sym in {data.get("nse_code"), code, chart_symbols.get(code)}:
            if sym:
                by_symbol.setdefault(str(sym).upper(), code)
        by_name.setdefault(_norm(data.get("name") or ""), code)

    companies = []
    for c in raw.get("companies", []):
        code = by_symbol.get(str(c.get("symbol") or "").upper())
        if not code:
            for n in [c.get("name"), *(c.get("asme_names") or [])]:
                code = by_name.get(_norm(n or ""))
                if code:
                    break
        companies.append({**c, "board_code": code})

    return {
        "scanned_on": raw.get("scanned_on"),
        "companies": companies,
        "on_board": sum(1 for c in companies if c["board_code"]),
    }
