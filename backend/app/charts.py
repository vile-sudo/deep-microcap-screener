"""
Daily price history for the Chart gallery.

Where the prices come from
--------------------------
The exchanges' own end-of-day "bhavcopy" files: one CSV per trading day
from NSE and one from BSE, each listing the open/high/low/close/volume of
every listed equity -- main board and SME platform alike. That matters
here: most of this board's SME names (QLINE, APSISAERO, 5447xx BSE SME
codes...) have no history at all on free price APIs, but every one of
them is in the bhavcopy.

  scripts/update_charts.py (the scheduled GitHub Action) keeps a rolling
  cache of those daily files, rebuilds a year of candles for every company
  on the board, and writes:

    chart_data/prices/<code>.json   {"symbol","exchange","rows":[[date,o,h,l,c,v],...]}
    chart_data/index.json           per-company chart stats for the gallery

Because the company list is read fresh from data/companies_raw.json on
every run, a company added to the board is charted on the next run with
full history (the cached daily files already contain it).

routers/charts.py serves those files. For a company that reached the board
after the last run it falls back to a live fetch from Yahoo Finance, which
covers most main-board names, so they show up before the next run too.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
CHART_DIR = BACKEND_DIR / "chart_data"
PRICES_DIR = CHART_DIR / "prices"
INDEX_FILE = CHART_DIR / "index.json"

SESSIONS = 250  # about one trading year
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}


# ---------------------------------------------------------------- bhavcopy
NSE_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip"
BSE_URL = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d}_F_0000.CSV"


def _get(url: str, session: requests.Session, referer: str) -> bytes | None:
    """The file's bytes, None when the exchange has no file for that day."""
    for attempt in range(4):
        try:
            r = session.get(url, headers={**_UA, "Referer": referer}, timeout=30)
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 200 and r.content:
            return r.content
        if r.status_code in (403, 404):
            return None
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"could not fetch {url}")


def fetch_bhavcopy(day: date, exchange: str, session: requests.Session) -> list[list] | None:
    """One day's equity rows as [key, symbol, name, o, h, l, c, v, prev_close], or None.

    key is the NSE trading symbol, or the BSE scrip code -- whichever the
    board's nse_code / bse_code fields carry for that exchange."""
    d = day.strftime("%Y%m%d")
    if exchange == "NSE":
        raw = _get(NSE_URL.format(d=d), session, "https://www.nseindia.com/")
        if raw is None:
            return None
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    else:
        raw = _get(BSE_URL.format(d=d), session, "https://www.bseindia.com/")
        if raw is None or raw[:6] != b"TradDt":
            return None
        text = raw.decode("utf-8", "replace")
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("FinInstrmTp") != "STK":
            continue
        try:
            o, h, l, c = (round(float(r[k]), 2) for k in ("OpnPric", "HghPric", "LwPric", "ClsPric"))
            v = int(float(r.get("TtlTradgVol") or 0))
            prev = round(float(r.get("PrvsClsgPric") or 0), 2)
        except (TypeError, ValueError):
            continue
        key = r["TckrSymb"] if exchange == "NSE" else r["FinInstrmId"]
        rows.append([key, r["TckrSymb"], r.get("FinInstrmNm") or "", o, h, l, c, v, prev])
    return rows


def write_day(path: Path, rows: list[list]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)


def read_day(path: Path) -> list[list]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return [[r[0], r[1], r[2], float(r[3]), float(r[4]), float(r[5]), float(r[6]), int(r[7]), float(r[8])]
                for r in csv.reader(f)]


# ------------------------------------------------------------------ series
def build_series(records: list[dict], days: list[tuple[date, dict, dict]]) -> dict[str, dict]:
    """Candles per board company from cached daily files, oldest first.

    days: [(date, {nse_symbol: row}, {bse_code: row}), ...] sorted by date.
    NSE is used when the company trades there, BSE otherwise, never a mix
    -- the two exchanges' closes differ by a few paise and a series that
    hops between them draws false wicks.

    A handful of board records carry a ticker the exchange no longer uses
    (a renamed company, a BSE listing filed under its NSE-style symbol).
    Those are matched instead on the BSE ticker symbol, then on the exact
    company name -- but only when that name belongs to one listing."""
    latest_nse, latest_bse = {}, {}
    for _, nse_rows, bse_rows in days:
        latest_nse.update(nse_rows)
        latest_bse.update(bse_rows)
    bse_by_ticker = {r[1]: k for k, r in latest_bse.items()}
    by_name: dict[str, set] = {}
    for exch, book in (("NSE", latest_nse), ("BSE", latest_bse)):
        for k, r in book.items():
            by_name.setdefault(_norm_name(r[2]), set()).add((exch, k))

    def collect(exch: str, key: str) -> list:
        idx = 1 if exch == "NSE" else 2
        raw, symbol = [], None
        for day, *books in days:
            r = books[idx - 1].get(key)
            if r:
                symbol = r[1]
                raw.append((day.isoformat(), r[3], r[4], r[5], r[6], r[7], r[8]))
        return raw and {"symbol": symbol, "exchange": exch, "rows": _adjust(raw)[-SESSIONS:]}

    out = {}
    for rec in records:
        nse = str(rec.get("nse_code") or "").strip()
        bse = str(rec.get("bse_code") or "").strip()
        code = str(rec.get("code") or "").strip()
        if not nse and code and not code.isdigit():
            nse = code
        if not bse and code.isdigit():
            bse = code
        tries = []
        if nse and not nse.isdigit():
            tries.append(("NSE", nse))
        if bse:
            tries.append(("BSE", bse))
        best = None
        for exch, key in tries:
            got = collect(exch, key)
            if got and (best is None or len(got["rows"]) > len(best["rows"]) + 20):
                best = got
        if not best and nse in bse_by_ticker:
            best = collect("BSE", bse_by_ticker[nse])
        if not best:
            hits = by_name.get(_norm_name(rec.get("name") or ""), set())
            if hits and len({k for _, k in hits}) <= 2 and len({e for e, _ in hits}) == len(hits):
                # one listing, possibly on both exchanges: prefer NSE
                exch, key = sorted(hits)[0]
                best = collect(exch, key)
        if best:
            out[code] = best
    return out


# Price factors of the corporate actions Indian companies actually do:
# splits 1:2..1:10, bonuses 1:1, 1:2, 2:1, 3:1, 3:2, 4:1, and consolidations.
_ACTION_FACTORS = sorted({1/2, 1/3, 2/3, 1/4, 3/4, 1/5, 2/5, 1/6, 1/8, 1/10, 1/20, 2, 5, 10})


def _adjust(raw: list[tuple]) -> list[list]:
    """Back-adjust candles for splits, bonuses and demergers.

    Without this a 1:5 split draws as an 80% crash and wrecks the trend,
    the averages and the 52-week high. The bhavcopy has no adjustment flag,
    so the ex-date is found from the prices themselves:

      * if the exchange restated the previous close it prints, that ratio
        is the factor, exactly;
      * otherwise an overnight gap beyond -25% / +33% is treated as a
        corporate action -- circuit limits cap a normal session's move at
        20%, so a gap that size does not happen on an ordinary day. The
        factor is snapped to the nearest common split/bonus ratio when one
        fits the ex-date's trading, else the observed ratio is used (a
        demerger has no neat ratio).
    Everything before the ex-date is multiplied by the factor."""
    out, factor = [], 1.0
    for i in range(len(raw) - 1, -1, -1):
        day, o, h, l, c, v, prev = raw[i]
        out.append([day, round(o * factor, 2), round(h * factor, 2), round(l * factor, 2),
                    round(c * factor, 2), int(round(v / factor))])
        if not i:
            continue
        last_close = raw[i - 1][4]
        if last_close <= 0:
            continue
        if prev > 0 and abs(prev / last_close - 1) > 0.02:
            factor *= prev / last_close
            continue
        gap = o / last_close
        close_days = (date.fromisoformat(day) - date.fromisoformat(raw[i - 1][0])).days <= 7
        if close_days and (gap < 0.75 or gap > 1.33):
            seen = (h + l + c) / 3 / last_close   # where the ex-date actually traded
            near = min(_ACTION_FACTORS, key=lambda f: abs(seen / f - 1))
            factor *= near if abs(seen / near - 1) <= 0.12 else seen
    out.reverse()
    return out


def _norm_name(name: str) -> str:
    n = name.upper().replace("&", " AND ")
    n = "".join(ch if ch.isalnum() else " " for ch in n)
    drop = {"LTD", "LIMITED", "THE", "CO", "COMPANY", "PVT", "PRIVATE"}
    return " ".join(w for w in n.split() if w not in drop)


# ------------------------------------------------------------------- stats
def _sma(closes: list[float], n: int) -> float | None:
    return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None


def compute_stats(series: dict) -> dict:
    """The numbers a gallery card shows, sorts and filters on."""
    rows = series["rows"]
    closes = [r[4] for r in rows]
    vols = [r[5] for r in rows]
    last = closes[-1]
    prev = closes[-2] if len(closes) > 1 else None
    high52 = max(r[2] for r in rows)
    low52 = min(r[3] for r in rows)
    dma50, dma200 = _sma(closes, 50), _sma(closes, 200)
    past = vols[-51:-1]
    avg_vol = sum(past) / len(past) if past and sum(past) else None
    from_high = round((last / high52 - 1) * 100, 2) if high52 else None

    if from_high is not None and from_high >= -1:
        status = "high"          # at (within 1% of) the 52-week high
    elif from_high is not None and from_high >= -5:
        status = "near"          # within 5% of it
    elif dma50 and dma200 and last > dma50 > dma200:
        status = "up"            # above a rising 50 and 200-day average
    elif (dma200 or dma50) and last < (dma200 or dma50):
        status = "down"          # below its long average
    else:
        status = "flat"

    return {
        "symbol": series["symbol"],
        "exchange": series["exchange"],
        "asof": rows[-1][0],
        "sessions": len(rows),
        "last": last,
        "chg_pct": round((last / prev - 1) * 100, 2) if prev else None,
        "vol_ratio": round(vols[-1] / avg_vol, 2) if avg_vol else None,
        "high52": high52,
        "low52": low52,
        "from_high_pct": from_high,
        "dma50": dma50,
        "dma200": dma200,
        "status": status,
    }


# -------------------------------------------------------- files + live fallback
def safe_name(code: str) -> str:
    return "".join(ch for ch in str(code) if ch.isalnum() or ch in "-_&") or "_"


def load_index() -> dict:
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"generated_at": None, "companies": {}}


def load_series(code: str) -> dict | None:
    try:
        return json.loads((PRICES_DIR / f"{safe_name(code)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1y&interval=1d"


def fetch_live(rec: dict, timeout: float = 12) -> dict | None:
    """A year of candles from Yahoo, for a company added since the last run.

    Only used until the scheduled job picks the company up. Yahoo has no
    history for NSE SME listings, so those wait for that run."""
    nse = str(rec.get("nse_code") or "").strip()
    bse = str(rec.get("bse_code") or "").strip()
    code = str(rec.get("code") or "").strip()
    syms = []
    if nse and not nse.isdigit():
        syms.append((f"{nse}.NS", "NSE"))
    if bse.isdigit():
        syms.append((f"{bse}.BO", "BSE"))
    if code and not syms:
        syms.append((f"{code}.BO", "BSE") if code.isdigit() else (f"{code}.NS", "NSE"))
    for sym, exch in syms:
        try:
            r = requests.get(_YAHOO.format(sym=sym), headers=_UA, timeout=timeout)
            res = ((r.json().get("chart") or {}).get("result") or [None])[0]
        except (requests.RequestException, ValueError):
            continue
        if not res:
            continue
        q = ((res.get("indicators") or {}).get("quote") or [{}])[0]
        cols = [q.get(k) or [] for k in ("open", "high", "low", "close", "volume")]
        off = (res.get("meta") or {}).get("gmtoffset") or 0
        rows = []
        for i, ts in enumerate(res.get("timestamp") or []):
            o, h, l, c, v = (col[i] if i < len(col) else None for col in cols)
            if None in (o, h, l, c):
                continue
            day = datetime.fromtimestamp(ts + off, tz=timezone.utc).strftime("%Y-%m-%d")
            rows.append([day, round(o, 2), round(h, 2), round(l, 2), round(c, 2), int(v or 0)])
        if len(rows) >= 20:  # one stray quote is not a chart
            return {"symbol": sym.rsplit(".", 1)[0], "exchange": exch, "rows": rows[-SESSIONS:]}
    return None
