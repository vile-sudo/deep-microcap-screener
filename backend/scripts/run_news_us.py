"""Recent news for the US board's companies.

    cd backend
    python scripts/run_news_us.py

Pulls each board company's headlines from Finnhub's free /company-news endpoint
(the same FINNHUB_API_KEY the auto-screen and earnings jobs use) and keeps a rolling
two weeks in data/news_us/latest.json, served at GET /api/news-us. The US board's
News tab, the scorecard's "Recent news" section and the alert bell read it.

Runs every three hours from .github/workflows/news_us.yml. One call per company
(free tier: 60/min). A company whose call fails keeps the headlines already on file;
a run without FINNHUB_API_KEY, or one where nothing came back, leaves the file alone.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW_US = BACKEND_DIR / "data" / "companies_us_raw.json"
OUT_FILE = BACKEND_DIR / "data" / "news_us" / "latest.json"

FINNHUB = "https://finnhub.io/api/v1"
DELAY = 1.1
LOOKBACK_DAYS = 3          # each run asks for the last few days; older items are already on file
RETENTION_DAYS = 14
PER_COMPANY = 12           # newest headlines kept per company
MAX_CONSECUTIVE_ERRORS = 6
SUMMARY_CHARS = 320


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def fetch_company(session: requests.Session, key: str, code: str, name: str, now: datetime) -> list[dict]:
    frm = (now - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    r = session.get(f"{FINNHUB}/company-news", params={"symbol": code, "from": frm, "to": now.date().isoformat(), "token": key}, timeout=30)
    if r.status_code == 429:
        time.sleep(30)
        r = session.get(f"{FINNHUB}/company-news", params={"symbol": code, "from": frm, "to": now.date().isoformat(), "token": key}, timeout=30)
    r.raise_for_status()
    rows = r.json()
    if not isinstance(rows, list):
        raise ValueError("unexpected response")
    out = []
    for x in rows:
        if not isinstance(x, dict) or not x.get("headline") or not x.get("url") or not x.get("datetime"):
            continue
        out.append({
            "id": f"{code}:{x.get('id') or x['url']}", "symbol": code, "name": name,
            "headline": _clip(x["headline"], 220), "summary": _clip(x.get("summary"), SUMMARY_CHARS),
            "source": x.get("source") or "", "url": x["url"],
            "datetime": datetime.fromtimestamp(int(x["datetime"]), timezone.utc).isoformat(timespec="seconds"),
            "image": x.get("image") or "",
        })
    return out


def main() -> int:
    key = os.environ.get("FINNHUB_API_KEY", "")
    if not key:
        print("run_news_us: FINNHUB_API_KEY not set; leaving the news file as it is")
        return 0
    records = json.loads(RAW_US.read_text(encoding="utf-8")) if RAW_US.exists() else []
    board = {r["code"]: r.get("name") or r["code"] for r in records if r.get("code")}
    if not board:
        print("run_news_us: no US board companies yet; nothing to fetch")
        return 0
    try:
        existing = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("items") or []
    except (OSError, ValueError):
        existing = []

    now = datetime.now(timezone.utc)
    session = requests.Session()
    fresh, errors, consecutive = [], 0, 0
    for code, name in sorted(board.items()):
        try:
            fresh += fetch_company(session, key, code, name, now)
            consecutive = 0
        except (requests.RequestException, ValueError) as e:
            errors += 1
            consecutive += 1
            print(f"  {code}: {e}")
            if consecutive >= MAX_CONSECUTIVE_ERRORS:
                print("run_news_us: Finnhub keeps failing; stopping and keeping what is on file for the rest")
                break
        time.sleep(DELAY)

    if not fresh and existing:
        print(f"run_news_us: nothing came back ({errors} errors); keeping the existing file")
        return 0

    cutoff = (now - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
    merged = {i["id"]: i for i in existing if i.get("symbol") in board}     # a company taken off the board drops out
    merged.update({i["id"]: i for i in fresh})
    per, kept = {}, []
    for i in sorted(merged.values(), key=lambda i: i["datetime"], reverse=True):
        if i["datetime"] < cutoff:
            continue
        per[i["symbol"]] = per.get(i["symbol"], 0) + 1
        if per[i["symbol"]] <= PER_COMPANY:
            kept.append(i)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({
        "fetched_at": now.isoformat(timespec="seconds"),
        "count": len(kept),
        "companies_checked": len(board),
        "companies_with_news": len({i["symbol"] for i in kept}),
        "items": kept,
    }, separators=(",", ":")), encoding="utf-8")
    added = len({i["id"] for i in fresh} - {i["id"] for i in existing})
    print(f"run_news_us: {len(fresh)} fetched ({added} new), {len(kept)} kept across {len({i['symbol'] for i in kept})} "
          f"of {len(board)} companies, {errors} errors -> {OUT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
