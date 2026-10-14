"""Build and keep the Deals page's ~3-year history (app/disclosures_archive.py).

    cd backend
    python scripts/disclosures_archive.py backfill [--years 3]   # one-off: seed from NSE (resumable)
    python scripts/disclosures_archive.py update                  # nightly: fold in the feeds, publish
    python scripts/disclosures_archive.py publish                 # upload the local file to the release

backfill fetches, month by month (each month is recorded as done, so a stopped run carries on where it was):
  - bulk/block deals from NSE's historical report (app/movers/deals.py:historical_deals)
  - corporate announcements day by day (app/movers/announcements.py:range_announcements), routine filings
    dropped exactly as scripts/run_announcements.py drops them
  - insider trades from NSE's date-range PIT report (full transaction detail per row); for the recent months
    that report no longer covers, each filing's XBRL file from the current feed, as run_insider_trades.py does
update downloads the published file, adds whatever the three latest.json feeds hold, and publishes it again
(the nightly step in .github/workflows/daily.yml, after the feeds have run).
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import disclosures_archive as A  # noqa: E402

PIT_URL = "https://www.nseindia.com/api/corporates-pit"


def months(start: date, end: date):
    d = date(start.year, start.month, 1)
    while d <= end:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        yield d, min(nxt - timedelta(days=1), end)
        d = nxt


def done(con, key: str) -> bool:
    return con.execute("SELECT 1 FROM meta WHERE key=?", (key,)).fetchone() is not None


def mark(con, key: str, n: int) -> None:
    con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(n)))
    con.commit()


def _f(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _pct(v):
    x = _f(v)
    return None if x is None else round(x / 100, 6)


def _d(v: str | None) -> str | None:
    for fmt in ("%d-%b-%Y", "%d-%b-%Y %H:%M", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date().isoformat()
        except (TypeError, ValueError):
            pass
    return None


def pit_row(r: dict, names: dict) -> dict | None:
    """One row of NSE's date-range PIT report, in the feed's shape."""
    sym = str(r.get("symbol") or "").strip().upper()
    t = str(r.get("tdpTransactionType") or "").strip()
    day = _d(r.get("acqtoDt")) or _d(r.get("intimDt")) or _d(r.get("date"))
    if not sym or not t or not day:
        return None
    val = _f(r.get("secVal")) or 0.0
    return {"date": day, "symbol": sym, "name": names.get(sym, str(r.get("company") or sym)),
            "person": str(r.get("acqName") or "").strip(), "category": str(r.get("personCategory") or "Other").strip(),
            "transaction_type": t, "side": "BUY" if t.lower() == "buy" else "SELL" if t.lower() == "sell" else "OTHER",
            "quantity": _f(r.get("secAcq")), "value_cr": round(val / 1e7, 3), "mode": str(r.get("acqMode") or "").strip(),
            # this report gives holdings in percent; the XBRL feed (and the page) use a fraction
            "pre_qty": _f(r.get("befAcqSharesNo")), "pre_pct": _pct(r.get("befAcqSharesPer")),
            "post_qty": _f(r.get("afterAcqSharesNo")), "post_pct": _pct(r.get("afterAcqSharesPer")),
            "sec_type": str(r.get("secType") or "").strip(), "broadcast_at": str(r.get("date") or ""),
            "regulation": str(r.get("anex") or "")}


def backfill(years: int) -> None:
    from app.movers.announcements import range_announcements
    from app.movers.companies import name_lookup
    from app.movers.deals import historical_deals
    from app.movers.insider_trading import PIT_PAGE, fetch_detail, list_recent, open_session
    from app.movers.nse import NseClient
    names = name_lookup()
    end = date.today()
    start = date(end.year - years, end.month, 1)
    A.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    con = A.connect(A.DB_FILE)

    for a, b in months(start, end):                                       # ---- deals
        key = f"done:deals:{a:%Y-%m}"
        if done(con, key) and b < end:
            continue
        try:
            got = historical_deals(a, b, timeout=60)
        except Exception as e:  # noqa: BLE001
            print(f"deals {a:%Y-%m}: {e}")
            continue
        rows = [{"date": d.trade_date.isoformat(), "symbol": d.symbol, "name": names.get(d.symbol, d.symbol), "kind": d.kind,
                 "client": d.client, "side": d.side, "quantity": d.quantity, "price": d.price,
                 "value_cr": round(d.value_crore, 2)} for d in got]
        A.upsert(con, "deals", rows)
        mark(con, key, len(rows))
        print(f"deals {a:%Y-%m}: {len(rows)}", flush=True)
        time.sleep(1)

    session = open_session()                                              # ---- insider trades
    last_pit = None
    for a, b in months(start, end):
        key = f"done:ins:{a:%Y-%m}"
        if done(con, key) and b < end - timedelta(days=40):
            n = int(con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0])
            last_pit = b if n else last_pit
            continue
        rows = []
        for attempt in range(3):
            try:
                r = session.get(PIT_URL, params={"index": "equities", "from_date": f"{a:%d-%m-%Y}", "to_date": f"{b:%d-%m-%Y}"},
                                headers={"Accept": "application/json, text/plain, */*", "Referer": PIT_PAGE}, timeout=120)
                rows = [x for x in (pit_row(z, names) for z in (r.json().get("data") or [])) if x]
                break
            except Exception as e:  # noqa: BLE001
                print(f"insider {a:%Y-%m}: {e}; retrying")
                time.sleep(5)
                session = open_session()
        A.upsert(con, "ins", rows)
        mark(con, key, len(rows))
        if rows:
            last_pit = b
        print(f"insider {a:%Y-%m}: {len(rows)}", flush=True)
        time.sleep(1)
    # the months after the date-range report stops: the current feed's filings, one XBRL file each
    since = (last_pit or start).isoformat()
    try:
        meta = list_recent(session)
    except Exception as e:  # noqa: BLE001
        meta = []
        print(f"insider feed: {e}")
    todo, rows = [m for m in meta if (_d(str(m.get("broadcastDateTime") or "")[:11]) or "") > since], []
    print(f"insider XBRL: {len(todo)} filings after {since}", flush=True)
    for i, m in enumerate(todo):
        t = fetch_detail(session, m)
        time.sleep(0.2)
        if t is None:
            continue
        rows.append({"date": t.date, "symbol": t.symbol, "name": names.get(t.symbol, t.company_name or t.symbol),
                     "person": t.person, "category": t.category, "transaction_type": t.transaction_type, "side": t.side,
                     "quantity": t.quantity, "value_cr": round(t.value_cr, 3), "mode": t.mode, "pre_qty": t.pre_qty,
                     "pre_pct": t.pre_pct, "post_qty": t.post_qty, "post_pct": t.post_pct, "sec_type": "",
                     "broadcast_at": t.broadcast_at, "regulation": t.regulation})
        if len(rows) >= 200 or i == len(todo) - 1:
            A.upsert(con, "ins", rows)
            con.commit()
            rows = []

    client = NseClient()                                                  # ---- announcements
    for a, b in months(start, end):
        key = f"done:ann:{a:%Y-%m}"
        if done(con, key) and b < end - timedelta(days=3):
            continue
        rows, tmp = [], Path(tempfile.mkdtemp(prefix="ann-"))
        try:
            got = range_announcements(client, a, b, cache_dir=tmp)      # a month of raw filings, not kept
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        for r in got:
            if r["kind"] == "routine filing" or not r.get("seq_id"):
                continue
            rows.append({"seq_id": r["seq_id"], "date": r["filed_at"][:10], "filed_at": r["filed_at"], "symbol": r["symbol"],
                         "name": names.get(r["symbol"], r.get("company") or r["symbol"]), "category": r["category"],
                         "kind": r["kind"], "weight": r["weight"], "summary": (r.get("summary") or "")[:320], "url": r["url"]})
        A.upsert(con, "ann", rows)
        mark(con, key, len(rows))
        print(f"announcements {a:%Y-%m}: {len(rows)}", flush=True)

    A.fold_feeds(con)
    con.execute("VACUUM")
    con.close()
    report()


def report() -> None:
    con = A.connect(A.DB_FILE)
    for k in ("deals", "ann", "ins"):
        n, a, b = con.execute(f"SELECT COUNT(*), MIN(date), MAX(date) FROM {k}").fetchone()
        print(f"  {k}: {n} rows, {a} to {b}")
    con.close()
    print(f"  file: {A.DB_FILE} ({A.DB_FILE.stat().st_size / 1e6:.1f} MB)")


def _gh(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def publish() -> None:
    gz = A.DB_FILE.parent / A.ASSET
    with open(A.DB_FILE, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    if _gh("release", "view", A.RELEASE).returncode != 0:
        _gh("release", "create", A.RELEASE, "--title", "Deals page history",
            "--notes", "Bulk/block deals, announcements and insider trades, ~3 years (scripts/disclosures_archive.py).")
    r = _gh("release", "upload", A.RELEASE, str(gz), "--clobber")
    print(f"published {gz.name} ({gz.stat().st_size / 1e6:.1f} MB)" if r.returncode == 0 else f"publish failed: {r.stderr[-300:]}")
    gz.unlink(missing_ok=True)


def update() -> int:
    """Nightly: the published file plus the feeds' current rows, published again."""
    A.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    d = A.DB_FILE.parent / "dl"
    shutil.rmtree(d, ignore_errors=True)
    if _gh("release", "download", A.RELEASE, "--pattern", A.ASSET, "--dir", str(d)).returncode != 0:
        print("disclosures archive: no published file yet (run the backfill once) - skipping")
        return 0
    with gzip.open(d / A.ASSET, "rb") as src, open(A.DB_FILE, "wb") as dst:
        shutil.copyfileobj(src, dst)
    shutil.rmtree(d, ignore_errors=True)
    con = A.connect(A.DB_FILE)
    added = A.fold_feeds(con)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('updated_at', ?)", (datetime.now().isoformat(timespec="seconds"),))
    con.commit()
    con.close()
    print(f"disclosures archive: folded in {added}")
    report()
    publish()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["backfill", "update", "publish", "report"])
    ap.add_argument("--years", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "backfill":
        backfill(a.years)
    elif a.cmd == "update":
        return update()
    elif a.cmd == "publish":
        publish()
    else:
        report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
