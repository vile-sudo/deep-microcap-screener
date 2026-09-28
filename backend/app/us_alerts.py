"""US-board alerts, computed on the server so they can reach a phone or a desktop browser.

The US page's alert bell (frontend/static/us.js, buildAlerts) works out its list in the
browser from the files the nightly jobs write. This module applies the SAME rules on the
server, and pushes the ones that are new to the users who have that company on their US
watchlist -- so an earnings date, an insider purchase or a material 8-K on a company you
follow reaches you without opening the site, on whichever of a registered phone
(push.py, Firebase) and a subscribed browser (webpush.py, notify_us) you have set up;
either, both, or neither work independently.

Rules (keep in step with buildAlerts in us.js):
  * earnings      the company reports in the next 7 days
  * insider buy   an open-market purchase of $25k or more in the last 14 days
  * insider cluster  two or more insiders on the same side (>= $10k each), buying or
                  selling, within 14 days
  * 8-K filing    a material 8-K (results, order win, M&A, management change,
                  regulatory or legal, fundraise) in the last 7 days
  * price signal  the chart job's feed (new 52-week high/low, breakout) for a board name
  * institutional flow  a new/exited/grown/shrunk 13F position (app/institutions_flow_us.py),
                  shown while its quarter is still the latest one on file (~45 days,
                  13F's own reporting lag) -- much rarer than the others (13F updates
                  quarterly), but real disclosed institutional conviction
  * short interest  reported short position moved 35%+ since the previous FINRA
                  report (bi-monthly), shown for ~20 days after that settlement date
  * analyst sentiment  Finnhub's monthly "bullish share" (strong buy + buy, as a
                  percentage of every rating that month) swung 25+ points since the
                  prior month -- a consensus shift, not a specific analyst's
                  upgrade/downgrade (Finnhub's free tier only gives the aggregate)

Which alerts have already been pushed is remembered in MetaKV (US_ALERTS_NOTIFIED). The
first run only records what exists, so switching this on never floods anyone with the
backlog. Runs every 15 minutes from main.py; reading four small files is cheap.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from . import push, webpush
from .config import BASE_DIR
from .models import Company, DeviceToken, MetaKV, UserSettings, WatchItem, WebPushSubscription

log = logging.getLogger("deepsweep.us_alerts")

STATE_KEY = "US_ALERTS_NOTIFIED"
KEEP_IDS = 3000
MATERIAL_8K = {"results", "order win", "M&A", "management change", "regulatory or legal", "fundraise"}
SHORT_INTEREST_MOVE_PCT = 35.0   # a rise (bearish building) or fall (covering) at least this large,
                                  # calibrated against a real settlement: at 35% roughly the top ~13% of
                                  # the board's own reported moves, not the routine day-to-day noise
ANALYST_SHIFT_PTS = 25.0         # percentage-point swing in the "bullish" share of Finnhub's monthly
                                  # consensus (see _bullish_pct) -- calibrated against real board data:
                                  # this caught the two genuine consensus reversals in a real month
                                  # (both 80+ point swings) and excluded four companies whose count only
                                  # moved by a single analyst (under 12 points)
DATA = BASE_DIR / "data"
SETUPS = BASE_DIR / "chart_data_us" / "setups.json"
LOCK_FILE = DATA / ".us_alerts.lock"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _days_since(iso: str, today: date) -> int:
    return (today - date.fromisoformat(str(iso)[:10])).days


def _bullish_pct(month: dict) -> float | None:
    """(strong buy + buy) as a share of every analyst's rating that month -- Finnhub's free tier gives
    only this monthly aggregate count, not which analyst changed their rating or when, so "consensus
    shifted" is the honest framing here, not "upgraded"/"downgraded" (a specific rating-action claim this
    data cannot support)."""
    n = sum(month.get(k, 0) for k in ("strong_buy", "buy", "hold", "sell", "strong_sell"))
    return (month.get("strong_buy", 0) + month.get("buy", 0)) / n * 100 if n else None


def _compact(v: float) -> str:
    return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${round(v / 1e3)}k" if v >= 1e3 else f"${round(v)}"


def compute_alerts(board: dict[str, str], today: date | None = None) -> list[dict]:
    """board: code -> company name. Returns [{id, code, kind, title, sub}]."""
    today = today or datetime.now(timezone.utc).date()
    out: list[dict] = []

    for code, e in (_load(DATA / "earnings_us" / "latest.json").get("companies") or {}).items():
        n = (e or {}).get("next")
        if code not in board or not n or not n.get("date"):
            continue
        d = -_days_since(n["date"], today)
        if 0 <= d <= 7:
            when = "today" if d == 0 else "tomorrow" if d == 1 else f"in {d} days"
            out.append({"id": f"earn:{code}:{n['date']}", "code": code, "kind": "Earnings",
                        "title": f"{board[code]} reports {when}", "sub": n["date"]})

    txns = _load(DATA / "insider_us" / "latest.json").get("transactions") or []
    for side, verb, kind in (("BUY", "bought", "Insider cluster"), ("SELL", "sold", "Insider selling cluster")):
        by_sym: dict[str, list] = {}
        for t in txns:
            if t.get("symbol") in board and t.get("is_market") and t.get("side") == side and (t.get("value_usd") or 0) >= 10000 \
                    and _days_since(t["trade_date"], today) <= 14:
                by_sym.setdefault(t["symbol"], []).append(t)
        for sym, rows in by_sym.items():                # several insiders on the same side within two weeks
            if len({r.get("insider") for r in rows}) >= 2:
                latest = max(r["trade_date"] for r in rows)
                out.append({"id": f"cluster:{side}:{sym}:{latest}", "code": sym, "kind": kind,
                            "title": f"{len({r.get('insider') for r in rows})} insiders {verb} {_compact(sum(r['value_usd'] for r in rows))} of {sym}",
                            "sub": latest})

    for t in txns:
        if t.get("symbol") in board and t.get("is_market") and t.get("side") == "BUY" and (t.get("value_usd") or 0) >= 25000 \
                and _days_since(t["trade_date"], today) <= 14:
            out.append({"id": f"ins:{t.get('accession')}:{t.get('insider')}:{t.get('shares')}", "code": t["symbol"],
                        "kind": "Insider buy", "title": f"{t.get('insider')} bought {_compact(t['value_usd'])} of {t['symbol']}",
                        "sub": t["trade_date"]})

    for a in _load(DATA / "announcements_us" / "latest.json").get("announcements") or []:
        if a.get("symbol") in board and a.get("category") in MATERIAL_8K and _days_since(a["date"], today) <= 7:
            out.append({"id": f"ann:{a.get('accession')}:{a['symbol']}", "code": a["symbol"], "kind": "8-K filing",
                        "title": f"{a.get('name') or a['symbol']}: {a['category']}", "sub": a["date"]})

    setups = _load(SETUPS)
    for f in setups.get("feed") or []:
        if f.get("code") in board:
            out.append({"id": f"feed:{f['code']}:{f.get('kind')}:{setups.get('as_of')}", "code": f["code"],
                        "kind": "Price signal", "title": f"{f.get('name') or f['code']} {f.get('text', '')}".strip(),
                        "sub": str(setups.get("as_of"))})

    flow_doc = _load(DATA / "institutions_us" / "flow.json")
    if flow_doc.get("fetched_at") and _days_since(flow_doc["fetched_at"], today) <= 45:   # 13F's own reporting lag
        for code, f in (flow_doc.get("companies") or {}).items():
            if code not in board:
                continue
            bits = [f"{len(f[k])} {k}" for k in ("new", "exited", "increased", "decreased") if f.get(k)]
            if not bits:
                continue
            out.append({"id": f"instflow:{code}:{f.get('period')}", "code": code, "kind": "Institutional flow",
                        "title": f"{board[code]}: {' · '.join(bits)} institution{'s' if sum(len(f[k]) for k in ('new','exited','increased','decreased'))!=1 else ''}",
                        "sub": f"13F for {f.get('period') or 'the latest quarter'}, vs {f.get('prev_period') or 'the prior one'}"})

    short_doc = _load(DATA / "short_us" / "latest.json")
    settle = short_doc.get("settlement_date")
    if settle and _days_since(settle, today) <= 20:   # FINRA's own ~twice-a-month cadence, plus a few days' slack
        for code, s in (short_doc.get("companies") or {}).items():
            pct = s.get("change_pct")
            if code in board and pct is not None and abs(pct) >= SHORT_INTEREST_MOVE_PCT:
                direction = "up" if pct > 0 else "down"
                out.append({"id": f"short:{code}:{settle}", "code": code, "kind": "Short interest",
                            "title": f"{board[code]}: short interest {direction} {abs(pct):.0f}% since the last report",
                            "sub": f"settlement {settle}" + (f" · {s['short_pct_shares']:.1f}% of shares outstanding" if s.get("short_pct_shares") is not None else "")})

    for code, e in (_load(DATA / "analysts_us" / "latest.json").get("companies") or {}).items():
        if code not in board or not e.get("covered"):
            continue
        months = e.get("months") or []
        if len(months) < 2 or _days_since(months[0]["period"], today) > 45:   # Finnhub's own monthly cadence
            continue
        cur, prev = _bullish_pct(months[0]), _bullish_pct(months[1])
        if cur is None or prev is None:
            continue
        shift = cur - prev
        if abs(shift) >= ANALYST_SHIFT_PTS:
            direction = "improved" if shift > 0 else "worsened"
            out.append({"id": f"analyst:{code}:{months[0]['period']}", "code": code, "kind": "Analyst sentiment",
                        "title": f"{board[code]}: analyst consensus {direction} ({prev:.0f}% → {cur:.0f}% bullish)",
                        "sub": f"{months[0]['period']} vs {months[1]['period']}"})
    return out


def _take_lock() -> bool:
    """Both gunicorn workers run the loop; only one may check at a time or a new alert would be
    pushed twice. A lock older than five minutes is a crashed holder's and is taken over."""
    try:
        os.close(os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        try:
            if time.time() - LOCK_FILE.stat().st_mtime > 300:
                os.utime(LOCK_FILE)
                return True
        except OSError:
            pass
        return False
    except OSError:
        return False


def notify_new(db: Session) -> int:
    """Push the alerts not sent before. Returns how many notifications went out."""
    if not _take_lock():
        return 0
    try:
        return _notify_new(db)
    finally:
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass


def _notify_new(db: Session) -> int:
    board = {c.code: c.name for c in db.query(Company.code, Company.name).filter(Company.market == "US")}
    alerts = compute_alerts(board)
    row = db.get(MetaKV, STATE_KEY)
    if row is None:                                    # first run: remember what exists, send nothing
        db.add(MetaKV(key=STATE_KEY, value=[a["id"] for a in alerts][-KEEP_IDS:]))
        db.commit()
        return 0
    known = set(row.value or [])
    fresh = [a for a in alerts if a["id"] not in known]
    if not fresh:
        return 0
    row.value = (list(row.value or []) + [a["id"] for a in fresh])[-KEEP_IDS:]
    db.commit()
    if not push.configured() and not webpush.configured():
        return 0

    # who follows what, and through which channel(s) they can actually be reached -- mobile (a
    # registered device, opted in via `us_alerts_notify`) and desktop (a browser subscribed with
    # notify_us, its own independent opt-in/out) are independent channels with independent settings;
    # `us_alerts_notify` is specifically the "push to my phone" checkbox (see us.js's alertsPanel), so it
    # must only gate mobile -- turning that off must not silently also stop a separately-opted-in
    # desktop subscription, and a browser-only visitor with no phone must not be skipped just because
    # push.configured() checks Firebase, and vice versa.
    mobile_off = {s.user_id for s in db.query(UserSettings) if (s.prefs or {}).get("us_alerts_notify") is False}
    mobile_devices = ({uid for (uid,) in db.query(DeviceToken.user_id).distinct()} - mobile_off) if push.configured() else set()
    desktop_users = ({uid for (uid,) in db.query(WebPushSubscription.user_id)
                      .filter(WebPushSubscription.user_id.isnot(None), WebPushSubscription.notify_us.is_(True)).distinct()}
                     if webpush.configured() else set())
    follows: dict[int, set[str]] = {}
    for uid, code in db.query(WatchItem.user_id, WatchItem.code).filter(WatchItem.market == "US"):
        if uid in mobile_devices or uid in desktop_users:
            follows.setdefault(uid, set()).add(code)

    sent = 0
    for uid, codes in follows.items():
        mine = [a for a in fresh if a["code"] in codes]
        if not mine:
            continue
        if uid in mobile_devices:
            title = mine[0]["title"] if len(mine) == 1 else f"{len(mine)} new alerts on your US watchlist"
            body = mine[0]["sub"] if len(mine) == 1 else "; ".join(a["title"] for a in mine[:3]) + ("…" if len(mine) > 3 else "")
            sent += push.send_to_users(db, [uid], title, body, {"type": "us_alert", "code": mine[0]["code"]})
        if uid in desktop_users:
            dtitle, dbody, durl, dtag = webpush.us_alert_target(mine)
            sent += webpush.send_to_user(db, uid, dtitle, dbody, url=durl, tag=dtag)
    log.info("us alerts: %d new, %d notifications sent", len(fresh), sent)
    return sent
