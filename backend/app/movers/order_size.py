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
