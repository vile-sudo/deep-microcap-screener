"""The PEAD engine's SQLite (india_pead_screener -> data/pead_engine/pead.sqlite), read by the calculators.

Built nightly by scripts/run_pead_engine.py and published as a GitHub release asset (it changes every night
and would bloat the repo); the server fetches it when the release is newer, at most every 3 hours -- the
same arrangement as the Deals history (app/disclosures_archive.py).
"""
from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
DB_FILE = BACKEND_DIR / "data" / "pead_engine" / "pead.sqlite"
REPO = os.environ.get("GITHUB_REPO", "vile-sudo/deep-microcap-screener")
RELEASE = os.environ.get("PEAD_RELEASE", "pead-engine")
ASSET = "pead.sqlite.gz"
CHECK_SECONDS = 3 * 3600
_lock = threading.Lock()
_state = {"checked": 0.0, "stamp": None}


def _refresh() -> None:
    r = requests.get(f"https://api.github.com/repos/{REPO}/releases/tags/{RELEASE}", timeout=20,
                     headers={"Accept": "application/vnd.github+json"})
    if r.status_code != 200:
        return
    a = next((x for x in r.json().get("assets") or [] if x.get("name") == ASSET), None)
    if not a or a.get("updated_at") == _state["stamp"]:
        return
    stamp_file = DB_FILE.with_suffix(".stamp")
    if DB_FILE.exists() and stamp_file.exists() and stamp_file.read_text() == a["updated_at"]:
        _state["stamp"] = a["updated_at"]
        return
    lock = DB_FILE.with_suffix(".lock")
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - lock.stat().st_mtime > 900:
            lock.unlink()
    except OSError:
        pass
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return                                   # another worker is downloading it
    try:
        gz, tmp = DB_FILE.with_suffix(".gz.part"), DB_FILE.with_suffix(".part")
        with requests.get(a["browser_download_url"], stream=True, timeout=300) as resp:
            resp.raise_for_status()
            with open(gz, "wb") as f:
                for chunk in resp.iter_content(1 << 20):
                    f.write(chunk)
        with gzip.open(gz, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        gz.unlink(missing_ok=True)
        os.replace(tmp, DB_FILE)
        stamp_file.write_text(a["updated_at"])
        _state["stamp"] = a["updated_at"]
    finally:
        lock.unlink(missing_ok=True)


def path() -> Path | None:
    with _lock:
        if time.time() - _state["checked"] > CHECK_SECONDS or not DB_FILE.exists():
            _state["checked"] = time.time()
            try:
                _refresh()
            except (requests.RequestException, OSError, EOFError):
                pass
    return DB_FILE if DB_FILE.exists() else None


def _con():
    p = path()
    if not p:
        return None
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _cuts(con, days: int = 150) -> dict:
    """Typical Q5 entry levels lately: the median 80th percentile of SUE and EAR across recent groups."""
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = con.execute("SELECT sue_q5, ear_q5 FROM cutoffs WHERE last_date >= ? ORDER BY last_date", (since,)).fetchall()
    if not rows:
        rows = con.execute("SELECT sue_q5, ear_q5 FROM cutoffs ORDER BY last_date DESC LIMIT 12").fetchall()
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    return {"sue": med([r[0] for r in rows if r[0] is not None]), "ear": med([r[1] for r in rows if r[1] is not None]),
            "groups": len(rows), "days": days}


def calc(q: str) -> dict:
    """Everything the two calculators need for one company (or the matches when q is ambiguous)."""
    con = _con()
    if con is None:
        return {"error": "The PEAD engine hasn't published its database yet."}
    try:
        t = q.strip().upper()
        hit = con.execute("SELECT symbol, name, sector FROM names WHERE symbol = ?", (t,)).fetchone()
        if not hit:
            words = q.strip().lower().split()
            ms = [dict(r) for r in con.execute("SELECT symbol, name, sector FROM names").fetchall()
                  if all(w in f"{r['symbol']} {r['name'] or ''}".lower() for w in words)]
            if len(ms) != 1:
                return {"matches": ms[:12]}
            hit = ms[0]
        sym = hit["symbol"]
        hist = [dict(r) for r in con.execute(
            "SELECT * FROM scores WHERE symbol = ? ORDER BY quarter_end_date DESC LIMIT 12", (sym,)).fetchall()]
        fc = con.execute("SELECT * FROM forecasts WHERE symbol = ?", (sym,)).fetchone()
        grp = None
        if hist and hist[0].get("quintile_group"):
            grp = con.execute("SELECT * FROM cutoffs WHERE quintile_group = ?", (hist[0]["quintile_group"],)).fetchone()
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        run = con.execute("SELECT config FROM runs ORDER BY run_at DESC LIMIT 1").fetchone()
        return {"symbol": sym, "name": hit["name"], "sector": hit["sector"], "updated_at": meta.get("updated_at"),
                "forecast": dict(fc) if fc else None, "history": hist, "group": dict(grp) if grp else None,
                "cuts": _cuts(con), "config": run[0] if run else None}
    finally:
        con.close()


def q5(days: int = 120) -> dict:
    """Recent SUE Q5 x EAR Q5 intersections."""
    con = _con()
    if con is None:
        return {"rows": []}
    try:
        since = (date.today() - timedelta(days=days)).isoformat()
        rows = [dict(r) for r in con.execute(
            "SELECT s.symbol, n.name, n.sector, s.quarter_end_date, s.announcement_date, s.announcement_date_used, s.actual, s.expected, "
            "s.sue, s.ear, s.stock_ret3, s.bench_ret3, s.sue_pct, s.ear_pct, s.quintile_group, s.car21, s.car61 "
            "FROM scores s LEFT JOIN names n USING(symbol) WHERE s.qualifies = 1 AND s.announcement_date_used >= ? "
            "ORDER BY s.announcement_date_used DESC, s.sue DESC", (since,)).fetchall()]
        allq = con.execute("SELECT COUNT(*), AVG(car21), AVG(car61), SUM(car61 > 0), COUNT(car61) FROM scores WHERE qualifies = 1").fetchone()
        base = con.execute("SELECT AVG(car61), SUM(car61 > 0), COUNT(car61) FROM scores WHERE sue_q IS NOT NULL").fetchone()
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
        return {"updated_at": meta.get("updated_at"), "rows": rows, "days": days,
                "stats": {"q5": allq[0], "q5_car21": allq[1], "q5_car61": allq[2], "q5_beat61": (allq[3] / allq[4]) if allq[4] else None,
                          "q5_n61": allq[4], "all_car61": base[0], "all_beat61": (base[1] / base[2]) if base[2] else None, "all_n61": base[2]}}
    finally:
        con.close()
