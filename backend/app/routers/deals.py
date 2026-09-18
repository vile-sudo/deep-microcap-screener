"""
Bulk & block deals.

GET /api/deals   every deal currently on record (see scripts/run_deals.py),
                 newest first -- symbol, client, side, quantity, price,
                 value, and the board code when the symbol is on this board

Written by scripts/run_deals.py into backend/data/deals/latest.json, from
NSE's own bulk/block deals report; this router only reads it.
"""
from fastapi import APIRouter, Request

from ..json_file import file_response
from ..config import BASE_DIR

router = APIRouter(tags=["deals"])
DATA_FILE = BASE_DIR / "data" / "deals" / "latest.json"


@router.get("/api/deals")
def deals(request: Request):
    return file_response(request, DATA_FILE,
                         {"fetched_at": None, "from_date": None, "to_date": None,
                          "count": 0, "board_count": 0, "deals": []})
