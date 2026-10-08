"""Web Push: desktop/browser notifications for the plain website (India board), so a visitor who turns
on "Desktop notifications" in the Alerts settings panel sees a new alert as an OS notification even when
Deep Sweep is not the open tab -- no app to install, standard Web Push (Service Worker + Push API), the
same mechanism Gmail/Twitter use. Separate from app/push.py, which is Firebase Cloud Messaging for the
mobile/ Android/iOS app; this one needs no Firebase project, only the VAPID keypair in config.py.

Subscriptions are registered by routers/push_web.py and stored in WebPushSubscription (models.py).
Configured from VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT (see config.py's own comment for how
to generate them) -- unset, every function here silently no-ops, so a server with no VAPID keys behaves
exactly as it did before this module existed, and the settings-panel toggle explains that it isn't set up.

Callers: main.py's `_desktop_push_loop` (watches for new India alerts -- highs/lows, breakouts,
institutional deal/insider clusters), `_refresh_news_and_push` (News Channel refreshes that actually
added stories), news_sync.merge (the same, for stories that arrived via the GitHub-workflow fetch
instead), and us_alerts._notify_new (US board alerts, per user -- see send_to_user below, the one thing
India's broadcast-style send_to_all does not need); this module only knows how to deliver a message once
given one, and (news_target/alert_target/us_alert_target) how to shape it -- a single new item deep-links
straight to itself, several fall back to a summary that opens the relevant view.

Two boards, one subscription list (WebPushSubscription), one browser can want either or both --
notify_india/notify_us on each row say which; send_to_all/send_to_user filter on whichever this call is
for, so turning off one board's toggle never silently affects the other."""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from .config import get_settings
from .models import WebPushSubscription

log = logging.getLogger("deepsweep.webpush")


def configured() -> bool:
    return get_settings().vapid_configured


def news_target(new_items: list[dict]) -> tuple[str, str, str, str]:
    """(title, body, url, tag) for a batch of newly-arrived News Channel stories. Exactly one new story
    deep-links straight to it -- its own headline as the notification title, its own article link as the
    target, so a click takes the reader directly to what changed, not to a list they still have to
    search. More than one has no single "the" story to point at, so it falls back to a summary that
    opens the News Channel view, same as before this existed."""
    if len(new_items) == 1:
        it = new_items[0]
        link = it.get("link") or "/#v=news"
        return (it.get("title") or "News Channel", it.get("source") or "Tap to read", link,
                "news-" + str(abs(hash(link)) % 10**8))
    n = len(new_items)
    return "News Channel", f"{n} new stor{'y' if n == 1 else 'ies'} just in", "/#v=news", "news-channel"


_ALERT_LABELS = {"high": "New high", "low": "New low", "ipo_breakout": "IPO base breakout",
                 "vcp_breakout": "Base breakout", "deal_buy": "Buying cluster", "deal_sell": "Selling cluster",
                 "insider_buy": "Insider buying", "insider_sell": "Insider selling"}


def alert_target(new_items: list[dict]) -> tuple[str, str, str, str]:
    """(title, body, url, tag) for a batch of newly-arrived Alerts items. Exactly one new item deep-links
    straight to it: a deal/insider cluster opens the Deals page's matching tab pre-filtered to that
    symbol, anything else opens that company's own scorecard drawer (board companies only -- a
    market-scope high/low/breakout has no drawer to open, so it falls back to the Alerts panel). More
    than one has no single item to point at, so it just opens the Alerts panel (still better than the
    plain overview link this used to be -- a click always lands somewhere useful now)."""
    if len(new_items) == 1:
        it = new_items[0]
        label = _ALERT_LABELS.get(it["type"], "Alert")
        name = it.get("name") or it.get("symbol") or it.get("code") or "a stock"
        title = f"{label}: {name}"
        if it["type"] in ("deal_buy", "deal_sell"):
            return title, f"{it.get('deals', 0)} disclosed deals", f"/#v=overview&al=deals:{it.get('symbol', '')}", "alert-" + it["type"]
        if it["type"] in ("insider_buy", "insider_sell"):
            return title, f"{it.get('insiders', 0)} insiders", f"/#v=overview&al=insiders:{it.get('symbol', '')}", "alert-" + it["type"]
        if it.get("code"):
            return title, "Open its scorecard", f"/#v=overview&al=stock:{it['code']}", "alert-" + it["type"]
        return title, "Open the Alerts panel", "/#v=overview&al=modal", "alert-" + it["type"]
    return "Deep Sweep alerts", f"{len(new_items)} new alerts", "/#v=overview&al=modal", "deep-sweep-alerts"


def us_alert_target(new_items: list[dict]) -> tuple[str, str, str, str]:
    """(title, body, url, tag) for a batch of newly-arrived US board alerts (see us_alerts.py's
    compute_alerts -- earnings/insider buy/insider cluster/8-K/price signal). Unlike the India board,
    every alert kind here already opens the same thing when clicked in-app (that company's own drawer,
    see us.js's alertsPanel), so there is only one kind of deep-link target, not several: exactly one new
    item (or several that are all the SAME company) opens that company's drawer; different companies at
    once has no single one to point at, so it opens the Alerts panel instead."""
    codes = {it.get("code") for it in new_items if it.get("code")}
    if len(codes) == 1:
        code = codes.pop()
        it = new_items[0]
        if len(new_items) == 1:
            return it.get("title") or "US board alert", f"{it.get('kind', 'Alert')} · {it.get('sub', '')}", f"/us#al=stock:{code}", "us-alert-" + code
        return f"{len(new_items)} new alerts on {code}", it.get("title") or "", f"/us#al=stock:{code}", "us-alert-" + code
    return "US board alerts", f"{len(new_items)} new alerts", "/us#al=modal", "us-alerts"


def send_to_all(db: Session, title: str, body: str, url: str = "/", tag: str | None = None, board: str = "in") -> int:
    """Sends one notification to every browser subscribed for `board` ("in" or "us" -- notify_india/
    notify_us on WebPushSubscription). Returns how many it attempted to reach (0 if web push isn't
    configured or nobody has subscribed for that board). See _send() for delivery/cleanup."""
    if not configured():
        return 0
    col = WebPushSubscription.notify_india if board == "in" else WebPushSubscription.notify_us
    subs = db.query(WebPushSubscription).filter(col.is_(True)).all()
    return _send(db, subs, title, body, url, tag)


def send_to_user(db: Session, user_id: int, title: str, body: str, url: str = "/", tag: str | None = None) -> int:
    """Sends to just this one user's US-board-subscribed browsers (there may be more than one -- two
    computers, say). US alerts are per-user-watchlist-scoped (us_alerts.py), unlike India's board-wide
    broadcast, so this is the one place a single user_id needs targeting rather than every subscriber."""
    if not configured():
        return 0
    subs = db.query(WebPushSubscription).filter(WebPushSubscription.user_id == user_id,
                                                 WebPushSubscription.notify_us.is_(True)).all()
    return _send(db, subs, title, body, url, tag)


def send_to_user_browsers(db: Session, user_id: int, title: str, body: str, url: str = "/", tag: str | None = None) -> int:
    """Sends to every browser this user has enabled push in, whichever board's flag it carries (or
    none -- a browser enabled only for filing alerts has neither). For per-user subscriptions such as
    filing categories (app/filing_alerts.py), where the user's own choices decide what is sent."""
    if not configured():
        return 0
    subs = db.query(WebPushSubscription).filter(WebPushSubscription.user_id == user_id).all()
    return _send(db, subs, title, body, url, tag)


def _send(db: Session, subs: list[WebPushSubscription], title: str, body: str, url: str, tag: str | None) -> int:
    if not subs:
        return 0

    from pywebpush import WebPushException, webpush

    settings = get_settings()
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag or "deep-sweep-alert"})
    dead: list[str] = []
    sent = 0
    for sub in subs:
        subscription_info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
        try:
            webpush(subscription_info=subscription_info, data=payload, vapid_private_key=settings.vapid_private_key,
                    vapid_claims={"sub": settings.vapid_subject}, timeout=10)
            sent += 1
        except WebPushException as e:
            if e.status_code in (404, 410):
                dead.append(sub.endpoint)
            else:
                log.warning("web push failed for one subscription (status %s): %s", e.status_code, e)
        except Exception as e:  # noqa: BLE001 - one bad subscription must never break the rest
            log.warning("web push failed for one subscription: %s", e)

    if dead:
        db.query(WebPushSubscription).filter(WebPushSubscription.endpoint.in_(dead)).delete(synchronize_session=False)
        db.commit()
    return sent
