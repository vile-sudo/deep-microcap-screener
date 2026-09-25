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
GET /api/insider-us       the US board's counterpart to deals, but a
                          genuinely different signal (SEC Form 4 insider
                          disclosure, not NSE's counterparty-named bulk/
                          block deal disclosure -- see app/movers/
                          insider_us.py's docstring for why) -- every
                          insider transaction on record (scripts/
                          run_insider_us.py), newest first, always
                          board-scoped (SEC has no single market-wide
                          "every insider transaction today" file the way
                          NSE gives one CSV for deals)
GET /api/announcements-us the US board's counterpart to announcements --
                          SEC Form 8-K, genuinely closer to a straight port
                          than insider-us is (see app/movers/
                          announcements_us.py's docstring): 8-K IS the US
                          "material corporate event" filing, with SEC's own
                          structured item codes standing in for NSE's
                          free-text category. Every material 8-K on record
                          (scripts/run_announcements_us.py), newest first,
                          also always board-scoped.
GET /api/earnings-us      the US board's earnings calendar and results --
                          each company's next report date (before open /
                          after close), the estimates, and its last few
                          quarters' EPS actual vs estimate
                          (scripts/run_earnings_us.py, Finnhub free tier)

Written into backend/data/deals/latest.json,
backend/data/announcements/latest.json, backend/data/insider_us/
latest.json and backend/data/announcements_us/latest.json, and (earnings) backend/data/earnings_us/
latest.json respectively;
this router only reads them.
"""
from fastapi import APIRouter, Request

from ..json_file import file_response
from ..config import BASE_DIR

router = APIRouter(tags=["deals"])
DATA_FILE = BASE_DIR / "data" / "deals" / "latest.json"
ANNOUNCEMENTS_FILE = BASE_DIR / "data" / "announcements" / "latest.json"
INSIDER_US_FILE = BASE_DIR / "data" / "insider_us" / "latest.json"
ANNOUNCEMENTS_US_FILE = BASE_DIR / "data" / "announcements_us" / "latest.json"
EARNINGS_US_FILE = BASE_DIR / "data" / "earnings_us" / "latest.json"


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


@router.get("/api/insider-us")
def insider_us(request: Request):
    return file_response(request, INSIDER_US_FILE,
                         {"fetched_at": None, "from_date": None, "to_date": None,
                          "count": 0, "companies_checked": 0, "companies_with_cik": 0, "transactions": []})


@router.get("/api/announcements-us")
def announcements_us(request: Request):
    return file_response(request, ANNOUNCEMENTS_US_FILE,
                         {"fetched_at": None, "from_date": None, "to_date": None,
                          "count": 0, "companies_checked": 0, "companies_with_cik": 0, "announcements": []})


@router.get("/api/earnings-us")
def earnings_us(request: Request):
    return file_response(request, EARNINGS_US_FILE,
                         {"fetched_at": None, "asof": None, "companies_checked": 0,
                          "companies_with_data": 0, "companies": {}})
