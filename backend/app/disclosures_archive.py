"""Deals page history: every bulk/block deal, non-routine announcement and insider trade for ~3 years.

The nightly feeds (data/deals, data/announcements, data/insider_trades latest.json) only keep a rolling
window (45-120 days) -- small enough for the page to load whole and filter in the browser. Older disclosures
live here instead: one SQLite file with a table per kind, queried page by page with the same filters the
page offers (date range, search, type, side/category, board companies only).

Where it comes from:
  - scripts/disclosures_archive.py backfill   seeds ~3 years straight from NSE (a one-off; deals by month from
                                               NSE's historical report, announcements day by day, insider
                                               trades from NSE's date-range PIT report)
  - scripts/disclosures_archive.py update     the nightly step in daily.yml: pulls the published file, adds
                                               whatever the three latest.json feeds have, publishes it again
  - published as a GitHub release asset (release "disclosures", disclosures.sqlite.gz) -- too big for git

The server (archive_path()) downloads that asset into its data volume the first time it's needed and again
whenever the release has a newer copy (checked at most every CHECK_SECONDS), then also folds in the current
latest.json files, so a query always reaches up to the last nightly/evening feed.
"""
from __future__ import annotations

import gzip
import html
import json
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA = BACKEND_DIR / "data"
DB_FILE = DATA / "disclosures" / "archive.sqlite"
FEEDS = {"deals": DATA / "deals" / "latest.json", "ann": DATA / "announcements" / "latest.json",
         "ins": DATA / "insider_trades" / "latest.json"}
REPO = os.environ.get("GITHUB_REPO", "vile-sudo/deep-microcap-screener")
RELEASE = os.environ.get("DISCLOSURES_RELEASE", "disclosures")
ASSET = "disclosures.sqlite.gz"
CHECK_SECONDS = 3 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (k TEXT PRIMARY KEY, date TEXT, symbol TEXT, name TEXT, kind TEXT, client TEXT,
  side TEXT, quantity REAL, price REAL, value_cr REAL);
CREATE TABLE IF NOT EXISTS ann (k TEXT PRIMARY KEY, date TEXT, filed_at TEXT, symbol TEXT, name TEXT, category TEXT,
  kind TEXT, weight REAL, summary TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS ins (k TEXT PRIMARY KEY, date TEXT, symbol TEXT, name TEXT, person TEXT, category TEXT,
  transaction_type TEXT, side TEXT, quantity REAL, value_cr REAL, mode TEXT, pre_qty REAL, pre_pct REAL,
  post_qty REAL, post_pct REAL, sec_type TEXT, broadcast_at TEXT, regulation TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS orders (k TEXT PRIMARY KEY, symbol TEXT, date TEXT, order_cr REAL, months REAL, not_order INTEGER);
CREATE INDEX IF NOT EXISTS orders_sym ON orders(symbol, date);
CREATE INDEX IF NOT EXISTS deals_date ON deals(date);
CREATE INDEX IF NOT EXISTS deals_sym ON deals(symbol);
CREATE INDEX IF NOT EXISTS ann_date ON ann(date);
CREATE INDEX IF NOT EXISTS ann_sym ON ann(symbol);
CREATE INDEX IF NOT EXISTS ann_filed ON ann(filed_at);
CREATE INDEX IF NOT EXISTS ann_kind ON ann(kind, filed_at);
CREATE INDEX IF NOT EXISTS ins_date ON ins(date);
CREATE INDEX IF NOT EXISTS ins_sym ON ins(symbol);
"""
COLS = {
    "deals": ["k", "date", "symbol", "name", "kind", "client", "side", "quantity", "price", "value_cr"],
    "ann": ["k", "date", "filed_at", "symbol", "name", "category", "kind", "weight", "summary", "url"],
    "ins": ["k", "date", "symbol", "name", "person", "category", "transaction_type", "side", "quantity", "value_cr",
            "mode", "pre_qty", "pre_pct", "post_qty", "post_pct", "sec_type", "broadcast_at", "regulation"],
}


# ---------------------------------------------------------------- rows
def deal_row(r: dict) -> dict:
    k = "|".join(str(r.get(x) or "") for x in ("date", "symbol", "kind", "client", "side", "quantity", "price"))
    return {"k": k, "date": r["date"], "symbol": r["symbol"], "name": r.get("name") or r["symbol"], "kind": r.get("kind"),
            "client": r.get("client"), "side": r.get("side"), "quantity": r.get("quantity"), "price": r.get("price"),
            "value_cr": r.get("value_cr")}


def ann_row(r: dict) -> dict:
    return {"k": str(r["seq_id"]), "date": r["date"], "filed_at": r.get("filed_at"), "symbol": r["symbol"],
            "name": r.get("name") or r["symbol"], "category": r.get("category"), "kind": r.get("kind"),
            "weight": r.get("weight"), "summary": html.unescape(r.get("summary") or ""), "url": r.get("url")}


def ins_row(r: dict) -> dict:
    # one transaction can reach us from both NSE reports (the older date-range report and the XBRL feed): key on
    # what the trade is, not on either report's own id
    try:
        qty = str(int(float(r.get("quantity") or 0)))
    except (TypeError, ValueError):
        qty = str(r.get("quantity"))
    k = "|".join([str(r.get("date") or ""), str(r.get("symbol") or ""), " ".join(str(r.get("person") or "").split()),
                  str(r.get("transaction_type") or ""), qty])
    out = {c: r.get(c) for c in COLS["ins"] if c != "k"}
    out.update({"k": k.lower(), "name": r.get("name") or r["symbol"]})
    return out


ROW = {"deals": deal_row, "ann": ann_row, "ins": ins_row}


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=30)
    con.executescript(SCHEMA)
    return con


def upsert(con: sqlite3.Connection, kind: str, rows: list[dict]) -> int:
    cols = COLS[kind]
    data = [tuple(r.get(c) for c in cols) for r in (ROW[kind](x) for x in rows)]
    con.executemany(f"INSERT OR REPLACE INTO {kind} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", data)
    return len(data)


def fold_feeds(con: sqlite3.Connection) -> dict:
    """Add the three latest.json feeds (whatever they currently hold) to the archive."""
    added = {}
    for kind, path in FEEDS.items():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = d.get({"deals": "deals", "ann": "announcements", "ins": "trades"}[kind]) or []
        if kind == "ann":
            rows = [r for r in rows if r.get("kind") != "routine filing" and r.get("seq_id")]
            # order values read from the filing PDFs (scripts/run_announcements.py) -- kept apart from the
            # filing rows so re-folding a filing never loses them (Results Preview's order-inflow input)
            con.executemany("INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?)",
                            [(str(r["seq_id"]), r["symbol"], r["date"], r.get("order_cr"), r.get("order_months"),
                              1 if r.get("not_order") else 0) for r in rows if r.get("order_checked")])
        added[kind] = upsert(con, kind, rows)
    con.commit()
    return added


# ---------------------------------------------------------------- the server's copy
_lock = threading.Lock()
_state = {"checked": 0.0, "feeds": None, "error": None}


def _release_asset() -> dict | None:
    r = requests.get(f"https://api.github.com/repos/{REPO}/releases/tags/{RELEASE}", timeout=20,
                     headers={"Accept": "application/vnd.github+json"})
    if r.status_code != 200:
        return None
    return next((a for a in r.json().get("assets") or [] if a.get("name") == ASSET), None)


def _download(asset: dict) -> None:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_gz, tmp_db = DB_FILE.with_suffix(".gz.part"), DB_FILE.with_suffix(".part")
    with requests.get(asset["browser_download_url"], stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(tmp_gz, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    with gzip.open(tmp_gz, "rb") as src, open(tmp_db, "wb") as dst:
        shutil.copyfileobj(src, dst)
    tmp_gz.unlink(missing_ok=True)
    con = sqlite3.connect(str(tmp_db))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('asset_updated_at', ?)", (asset.get("updated_at"),))
    con.commit()
    con.close()
    os.replace(tmp_db, DB_FILE)


def _download_once(asset: dict) -> None:
    """Gunicorn runs several workers: one downloads, the others keep using the copy they have (or wait for it)."""
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
        _state["checked"] = time.time() - CHECK_SECONDS + 60   # look again in a minute
        return
    try:
        _download(asset)
    finally:
        lock.unlink(missing_ok=True)


def archive_path() -> Path | None:
    """The server's archive, fetched/refreshed from the release when due, with the current feeds folded in."""
    with _lock:
        now = time.time()
        if now - _state["checked"] > CHECK_SECONDS or not DB_FILE.exists():
            _state["checked"] = now
            try:
                asset = _release_asset()
                have = None
                if DB_FILE.exists():
                    c = sqlite3.connect(str(DB_FILE))
                    try:
                        row = c.execute("SELECT value FROM meta WHERE key='asset_updated_at'").fetchone()
                        have = row[0] if row else None
                    except sqlite3.Error:
                        have = None
                    c.close()
                if asset and asset.get("updated_at") != have:
                    _download_once(asset)
                    _state["feeds"] = None
                _state["error"] = None
            except (requests.RequestException, OSError, sqlite3.Error, EOFError) as e:
                _state["error"] = str(e)[:200]
        if not DB_FILE.exists():
            return None
        stamp = tuple(p.stat().st_mtime if p.exists() else 0 for p in FEEDS.values())
        if stamp != _state["feeds"]:
            con = connect(DB_FILE)
            try:
                fold_feeds(con)
            finally:
                con.close()
            _state["feeds"] = stamp
        return DB_FILE


# ---------------------------------------------------------------- queries
SEARCH = {"deals": ("symbol", "name", "client"), "ann": ("symbol", "name", "summary"), "ins": ("symbol", "name", "person")}
SORTS = {"deals": {"value": "value_cr DESC", "date": "date DESC, value_cr DESC"},
         "ann": {"date": "filed_at DESC", "weight": "weight DESC, filed_at DESC"},
         "ins": {"date": "date DESC, value_cr DESC", "value": "value_cr DESC"}}


def query(kind: str, frm: str, to: str, *, q: str = "", filters: dict | None = None, board: list[str] | None = None,
          sort: str = "date", offset: int = 0, limit: int = 200) -> dict:
    path = archive_path()
    if path is None:
        return {"available": False, "error": _state["error"], "total": 0, "rows": []}
    where, args = ["date >= ?", "date <= ?"], [frm, to]
    for col, val in (filters or {}).items():
        if val:
            where.append(f"{col} = ?")
            args.append(val)
    if q:                                            # LIKE is case-insensitive for plain letters in SQLite
        cols_q = SEARCH[kind]
        where.append("(" + " OR ".join(f"{c} LIKE ?" for c in cols_q) + ")")
        args += [f"%{q}%"] * len(cols_q)
    if board is not None:
        if not board:
            return {"available": True, "total": 0, "rows": []}
        where.append(f"symbol IN ({','.join('?' * len(board))})")
        args += board
    w = " AND ".join(where)
    order = SORTS[kind].get(sort) or next(iter(SORTS[kind].values()))
    cols = [c for c in COLS[kind] if c != "k"]
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    try:
        total = con.execute(f"SELECT COUNT(*) FROM {kind} WHERE {w}", args).fetchone()[0]
        cur = con.execute(f"SELECT {','.join(cols)} FROM {kind} WHERE {w} ORDER BY {order} LIMIT ? OFFSET ?",
                          args + [limit, offset])
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        span = con.execute(f"SELECT MIN(date), MAX(date) FROM {kind}").fetchone()
        extra = {}
        if kind == "ins":
            extra["types"] = [r[0] for r in con.execute("SELECT transaction_type, COUNT(*) n FROM ins GROUP BY 1 ORDER BY n DESC LIMIT 12") if r[0] and r[0].strip("- ")]
            extra["categories"] = [r[0] for r in con.execute("SELECT category, COUNT(*) n FROM ins GROUP BY 1 ORDER BY n DESC LIMIT 16") if r[0] and r[0].strip("- ")]
    finally:
        con.close()
    return {"available": True, "total": total, "rows": rows, "first": span[0], "last": span[1], **extra}
