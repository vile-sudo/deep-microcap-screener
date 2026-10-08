"""
Bulk & block deals, and corporate announcements -- the same dashboard
section (both are "who's doing what, disclosed" signals from NSE).

GET /api/deals            every deal on record (scripts/run_deals.py),
                          newest first -- symbol, client, side, quantity,
                          price, value, and the board code when the symbol
                          is on this board
GET /api/fii-dii          India board: daily FII/FPI and DII cash-market flow, NSE's
                          own combined report (scripts/run_fii_dii.py), newest first --
                          buy/sell/net value in crore for each, one row per session
GET /api/announcements    every non-routine filing on record
                          (scripts/run_announcements.py), newest first --
                          category, a one-line summary, the filing PDF, and
                          the board code when the symbol is on this board
GET /api/insider-trades   India board: insider trading & SAST disclosures
                          (scripts/run_insider_trades.py), newest first --
                          promoters/directors/KMPs' own buy/sell/pledge
                          transactions, from NSE's own reports; the
                          "Insider Trading" tab of the same Bulk & Block
                          Deals section (a different SEBI regulation than
                          bulk/block, same "who's doing what, disclosed"
                          shape)
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
GET /api/institutions-flow-us  quarter-over-quarter new/exited/increased/decreased
                          13F positions (scripts/run_institutions_flow_us.py)
GET /api/fundamentals-us  five fiscal years of revenue, profit, cash flow, debt
                          and equity from SEC XBRL (scripts/run_fundamentals_us.py)
GET /api/analysts-us      analyst coverage and rating counts (scripts/run_analysts_us.py)
GET /api/short-us         FINRA short interest (scripts/run_short_us.py)
GET /api/results-in       India board: last 12 quarters and 8 fiscal years of sales, margin,
                          profit, plus each company's earnings-call list (scripts/run_results_in.py)
GET /api/guidance-in      India board: numeric targets management stated on earnings calls, with the
                          exact sentence, and whether each came true (scripts/run_guidance_in.py)
GET /api/results-calendar-in  India board: each company's next results date (confirmed by NSE notice,
                          expected from last year, or the SEBI deadline) (scripts/run_results_calendar_in.py)
GET /api/company-profile-in  India board company pages: about, pros/cons, balance sheet, cash flow,
                          ratios, quarterly shareholding (scripts/run_results_in.py)
GET /api/company-activity-in/{symbol}  one company's filings, deals and insider trades (company page)
GET /api/logos-in, /api/logo-in/{code}  company logos for the Companies pages (scripts/run_logos_in.py)
GET /api/sector-strength, /api/sector-strength/history  Sector Strength breadth monitor (app/sector_strength.py)
GET /api/transcripts-in   India board: which earnings-call transcripts are on file, per company
GET /api/transcripts-in/{code}  that company's newest calls laid out for reading -- speakers, roles,
                          numbered turns, where the Q&A starts (scripts/run_transcripts_in.py)
GET /api/ipo-us           the US IPO calendar (scripts/run_ipo_us.py)
GET /api/performance-us   return since added and score history per board company
                          (scripts/run_performance_us.py)

Written into backend/data/deals/latest.json,
backend/data/announcements/latest.json, backend/data/insider_trades/latest.json,
backend/data/insider_us/latest.json and backend/data/announcements_us/latest.json, and (earnings)
backend/data/earnings_us/latest.json respectively;
this router only reads them.
"""
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..json_file import file_response
from ..config import BASE_DIR

router = APIRouter(tags=["deals"])
DATA_FILE = BASE_DIR / "data" / "deals" / "latest.json"
FII_DII_FILE = BASE_DIR / "data" / "fii_dii" / "latest.json"
ANNOUNCEMENTS_FILE = BASE_DIR / "data" / "announcements" / "latest.json"
INSIDER_TRADES_FILE = BASE_DIR / "data" / "insider_trades" / "latest.json"
INSIDER_US_FILE = BASE_DIR / "data" / "insider_us" / "latest.json"
ANNOUNCEMENTS_US_FILE = BASE_DIR / "data" / "announcements_us" / "latest.json"
EARNINGS_US_FILE = BASE_DIR / "data" / "earnings_us" / "latest.json"
NEWS_US_FILE = BASE_DIR / "data" / "news_us" / "latest.json"
INSTITUTIONS_US_FILE = BASE_DIR / "data" / "institutions_us" / "latest.json"
INSTITUTIONS_FLOW_US_FILE = BASE_DIR / "data" / "institutions_us" / "flow.json"
FUNDAMENTALS_US_FILE = BASE_DIR / "data" / "fundamentals_us" / "latest.json"
ANALYSTS_US_FILE = BASE_DIR / "data" / "analysts_us" / "latest.json"
SHORT_US_FILE = BASE_DIR / "data" / "short_us" / "latest.json"
IPO_US_FILE = BASE_DIR / "data" / "ipo_us" / "latest.json"
PERFORMANCE_US_FILE = BASE_DIR / "data" / "performance_us" / "latest.json"
RESULTS_IN_FILE = BASE_DIR / "data" / "results_in" / "latest.json"
GUIDANCE_IN_FILE = BASE_DIR / "data" / "guidance_in" / "latest.json"
RESULTS_CALENDAR_IN_FILE = BASE_DIR / "data" / "results_calendar_in" / "latest.json"
TRANSCRIPTS_IN_DIR = BASE_DIR / "data" / "transcripts_in"


@router.get("/api/deals")
def deals(request: Request):
    return file_response(request, DATA_FILE,
                         {"fetched_at": None, "from_date": None, "to_date": None,
                          "count": 0, "board_count": 0, "deals": []})


@router.get("/api/fii-dii")
def fii_dii(request: Request):
    return file_response(request, FII_DII_FILE, {"fetched_at": None, "count": 0, "rows": []})


@router.get("/api/announcements")
def announcements(request: Request):
    return file_response(request, ANNOUNCEMENTS_FILE,
                         {"fetched_at": None, "from_date": None, "to_date": None,
                          "count": 0, "board_count": 0, "announcements": []})


@router.get("/api/announcements/categories")
def announcement_categories():
    """NSE filing categories for the "Get notified" picker (app/filing_alerts.py) -- every one seen so far."""
    from ..filing_alerts import known_categories
    return {"categories": known_categories()}


@router.get("/api/insider-trades")
def insider_trades(request: Request):
    return file_response(request, INSIDER_TRADES_FILE,
                         {"fetched_at": None, "count": 0, "board_count": 0, "trades": []})


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


@router.get("/api/institutions-flow-us")
def institutions_flow_us(request: Request):
    return file_response(request, INSTITUTIONS_FLOW_US_FILE, {"fetched_at": None, "companies": {}})


def _company_file(name: str, path):
    @router.get(f"/api/{name}-us")
    def endpoint(request: Request):
        return file_response(request, path, {"fetched_at": None, "companies": {}})
    endpoint.__name__ = f"{name}_us"
    return endpoint


for _name, _path in (("fundamentals", FUNDAMENTALS_US_FILE), ("analysts", ANALYSTS_US_FILE),
                     ("short", SHORT_US_FILE), ("performance", PERFORMANCE_US_FILE)):
    _company_file(_name, _path)


@router.get("/api/results-in")
def results_in(request: Request):
    return file_response(request, RESULTS_IN_FILE, {"fetched_at": None, "companies": {}})


@router.get("/api/company-profile-in")
def company_profile_in(request: Request):
    """India board company pages: business description, balance sheet, cash flow, ratios, shareholding."""
    return file_response(request, RESULTS_IN_FILE.parent / "profile.json", {"fetched_at": None, "companies": {}})


_BY_SYMBOL: dict[str, tuple[float, dict]] = {}


def _by_symbol(path, key: str) -> dict:
    """{symbol: [rows]} of one of the feeds above, re-read only when the file changes."""
    import json
    try:
        mt = path.stat().st_mtime
    except OSError:
        return {}
    hit = _BY_SYMBOL.get(str(path))
    if hit and hit[0] == mt:
        return hit[1]
    out: dict[str, list] = {}
    try:
        for r in json.loads(path.read_text(encoding="utf-8")).get(key) or []:
            if r.get("symbol"):
                out.setdefault(str(r["symbol"]).upper(), []).append(r)
    except (OSError, ValueError):
        return {}
    _BY_SYMBOL[str(path)] = (mt, out)
    return out


@router.get("/api/company-activity-in/{symbol}")
def company_activity_in(symbol: str):
    """One company's filings, bulk/block deals and insider trades, for its company page."""
    sym = symbol.strip().upper()
    if not re.fullmatch(r"[A-Z0-9&_.-]{1,30}", sym):
        raise HTTPException(status_code=404)
    return {"symbol": sym,
            "announcements": _by_symbol(ANNOUNCEMENTS_FILE, "announcements").get(sym, [])[:200],
            "deals": _by_symbol(DATA_FILE, "deals").get(sym, [])[:100],
            "insider": _by_symbol(INSIDER_TRADES_FILE, "trades").get(sym, [])[:100]}


LOGO_DIR = BASE_DIR / "data" / "logos"
LOGO_TYPES = {"png": "image/png", "ico": "image/x-icon", "svg": "image/svg+xml", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}


@router.get("/api/logos-in")
def logos_in(request: Request):
    """Which board companies have a logo on file (scripts/run_logos_in.py)."""
    return file_response(request, LOGO_DIR / "index.json", {"logos": {}, "failed": {}})


@router.get("/api/logo-in/{code}")
def logo_in(code: str):
    """One company's logo, saved from its own website by scripts/run_logos_in.py."""
    from fastapi.responses import FileResponse
    if not re.fullmatch(r"[A-Za-z0-9_&-]{1,30}", code):
        raise HTTPException(status_code=404)
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", code)
    for ext, media in LOGO_TYPES.items():
        path = LOGO_DIR / f"{stem}.{ext}"
        if path.is_file():
            # an SVG is only ever shown through <img>, where its scripts can't run; the CSP makes sure of it
            return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=604800",
                                                                 "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"})
    raise HTTPException(status_code=404)


SECTOR_STRENGTH_DIR = BASE_DIR / "data" / "sector_strength"


@router.get("/api/sector-strength")
def sector_strength(request: Request):
    """Sector Strength: each tracked sector's stocks with their 1/2/3/5-day returns, and the Nifty 500 (app/sector_strength.py)."""
    return file_response(request, SECTOR_STRENGTH_DIR / "latest.json", {"asof": None, "sectors": []})


@router.get("/api/sector-strength/indices")
def sector_strength_indices(request: Request):
    """Each sector's daily level (equal and market-cap weighted) beside the Nifty 500, ~3 years, for the comparison chart."""
    return file_response(request, SECTOR_STRENGTH_DIR / "indices.json", {"dates": [], "sectors": {}, "nifty500": []})


@router.get("/api/sector-strength/history")
def sector_strength_history(request: Request):
    """Each sector's (and the Nifty 500's) breadth over the last 60 sessions, for the sparklines."""
    return file_response(request, SECTOR_STRENGTH_DIR / "history.json", {"dates": [], "sectors": {}})


@router.get("/api/results-calendar-in")
def results_calendar_in(request: Request):
    return file_response(request, RESULTS_CALENDAR_IN_FILE, {"fetched_at": None, "asof": None, "items": [], "events": []})


@router.get("/api/transcripts-in")
def transcripts_in_index(request: Request):
    return file_response(request, TRANSCRIPTS_IN_DIR / "index.json", {"fetched_at": None, "companies": {}})


@router.get("/api/transcripts-in/{code}")
def transcripts_in(code: str, request: Request):
    """Stored gzipped; sent as is to any browser that accepts gzip (all of them)."""
    if not re.fullmatch(r"[A-Za-z0-9_&-]{1,30}", code):
        raise HTTPException(status_code=404)
    path = TRANSCRIPTS_IN_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '_', code)}.json.gz"
    try:
        st = path.stat()
    except OSError:
        raise HTTPException(status_code=404, detail="No transcripts on file for this company")
    etag = f'"{int(st.st_mtime)}-{st.st_size}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Accept-Encoding"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    body = path.read_bytes()
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(body, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
    import gzip
    return Response(gzip.decompress(body), media_type="application/json", headers=headers)


@router.get("/api/guidance-in")
def guidance_in(request: Request):
    return file_response(request, GUIDANCE_IN_FILE, {"fetched_at": None, "companies": {}})


@router.get("/api/ipo-us")
def ipo_us(request: Request):
    return file_response(request, IPO_US_FILE, {"fetched_at": None, "count": 0, "items": []})
