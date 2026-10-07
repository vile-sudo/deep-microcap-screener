"""Order size for an "order win" filing, as a share of the company's last full-year sales.

The NSE feed's own text never carries the order value ("... has informed the Exchange about
Bagging/Receiving of orders/contracts"), so it is read from the filing's PDF. A 24-month check
(Jul 2024-Jun 2026) found size is what decides how hard the stock moves on the filing day -- orders
of 15-40% of annual sales beat the market that session 80% of the time against 60% for orders under
5% -- though not what happens after it, so this explains a move rather than predicting one.

Best-effort throughout: any failure (scanned PDF, no rupee value, screener.in miss) returns None.
"""
from __future__ import annotations

import io
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
           "Referer": "https://www.nseindia.com/"}
SALES_TTL_DAYS = 60
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

AMOUNT = re.compile(
    r"(?P<cur>rs\.?|inr|₹|usd|us\s?\$|\$|eur|€)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>crores?|cr\.?|lakhs?|lacs?|million|mn|billion|bn)?\b", re.I)
NEAR_ORDER = re.compile(r"order|contract|value|worth|aggregat|amount|consideration|size|award", re.I)
# Company-level figures that sit in the same press release ("outstanding order book now stands at Rs 5,395
# crore", "order inflow for FY27", performance guarantees) and would otherwise win the "largest amount" pick.
NOT_THIS_ORDER = re.compile(r"order\s*book|order\s*inflow|inflows?|turnover|revenue|net\s*worth|market\s*cap|"
                            r"guarantee|total\s*income|paid[- ]?up|share\s*capital", re.I)
# Filed under NSE's order categories but not an order: arbitration awards, tax orders and refunds.
NOT_AN_ORDER = re.compile(r"arbitra(?:l|tion|tor)|assessment year|income[- ]tax|show cause|demand notice|"
                          r"tax refund|refund amount", re.I)


def pdf_text(url: str, pages: int = 6) -> str:
    if not url or not url.lower().endswith(".pdf"):
        return ""
    try:
        from pypdf import PdfReader
        r = requests.get(url, headers=HEADERS, timeout=40)
        if not r.ok or r.content[:4] != b"%PDF":
            return ""
        reader = PdfReader(io.BytesIO(r.content))
        return "\n".join((p.extract_text() or "") for p in reader.pages[:pages])
    except Exception:  # noqa: BLE001 - a bad PDF must never break the feed
        return ""


def _to_crore(cur: str | None, num: str, unit: str | None) -> float | None:
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return None
    cur = (cur or "").lower().replace(" ", "")
    unit = (unit or "").lower().rstrip(".")
    usd, eur = cur in ("usd", "us$", "$"), cur in ("eur", "€")
    fx = 85 if usd else 95 if eur else 1          # rough rupee rate; this is a size, not an accounting figure
    if unit.startswith("cr"):
        cr = v * fx
    elif unit.startswith(("lakh", "lac")):
        cr = v / 100 * fx
    elif unit in ("million", "mn"):
        cr = v * 0.1 * fx
    elif unit in ("billion", "bn"):
        cr = v * 100 * fx
    elif cur and v >= 1e5:                          # plain rupees, e.g. "Rs. 12,34,56,789"
        cr = v / 1e7 * fx
    else:
        return None
    return cr if 0.05 <= cr <= 200_000 else None


def order_value_cr(text: str) -> float | None:
    """The largest money amount sitting next to order/contract wording, in Rs crore."""
    best = None
    for m in AMOUNT.finditer(text or ""):
        if not (m.group("cur") or m.group("unit")):
            continue
        cr = _to_crore(m.group("cur"), m.group("num"), m.group("unit"))
        if cr is None or NOT_THIS_ORDER.search(text[max(0, m.start() - 80): m.start()]):
            continue
        if NEAR_ORDER.search(text[max(0, m.start() - 220): m.end() + 120]) and (best is None or cr > best):
            best = cr
    return best


def not_an_order(text: str) -> bool:
    return bool(NOT_AN_ORDER.search(text or ""))


# ------------------------------------------------------------------ execution period
# SEBI's Regulation 30 order disclosure has a "time period by which the order is to be executed" field.
# PDF extraction of that two-column table interleaves label and value, so the value usually sits INSIDE
# the label ("Time period by which the 21 Months order(s)/contract(s) is to be executed", "is to be
# 28-DEC-27 executed"): the label span, a little before it and the rest of the line after it (cut at the
# next field) are all searched. A stated duration wins over a date.
EXEC_FIELD = re.compile(r"(?:time\s*period|period)[^.]{0,120}?execut\w*", re.I | re.S)
NEXT_FIELD = re.compile(r"broad\s+(?:commercial\s+)?consideration|size\s+of\s+(?:the\s+)?order|whether\s+(?:the\s+)?promoter|"
                        r"related\s+party|name\s+of\s+the\s+entity|\n\s*\(?[a-h1-9][).]\s", re.I)
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "eighteen": 18, "twenty": 20, "twenty four": 24,
          "twenty-four": 24, "thirty": 30, "thirty six": 36, "thirty-six": 36, "forty eight": 48, "sixty": 60}
_NUM = r"(?P<n>\d+(?:\.\d+)?|" + "|".join(sorted((re.escape(w) for w in _WORDS), key=len, reverse=True)) + r")"
DURATION = re.compile(r"(?:\b\d+(?:\.\d+)?\s*(?:-|to)\s*)?\b" + _NUM + r"\s*(?:\([^)]{0,20}\)\s*)?"
                      r"(?P<unit>months?|mths?|years?|yrs?|weeks?|days)\b", re.I)
_MON = r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
DATES = (
    re.compile(r"\b(?P<d>\d{1,2})[-/. ]" + _MON + r"[-/. ,']*(?P<yr>20\d\d|\d\d)\b", re.I),     # 28-DEC-27, 31-Mar-2029
    re.compile(r"\b" + _MON + r"\s+(?P<d>\d{1,2}),?\s+(?P<yr>20\d\d)\b", re.I),                # August 10, 2026
    re.compile(r"\b(?P<d>\d{1,2})[./-](?P<mm>\d{1,2})[./-](?P<yr>20\d\d)\b"),                  # 05.11.2026
    re.compile(r"\b" + _MON + r"[\s,'.-]*(?P<yr>20\d\d)\b", re.I),                             # December 2030
)
EXEC_SENTENCE = re.compile(r"(?:execut|complet|deliver|suppl|tenure|implement|period\s+of)\w*[^.]{0,60}?"
                           r"(?:within|over|period\s+of|in|for)\s+(?:a\s+(?:period|tenure)\s+of\s+)?(?:about\s+|approximately\s+)?", re.I)


def _months(m: re.Match) -> float | None:
    raw, unit = m.group("n").lower(), m.group("unit").lower()
    n = float(raw) if raw[0].isdigit() else float(_WORDS[raw])
    months = n if unit.startswith("m") else n * 12 if unit.startswith("y") else n / 4.35 if unit.startswith("w") else n / 30.4
    return months if 0.25 <= months <= 180 else None


def _latest_date_months(region: str, filed: date) -> float | None:
    """Months from filing to the latest date written in the region (the end date of a period)."""
    best = None
    for rx in DATES:
        for m in rx.finditer(region):
            yr = int(m.group("yr"))
            yr = yr + 2000 if yr < 100 else yr
            mon = int(m.group("mm")) if "mm" in rx.groupindex else MONTHS[m.group("mon")[:3].title()]
            if not 1 <= mon <= 12:
                continue
            months = (yr - filed.year) * 12 + mon - filed.month
            if 1 <= months <= 180 and (best is None or months > best):
                best = float(months)
    return best


def execution_months(text: str, filed: date) -> float | None:
    """How long the order runs, in months, from the disclosure field or, failing that, the wording."""
    text = text or ""
    for f in EXEC_FIELD.finditer(text):
        after = text[f.end(): f.end() + 220]
        cut = NEXT_FIELD.search(after)
        region = text[max(0, f.start() - 90): f.end()] + " " + (after[: cut.start()] if cut else after)
        for d in DURATION.finditer(region):
            if v := _months(d):
                return v
        if v := _latest_date_months(region, filed):
            return v
    for s in EXEC_SENTENCE.finditer(text):
        d = DURATION.match(text, s.end())
        if d and (v := _months(d)):
            return v
    return None


# ------------------------------------------------------------------ filing-session move
# The one actionable finding of the backtest: stocks that jumped more than 8% (vs the market) on the
# filing session gave ground afterwards (60-session median -3.3%, 36% beat the market).
CHASE_EXCESS_PCT = 8.0


class FilingMoves:
    """Each filing's own-session close-to-close move and its excess over the market median, from NSE's
    bhavcopy (app/movers/bhavcopy.py). A filing after 15:30 belongs to the next session."""

    def __init__(self, today: date, max_sessions: int = 40):
        self.today = today
        self.max_sessions = max_sessions
        self.loaded: dict[date, tuple[dict, float] | None] = {}

    def _session(self, d: date):
        if d in self.loaded:
            return self.loaded[d]
        if len(self.loaded) >= self.max_sessions:
            return "budget"
        from statistics import median
        from .bhavcopy import MarketHoliday, load_closes
        try:
            closes = load_closes(d)
            moves = {s: c.pct_change for s, c in closes.items()}
            self.loaded[d] = (moves, median(v for v in moves.values() if -40 < v < 40))
        except MarketHoliday as e:
            # load_closes raises this both when NSE served another day's file (a real holiday) and on a
            # 404, which for a recent date can just mean "not published / archive hiccup" -- shifting the
            # filing to the next session on a hiccup would attribute the wrong day's move.
            if "was a trading holiday" not in str(e) and (self.today - d).days < 3:
                return "retry"
            self.loaded[d] = None
        except Exception:  # noqa: BLE001 - NSE unreachable: try again next run
            return "retry"
        return self.loaded[d]

    def move(self, symbol: str, filed_at: str) -> dict | None:
        """{"move_date", "move_pct", "move_excess"}, or None if the session isn't settled/available yet."""
        from datetime import datetime
        try:
            ts = datetime.fromisoformat(filed_at)
        except (TypeError, ValueError):
            return None
        d = ts.date() + timedelta(days=1) if (ts.hour, ts.minute) > (15, 30) else ts.date()
        for _ in range(7):
            while d.weekday() >= 5:
                d += timedelta(days=1)
            if d >= self.today:          # today's bhavcopy isn't out until the evening: next run
                return None
            got = self._session(d)
            if got in ("budget", "retry"):
                return None
            if got is None:              # a trading holiday: the next session is the filing's session
                d += timedelta(days=1)
                continue
            moves, mkt = got
            if symbol not in moves:
                return {"move_date": d.isoformat(), "move_pct": None, "move_excess": None}
            return {"move_date": d.isoformat(), "move_pct": round(moves[symbol], 2),
                    "move_excess": round(moves[symbol] - mkt, 2)}
        return None


class SalesLookup:
    """Annual sales per symbol from screener.in's profit & loss table, cached on disk."""

    def __init__(self, cache_file: Path):
        self.cache_file = cache_file
        try:
            self.cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.dirty = False

    def _fetch(self, symbol: str) -> dict:
        try:
            from bs4 import BeautifulSoup
            r = requests.get(f"https://www.screener.in/company/{symbol}/", headers={"User-Agent": HEADERS["User-Agent"]},
                             timeout=30)
            time.sleep(1.5)
            if not r.ok:
                return {}
            pl = BeautifulSoup(r.text, "html.parser").select_one("#profit-loss")
            if not pl:
                return {}
            head = [th.get_text(strip=True) for th in pl.select("table thead th")][1:]
            row = next((tr for tr in pl.select("table tbody tr")
                        if tr.select("td") and tr.select("td")[0].get_text(" ", strip=True).lower().startswith(("sales", "revenue"))),
                       None)
            if not row:
                return {}
            vals = [c.get_text(strip=True).replace(",", "") for c in row.select("td")[1:]]
            return {h: float(v) for h, v in zip(head, vals) if re.fullmatch(r"[A-Z][a-z]{2} \d{4}", h) and re.fullmatch(r"-?\d+(?:\.\d+)?", v)}
        except Exception:  # noqa: BLE001
            return {}

    def last_full_year(self, symbol: str, before: str) -> tuple[float, str] | None:
        """(sales in Rs crore, "Mar 2026") for the last fiscal year that ended before `before` (ISO date)."""
        hit = self.cache.get(symbol)
        stale = not hit or hit.get("fetched", "") < (date.today() - timedelta(days=SALES_TTL_DAYS)).isoformat()
        if stale:
            hit = {"fetched": date.today().isoformat(), "sales": self._fetch(symbol)}
            self.cache[symbol] = hit
            self.dirty = True
        best = None
        for label, v in sorted(hit["sales"].items(), key=lambda kv: (int(kv[0][-4:]), MONTHS.get(kv[0][:3], 0))):
            if f"{label[-4:]}-{MONTHS.get(label[:3], 12):02d}-31" < before and v > 0:
                best = (v, label)
        return best

    def save(self) -> None:
        if self.dirty:
            self.cache_file.write_text(json.dumps(self.cache, separators=(",", ":")), encoding="utf-8")
