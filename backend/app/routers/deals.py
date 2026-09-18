"""
Bulk & block deals, and corporate announcements -- the same dashboard
section (both are "who's doing what, disclosed" signals from NSE).

GET /api/deals            every deal on record (scripts/run_deals.py),
                          newest first -- symbol, client, side, quantity,
                          price, value, and the board code when the symbol
                          is on this board
GET /api/announcements    every non-routine filing on record
                          (scripts/run_announcements.py), newest first --
                          category, a one-line summary, the filing PDF, and
                          the board code when the symbol is on this board

Written into backend/data/deals/latest.json and
backend/data/announcements/latest.json respectively; this router only reads
them.
"""
from fastapi import APIRouter, Request

from ..json_file import file_response
from ..config import BASE_DIR

router = APIRouter(tags=["deals"])
DATA_FILE = BASE_DIR / "data" / "deals" / "latest.json"
ANNOUNCEMENTS_FILE = BASE_DIR / "data" / "announcements" / "latest.json"


@router.get("/api/deals")
def deals(request: Request):
    return file_response(request, DATA_FILE,
                         {"fetched_at": None, "from_date": None, "to_date": None,
                          "count": 0, "board_count": 0, "deals": []})


@router.get("/api/announcements")
def announcements(request: Request):
    return file_response(request, ANNOUNCEMENTS_FILE,
                         {"fetched_at": None, "from_date": None, "to_date": None,
                          "count": 0, "board_count": 0, "announcements": []})
