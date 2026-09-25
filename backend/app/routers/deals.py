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
GET /api/news-us          the US board's recent headlines, newest first, a
                          rolling two weeks (scripts/run_news_us.py, every
                          three hours)
GET /api/institutions-us  institutional ownership from SEC Form 13F -- per
                          company: institutions holding it, share of shares
                          outstanding, top holders (scripts/run_institutions_us.py)
GET /api/fundamentals-us  five fiscal years of revenue, profit, cash flow, debt
                          and equity from SEC XBRL (scripts/run_fundamentals_us.py)
GET /api/analysts-us      analyst coverage and rating counts (scripts/run_analysts_us.py)
GET /api/short-us         FINRA short interest (scripts/run_short_us.py)
GET /api/ipo-us           the US IPO calendar (scripts/run_ipo_us.py)
GET /api/performance-us   return since added and score history per board company
                          (scripts/run_performance_us.py)

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
NEWS_US_FILE = BASE_DIR / "data" / "news_us" / "latest.json"
INSTITUTIONS_US_FILE = BASE_DIR / "data" / "institutions_us" / "latest.json"
FUNDAMENTALS_US_FILE = BASE_DIR / "data" / "fundamentals_us" / "latest.json"
ANALYSTS_US_FILE = BASE_DIR / "data" / "analysts_us" / "latest.json"
SHORT_US_FILE = BASE_DIR / "data" / "short_us" / "latest.json"
IPO_US_FILE = BASE_DIR / "data" / "ipo_us" / "latest.json"
PERFORMANCE_US_FILE = BASE_DIR / "data" / "performance_us" / "latest.json"


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


@router.get("/api/news-us")
def news_us(request: Request):
    return file_response(request, NEWS_US_FILE,
                         {"fetched_at": None, "count": 0, "companies_checked": 0,
                          "companies_with_news": 0, "items": []})


@router.get("/api/institutions-us")
def institutions_us(request: Request):
    return file_response(request, INSTITUTIONS_US_FILE,
                         {"fetched_at": None, "source_file": None, "companies_checked": 0,
                          "companies_matched": 0, "companies": {}})


def _company_file(name: str, path):
    @router.get(f"/api/{name}-us")
    def endpoint(request: Request):
        return file_response(request, path, {"fetched_at": None, "companies": {}})
    endpoint.__name__ = f"{name}_us"
    return endpoint


for _name, _path in (("fundamentals", FUNDAMENTALS_US_FILE), ("analysts", ANALYSTS_US_FILE),
                     ("short", SHORT_US_FILE), ("performance", PERFORMANCE_US_FILE)):
    _company_file(_name, _path)


@router.get("/api/ipo-us")
def ipo_us(request: Request):
    return file_response(request, IPO_US_FILE, {"fetched_at": None, "count": 0, "items": []})
