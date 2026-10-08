"""Results calendar for the India board: when each company reports its next results.

    cd backend
    python scripts/run_results_calendar_in.py

Every board company gets ONE entry -- its next results event -- with an honest label for how sure the date is:

  confirmed  the company has told NSE its board will meet on that date to approve results (NSE's board-meeting
             notices, mainboard and Emerge/SME). Notices appear about a week ahead, so early in a results season
             most companies are not confirmed yet.
  expected   no notice yet, but the company reported this same quarter on a known date last year; the date is
             last year's, moved to the same weekday. Companies keep a very steady calendar.
  deadline   no notice and no history on NSE (BSE-only companies mostly): the SEBI deadline for its next
             quarter -- 45 days after quarter-end for quarterly reporters -- as the latest it can report.

The "next quarter" is worked out from what the company has already reported (data/results_in, written by
scripts/run_results_in.py). Written to data/results_calendar_in/latest.json (GET /api/results-calendar-in).
Runs several times a day from .github/workflows/results_calendar_in.yml -- notices arrive continuously. If NSE
cannot be reached the previous file is kept, never blanked.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
from app.movers.nse import NseClient  # noqa: E402

RAW = BACKEND_DIR / "data" / "companies_raw.json"
RESULTS = BACKEND_DIR / "data" / "results_in" / "latest.json"
OUT_FILE = BACKEND_DIR / "data" / "results_calendar_in" / "latest.json"
REF = "https://www.nseindia.com/companies-listing/corporate-filings-board-meetings"
URL = "https://www.nseindia.com/api/corporate-board-meetings?index={index}&from_date={f}&to_date={t}"
AHEAD_DAYS = 75              # how far ahead notices are collected
GRACE_DAYS = 2               # a result declared yesterday still shows today
PAST_DAYS = 7                # the calendar view keeps the current week filled in back to Monday
ANNOUNCEMENTS = BACKEND_DIR / "data" / "announcements" / "latest.json"
RESULTS_FILING = re.compile(r"financial result|outcome of board meeting|integrated filing", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
RESULTS_LIKE = re.compile(r"financial results?|unaudited|audited (?:financial )?results?|results for the (?:quarter|period|half|year)|quarter ended|half[- ]year ended|year ended", re.I)


def nse_day(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def parse_day(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%d-%b-%Y").date()
    except (ValueError, TypeError):
        return None


def fetch(client: NseClient, index: str, f: date, t: date) -> list[dict]:
    out = []
    d = f
    while d <= t:                                        # 30-day slices keep each response small
        e = min(d + timedelta(days=29), t)
        try:
            data = client.get_json(URL.format(index=index, f=nse_day(d), t=nse_day(e)), referer=REF)
            out += data if isinstance(data, list) else []
        except Exception as ex:  # noqa: BLE001
            print(f"  NSE {index} {d}..{e}: {str(ex)[:80]}")
        d = e + timedelta(days=1)
    return out


def results_notices(rows: list[dict]) -> dict[str, list[dict]]:
    """symbol -> [{date, desc, url, purpose}] for notices that are about financial results."""
    by: dict[str, list[dict]] = {}
    for x in rows:
        text = f"{x.get('bm_purpose') or ''} {x.get('bm_desc') or ''}"
        day = parse_day(x.get("bm_date"))
        if day and RESULTS_LIKE.search(text):
            by.setdefault(x.get("bm_symbol"), []).append({"date": day, "desc": (x.get("bm_desc") or "")[:300], "url": x.get("attachment"),
                                                          "purpose": x.get("bm_purpose"), "name": x.get("sm_name"),
                                                          "industry": x.get("sm_indusrty"), "ts": x.get("bm_timestamp") or ""})
    return by


def quarter_of(month: int, year: int) -> tuple[str, int]:
    """Quarter-end month/year -> ('Q2', FY) with the Indian fiscal year that ends the following March."""
    q = {6: "Q1", 9: "Q2", 12: "Q3", 3: "Q4"}.get(month, "")
    fy = year + 1 if month > 3 else year
    return q, fy


def desc_qe(desc: str) -> tuple[int, int] | None:
    """(year, month) of the period a notice's text says the results are for, if it says."""
    m = re.search(r"(?:ended|ending)\s+(?:on\s+)?(?:(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?\s*,?\s*)?([A-Za-z]{3,9})\.?\s*(?:\d{1,2}(?:st|nd|rd|th)?,?\s*)?(20\d{2})", desc or "", re.I)
    return (int(m.group(2)), MONTHS[m.group(1)[:3].lower()]) if m and m.group(1)[:3].lower() in MONTHS else None


def label_from(desc: str, fallback_qe: tuple[int, int] | None, use_desc_date: bool = True) -> str:
    """'Q2 FY27 results' / 'Half-yearly results' / 'Annual results (Q4 FY26)' from the notice text."""
    d = desc or ""
    m = re.search(r"(?:ended|ending|period ended|quarter ended)\s+(?:on\s+)?(?:(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*)?([A-Za-z]{3,9})\.?\s*(?:(\d{1,2}),?\s*)?(20\d{2})", d, re.I)
    month = year = None
    if use_desc_date and m and m.group(2)[:3].lower() in MONTHS:
        month, year = MONTHS[m.group(2)[:3].lower()], int(m.group(4))
    elif fallback_qe:
        year, month = fallback_qe
    half = re.search(r"half[- ]year|six months", d, re.I) and not re.search(r"\bquarter\b", d, re.I)
    if month and year:
        q, fy = quarter_of(month, year)
        if month == 3 and re.search(r"year ended|annual", d, re.I) and not re.search(r"\bquarter\b", d, re.I):
            return f"Annual results (FY{str(fy)[2:]})"
        if half:
            return f"Half-yearly results (H{1 if month == 9 else 2} FY{str(fy)[2:]})"
        if q:
            return f"{q} FY{str(fy)[2:]} results"
    return "Half-yearly results" if half else "Financial results"


def next_quarter_end(latest_period: str | None, today: date) -> tuple[int, int]:
    """(year, month) of the quarter a company reports next: the quarter after its latest reported one
    ('Jun 2026' -> Sep 2026), moved forward past any quarter whose SEBI deadline (45 days after quarter-end)
    is already over -- a company whose page is behind, or a half-yearly reporter, is not "due" in the past."""
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", latest_period or "")
    if m and m.group(1).lower() in MONTHS:
        y, mo = int(m.group(2)), MONTHS[m.group(1).lower()]
        for _ in range(12):
            mo += 3
            if mo > 12:
                mo -= 12
                y += 1
            if month_end(y, mo) + timedelta(days=45) >= today - timedelta(days=GRACE_DAYS):
                return y, mo
    # no reported quarter on file: the first quarter whose SEBI deadline has not yet passed
    for y in (today.year - 1, today.year, today.year + 1):
        for mo in (3, 6, 9, 12):
            if month_end(y, mo) + timedelta(days=45) >= today - timedelta(days=GRACE_DAYS):
                return y, mo
    return today.year, 9


def month_end(y: int, m: int) -> date:
    return (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


def same_quarter_last_year(past: list[dict], qy: int, qm: int, today: date):
    """Last year's results notice for the quarter that is coming (its text says the period ended one year
    before it), dated far enough ahead that a year on it is still in the future."""
    for n in past:
        if n["date"] + timedelta(days=364) < today - timedelta(days=GRACE_DAYS):
            continue
        qe = desc_qe(n["desc"])
        if qe is None or qe == (qy - 1, qm):
            return n
    return None


def results_filings() -> dict[str, list[tuple[str, str]]]:
    """symbol -> [(filed date, url)] of results filings in the announcements feed (scripts/run_announcements.py)."""
    try:
        rows = json.loads(ANNOUNCEMENTS.read_text(encoding="utf-8")).get("announcements") or []
    except (OSError, ValueError):
        return {}
    out: dict[str, list[tuple[str, str]]] = {}
    for a in rows:
        if RESULTS_FILING.search(a.get("category") or "") and a.get("symbol"):
            out.setdefault(a["symbol"], []).append((a.get("date") or "", a.get("url") or ""))
    return out


def calendar_events(notices: dict, items: list[dict], board: list[dict], today: date) -> list[dict]:
    """Everything the calendar grid shows: every NSE company's confirmed results date (its latest
    board-meeting notice, so a rescheduled meeting shows once, at its new date), plus board companies
    with no notice yet at their expected date. SEBI-deadline placeholders stay out -- they aren't dates
    anyone announced, and would pile a third of the board onto one day. Past days of the last week keep
    their entries, marked with the results filing once it's in."""
    on_board = {str(c.get("nse_code")): c for c in board if c.get("nse_code")}
    filed = results_filings()
    lo, hi = today - timedelta(days=PAST_DAYS), today + timedelta(days=AHEAD_DAYS)
    events, confirmed_syms = [], set()
    for sym, ns in notices.items():
        ns = [n for n in ns if lo <= n["date"] <= hi]
        if not sym or not ns:
            continue
        n = max(ns, key=lambda x: (x["ts"], x["date"]))
        c = on_board.get(sym)
        confirmed_syms.add(sym)
        events.append({"symbol": sym, "code": str(c["code"]) if c else None, "name": (c or {}).get("name") or n["name"] or sym,
                       "industry": n["industry"], "date": n["date"].isoformat(), "status": "confirmed",
                       "result": label_from(n["desc"], None), "url": n["url"], "board": bool(c)})
    for i in items:
        if i["status"] == "expected" and i.get("symbol") not in confirmed_syms:
            events.append({"symbol": i.get("symbol") or i["code"], "code": i["code"], "name": i["name"], "industry": None,
                           "date": i["date"], "status": "expected", "result": i["result"], "url": None, "board": True,
                           "last_year": i.get("last_year")})
    for e in events:
        if e["date"] > today.isoformat():
            continue
        hit = sorted(f for f in filed.get(e["symbol"], []) if e["date"] <= f[0] <= (date.fromisoformat(e["date"]) + timedelta(days=3)).isoformat())
        if hit:
            e["results_at"], e["results_url"] = hit[0]
    events.sort(key=lambda e: (e["date"], not e["board"], e["name"] or ""))
    return events


def main() -> int:
    board = json.loads(RAW.read_text(encoding="utf-8"))
    try:
        results = (json.loads(RESULTS.read_text(encoding="utf-8")).get("companies")) or {}
    except (OSError, ValueError):
        results = {}
    today = datetime.now(timezone.utc).date()

    client = NseClient()
    ahead = []
    last_year = []
    for index in ("equities", "sme"):
        ahead += fetch(client, index, today - timedelta(days=PAST_DAYS), today + timedelta(days=AHEAD_DAYS))
        ly = today - timedelta(days=365)
        last_year += fetch(client, index, ly - timedelta(days=GRACE_DAYS), ly + timedelta(days=AHEAD_DAYS))
    if not ahead and not last_year:
        print("run_results_calendar_in: NSE returned nothing; keeping the previous file")
        return 0
    notices, history = results_notices(ahead), results_notices(last_year)

    items = []
    for c in board:
        code = str(c["code"])
        sym = c.get("nse_code")
        res = results.get(code) or {}
        periods = (res.get("quarters") or {}).get("periods") or []
        qy, qm = next_quarter_end(periods[-1] if periods else None, today)
        base = {"code": code, "symbol": sym, "name": c.get("name")}
        upcoming = sorted((n for n in notices.get(sym, []) if n["date"] >= today - timedelta(days=GRACE_DAYS)), key=lambda n: n["date"]) if sym else []
        past = sorted(history.get(sym, []), key=lambda n: n["date"]) if sym else []
        if upcoming:
            n = upcoming[0]
            items.append({**base, "date": n["date"].isoformat(), "status": "confirmed", "result": label_from(n["desc"], (qy, qm)),
                          "detail": n["desc"], "url": n["url"],
                          "last_year": None})
        elif same_quarter_last_year(past, qy, qm, today):
            n = same_quarter_last_year(past, qy, qm, today)
            d = n["date"] + timedelta(days=364)                    # the same weekday a year on
            items.append({**base, "date": d.isoformat(), "status": "expected", "result": label_from(n["desc"], (qy, qm), use_desc_date=False),
                          "detail": "No board-meeting notice yet. Reported this quarter on " + n["date"].strftime("%d %b %Y") + " last year.",
                          "url": None, "last_year": n["date"].isoformat()})
        else:
            deadline = month_end(qy, qm) + timedelta(days=45)
            q, fy = quarter_of(qm, qy)
            items.append({**base, "date": deadline.isoformat(), "status": "deadline", "result": f"{q} FY{str(fy)[2:]} results" if q else "Financial results",
                          "detail": "No date published and none on record from last year: the SEBI deadline for quarterly results (45 days after quarter-end). Half-yearly reporters have the same deadline.",
                          "url": None, "last_year": None})
    horizon = today + timedelta(days=AHEAD_DAYS)
    items = [i for i in items if today - timedelta(days=GRACE_DAYS) <= date.fromisoformat(i["date"]) <= horizon or i["status"] == "deadline"]
    items.sort(key=lambda i: (i["date"], {"confirmed": 0, "expected": 1, "deadline": 2}[i["status"]], i["name"] or ""))

    events = calendar_events(notices, items, board, today)
    payload = {"asof": today.isoformat(), "items": items, "events": events}
    try:
        prev = json.loads(OUT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    if {k: v for k, v in prev.items() if k != "fetched_at"} == payload:
        print("run_results_calendar_in: nothing changed")
        return 0
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **payload}, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    from collections import Counter
    n10 = sum(1 for i in items if date.fromisoformat(i["date"]) <= today + timedelta(days=10))
    print(f"run_results_calendar_in: {len(items)} board companies: {dict(Counter(i['status'] for i in items))}; {n10} due within 10 days; "
          f"calendar: {len(events)} events ({sum(1 for e in events if not e['board'])} off the board, "
          f"{sum(1 for e in events if e.get('results_url'))} with results out)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
