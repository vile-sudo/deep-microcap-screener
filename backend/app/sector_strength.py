"""Sector Strength: how broadly each tracked sector's stocks are moving over 1, 2, 3 and 5 sessions.

A breadth monitor, not a signal. Backtested before building (scripts/backtest_sector_strength.py,
Oct 2026): breadth gave at most a small, inconsistent tilt in forward returns, so the page shows what is
happening -- "35 of 50 up over 3 days" -- and never labels anything a buy.

Members: every listed company (NSE and BSE, main board and SME) in the sector's NSE basic industries,
as screener.in lists them (scripts/run_sector_members_in.py -> data/sector_strength/members.json, weekly).
Prices: the exchange bhavcopies scripts/update_charts.py already keeps (.bhav_cache), so this costs no
extra download. Returns chain each session's close / previous close as the exchange prints them, so a
split or bonus inside the window doesn't show up as a crash.

Benchmark: the Nifty 500 -- the index's own return over each window (NSE's daily index close file,
ind_close_all_DDMMYYYY.csv, cached next to the bhavcopies) and the breadth of its 500 constituents, so a
sector's "70% up" can be read against the market's.

write(days) is called at the end of scripts/update_charts.py and writes data/sector_strength/latest.json
(per-stock returns, the frontend aggregates them for its filters) and history.json (each sector's
breadth for the last HISTORY_SESSIONS sessions, for the sparklines). GET /api/sector-strength serves both.
"""
from __future__ import annotations

import csv
import io
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BACKEND_DIR / "data" / "sector_strength"
MEMBERS_FILE = OUT_DIR / "members.json"
LATEST_FILE = OUT_DIR / "latest.json"
HISTORY_FILE = OUT_DIR / "history.json"
WINDOWS = (1, 2, 3, 5)
HISTORY_SESSIONS = 60
LIQUID_TURNOVER_CR = 1.0      # 20-session average turnover, Rs crore
LIQUID_SESSIONS = 18          # traded on at least this many of the last 20
UP_THRESHOLD = 0.01           # "up more than 1%"

# NSE's industry classification as screener.in lays it out: /market/<sector>/<industry>/<sub>/<basic industry>/
SECTORS = [
    {"slug": "defence", "name": "Defence & Aerospace", "industries": [
        ("IN07/IN0702/IN070201/IN070201001", "Aerospace & Defense"),
        ("IN07/IN0702/IN070204/IN070204006", "Ship Building & Allied Services")]},
    {"slug": "chemicals", "name": "Chemicals", "industries": [
        ("IN01/IN0101/IN010101/IN010101002", "Specialty Chemicals"),
        ("IN01/IN0101/IN010101/IN010101001", "Commodity Chemicals"),
        ("IN01/IN0101/IN010101/IN010101004", "Dyes And Pigments"),
        ("IN01/IN0101/IN010101/IN010101003", "Carbon Black"),
        ("IN01/IN0101/IN010101/IN010101005", "Explosives"),
        ("IN01/IN0101/IN010101/IN010101006", "Petrochemicals"),
        ("IN01/IN0101/IN010101/IN010101007", "Printing Inks"),
        ("IN01/IN0101/IN010101/IN010101009", "Industrial Gases"),
        ("IN01/IN0101/IN010101/IN010101008", "Trading - Chemicals"),
        ("IN01/IN0101/IN010102/IN010102002", "Pesticides & Agrochemicals"),
        ("IN01/IN0101/IN010102/IN010102001", "Fertilizers")]},
    {"slug": "healthcare", "name": "Healthcare", "industries": [
        ("IN06/IN0601/IN060101/IN060101001", "Pharmaceuticals"),
        ("IN06/IN0601/IN060101/IN060101002", "Biotechnology"),
        ("IN06/IN0601/IN060102/IN060102001", "Medical Equipment & Supplies"),
        ("IN06/IN0601/IN060103/IN060103001", "Hospital"),
        ("IN06/IN0601/IN060103/IN060103002", "Healthcare Service Provider"),
        ("IN06/IN0601/IN060103/IN060103003", "Healthcare Research, Analytics & Technology")]},
    {"slug": "capital-markets", "name": "Capital Markets", "industries": [
        ("IN05/IN0501/IN050103/IN050103001", "Asset Management Company"),
        ("IN05/IN0501/IN050103/IN050103006", "Stockbroking & Allied"),
        ("IN05/IN0501/IN050103/IN050103005", "Exchange and Data Platform"),
        ("IN05/IN0501/IN050103/IN050103002", "Depositories, Clearing Houses and Other Intermediaries"),
        ("IN05/IN0501/IN050103/IN050103003", "Financial Products Distributor"),
        ("IN05/IN0501/IN050103/IN050103004", "Ratings"),
        ("IN05/IN0501/IN050103/IN050103007", "Other Capital Market related Services")]},
    {"slug": "electrical-equipment", "name": "Electrical Equipment", "industries": [
        ("IN07/IN0702/IN070203/IN070203001", "Heavy Electrical Equipment"),
        ("IN07/IN0702/IN070203/IN070203002", "Other Electrical Equipment"),
        ("IN07/IN0702/IN070205/IN070205003", "Cables - Electricals")]},
]


INDEX_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d}.csv"
INDEX_NAME = "Nifty 500"
BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def index_closes(dates: list, cache: Path | None) -> dict:
    """{date: Nifty 500 close} for the given sessions; each day's file fetched once and cached."""
    import requests
    out = {}
    s = requests.Session()
    for d in dates:
        f = cache / f"IDX_{d.strftime('%Y%m%d')}.csv" if cache else None
        text = f.read_text(encoding="utf-8") if f and f.exists() else None
        if text is None:
            try:
                r = s.get(INDEX_URL.format(d=d.strftime("%d%m%Y")), headers=BROWSER, timeout=30)
            except Exception:  # noqa: BLE001 - a missing day leaves a gap, never fails the run
                continue
            if r.status_code != 200 or not r.text.startswith("Index Name"):
                continue
            text = r.text
            if f:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text, encoding="utf-8")
            time.sleep(0.3)
        for row in csv.DictReader(io.StringIO(text)):
            if (row.get("Index Name") or "").strip().lower() == INDEX_NAME.lower():
                try:
                    out[d] = float(row["Closing Index Value"])
                except (KeyError, ValueError):
                    pass
    return out


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _row(day_books, ex: str, key: str):
    nse, bse = day_books
    return (nse if ex == "NSE" else bse).get(key)


def _resolve(m: dict, latest_nse: dict, latest_bse: dict, by_isin_nse: dict, by_isin_bse: dict):
    """(exchange, bhavcopy key) for a member: its NSE line when it trades there, else its BSE line."""
    code = str(m["code"])
    if not code.isdigit() and code in latest_nse:
        return "NSE", code
    if code.isdigit() and code in latest_bse:
        isin = latest_bse[code][9]
        if isin and isin in by_isin_nse:
            return "NSE", by_isin_nse[isin]
        return "BSE", code
    if not code.isdigit() and code in by_isin_nse.values():
        return "NSE", code
    return None, None


def _returns(days: list, ex: str, key: str, end: int) -> dict:
    """Chained close/prev-close returns over each window ending at session index `end`."""
    out, prod, n = {}, 1.0, 0
    for back in range(max(WINDOWS)):
        i = end - back
        if i < 0:
            break
        r = _row((days[i][1], days[i][2]), ex, key)
        if r and r[8] > 0 and r[6] > 0 and r[7] > 0:
            prod *= r[6] / r[8]
            n += 1
        if back + 1 in WINDOWS:
            out[back + 1] = (prod - 1) * 100 if n else None
    return out


def _daily(days: list, ex: str, key: str, end: int) -> list:
    """Each of the last max(WINDOWS) sessions' own % change, oldest first (None: didn't trade that day)."""
    out = []
    for i in range(end - max(WINDOWS) + 1, end + 1):
        r = _row((days[i][1], days[i][2]), ex, key) if i >= 0 else None
        out.append(round((r[6] / r[8] - 1) * 100, 2) if r and r[8] > 0 and r[6] > 0 and r[7] > 0 else None)
    return out


def _breadth(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "up": sum(v > 0 for v in vals), "down": sum(v < 0 for v in vals),
            "up1": sum(v > UP_THRESHOLD * 100 for v in vals), "median": round(statistics.median(vals), 2)}


def write(days: list, cache: Path | None = None) -> dict | None:
    """Compute the latest snapshot and the breadth history from the cached bhavcopies (oldest first)."""
    from .charts import drop_repeated_days
    days = drop_repeated_days(days)
    mfile = _load(MEMBERS_FILE, {})
    members = mfile.get("sectors") or {}
    if not members or len(days) < 25:
        print("sector strength: no member list or too little price history; skipped")
        return None
    last = len(days) - 1
    latest_nse, latest_bse = days[-1][1], days[-1][2]
    by_isin_nse = {r[9]: k for k, r in latest_nse.items() if r[9]}
    by_isin_bse = {r[9]: k for k, r in latest_bse.items() if r[9]}

    # the market yardstick: median return of every stock with Rs 1 cr+ average turnover
    def turnover(ex, key, end):
        t, s = 0.0, 0
        for i in range(max(0, end - 19), end + 1):
            r = _row((days[i][1], days[i][2]), ex, key)
            if r and r[7] > 0:
                t += r[6] * r[7]
                s += 1
        return t / 20 / 1e7, s

    def market_median(end):
        vals = {w: [] for w in WINDOWS}
        for ex, book in (("NSE", days[end][1]), ("BSE", days[end][2])):
            for key, r in book.items():
                if ex == "BSE" and r[9] and r[9] in by_isin_nse:
                    continue
                if not r[7] or r[6] * r[7] < 1e7 * 0.2:      # quick pre-filter before the 20-day average
                    continue
                avg, s = turnover(ex, key, end)
                if avg < LIQUID_TURNOVER_CR or s < LIQUID_SESSIONS:
                    continue
                rets = _returns(days, ex, key, end)
                for w in WINDOWS:
                    if rets.get(w) is not None:
                        vals[w].append(rets[w])
        return {w: round(statistics.median(v), 2) if v else None for w, v in vals.items()}

    market = market_median(last)
    first = max(0, last - HISTORY_SESSIONS - max(WINDOWS) + 1)
    idx = index_closes([d[0] for d in days[first:]], cache)

    def idx_ret(end, w):
        a, b = idx.get(days[end][0]), idx.get(days[end - w][0]) if end - w >= 0 else None
        return round((a / b - 1) * 100, 2) if a and b else None
    n500 = [k for k in (mfile.get("nifty500") or []) if k in latest_nse]
    sectors_out, resolved = [], {}
    for sec in SECTORS:
        rows = []
        for m in members.get(sec["slug"], []):
            ex, key = _resolve(m, latest_nse, latest_bse, by_isin_nse, by_isin_bse)
            if not ex:
                rows.append({**m, "exchange": None, "status": "no recent trade"})
                continue
            r = _row((latest_nse, latest_bse), ex, key)
            avg, s = turnover(ex, key, last)
            rets = _returns(days, ex, key, last)
            resolved.setdefault(sec["slug"], []).append((ex, key))
            rows.append({**m, "exchange": ex, "symbol": r[1], "full_name": r[2] or m.get("name"), "close": r[6],
                         "d": _daily(days, ex, key, last), "traded_today": r[7] > 0, "turnover_cr": round(avg, 2), "sessions20": s,
                         "liquid": avg >= LIQUID_TURNOVER_CR and s >= LIQUID_SESSIONS,
                         **{f"r{w}": None if rets.get(w) is None else round(rets[w], 2) for w in WINDOWS}})
        sectors_out.append({"slug": sec["slug"], "name": sec["name"], "industries": [i[1] for i in sec["industries"]], "stocks": rows})

    def group_breadth(keys, end):
        vals = {w: [] for w in WINDOWS}
        for ex, key in keys:
            rets = _returns(days, ex, key, end)
            for w in WINDOWS:
                if rets.get(w) is not None:
                    vals[w].append(rets[w])
        return {w: _breadth(vals[w]) for w in WINDOWS}

    n500_keys = [("NSE", k) for k in n500]
    b500 = group_breadth(n500_keys, last)
    sessions = [days[i][0].isoformat() for i in range(last - max(WINDOWS) + 1, last + 1)]
    daily500 = {w: [] for w in range(len(sessions))}
    for k in n500:
        for j, v in enumerate(_daily(days, "NSE", k, last)):
            if v is not None:
                daily500[j].append(v)
    benchmark = {"name": INDEX_NAME, "close": idx.get(days[last][0]), "constituents": len(n500),
                 "d": [idx_ret(i, 1) for i in range(last - max(WINDOWS) + 1, last + 1)],
                 "d_breadth": [_breadth(daily500[j]) for j in range(len(sessions))],
                 **{f"r{w}": idx_ret(last, w) for w in WINDOWS},
                 "breadth": {str(w): b500[w] for w in WINDOWS}}

    # breadth history for the sparklines: every member that traded, each of the last sessions
    hist = {"dates": [], "market": {str(w): [] for w in WINDOWS}, "nifty500": {str(w): [] for w in WINDOWS}, "sectors": {}}
    for end in range(max(0, last - HISTORY_SESSIONS + 1), last + 1):
        hist["dates"].append(days[end][0].isoformat())
        b = group_breadth(n500_keys, end)
        for w in WINDOWS:
            hist["nifty500"][str(w)].append([round(100 * b[w]["up"] / b[w]["n"], 1) if b[w]["n"] else None, idx_ret(end, w)])
        for sec in SECTORS:
            h = hist["sectors"].setdefault(sec["slug"], {str(w): [] for w in WINDOWS})
            vals = {w: [] for w in WINDOWS}
            for ex, key in resolved.get(sec["slug"], []):
                rets = _returns(days, ex, key, end)
                for w in WINDOWS:
                    if rets.get(w) is not None:
                        vals[w].append(rets[w])
            for w in WINDOWS:
                b = _breadth(vals[w])
                h[str(w)].append([round(100 * b["up"] / b["n"], 1), b["median"]] if b["n"] else None)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    latest = {"generated_at": now, "asof": days[-1][0].isoformat(), "windows": list(WINDOWS), "sessions": sessions,
              "market": {str(w): v for w, v in market.items()}, "benchmark": benchmark, "liquid_turnover_cr": LIQUID_TURNOVER_CR,
              "sme": mfile.get("sme") or [], "sectors": sectors_out}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_FILE.write_text(json.dumps(latest, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    HISTORY_FILE.write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")
    n = sum(len(s["stocks"]) for s in sectors_out)
    print(f"sector strength: {n} stocks across {len(sectors_out)} sectors as of {latest['asof']}; "
          f"Nifty 500 3D {benchmark['r3']}%, {b500[3].get('up', 0)}/{b500[3].get('n', 0)} constituents up")
    return latest
