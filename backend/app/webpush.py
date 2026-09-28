"""Web Push: desktop/browser notifications for the plain website (India board), so a visitor who turns
on "Desktop notifications" in the Alerts settings panel sees a new alert as an OS notification even when
Deep Sweep is not the open tab -- no app to install, standard Web Push (Service Worker + Push API), the
same mechanism Gmail/Twitter use. Separate from app/push.py, which is Firebase Cloud Messaging for the
mobile/ Android/iOS app; this one needs no Firebase project, only the VAPID keypair in config.py.

Subscriptions are registered by routers/push_web.py and stored in WebPushSubscription (models.py).
Configured from VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT (see config.py's own comment for how
to generate them) -- unset, every function here silently no-ops, so a server with no VAPID keys behaves
exactly as it did before this module existed, and the settings-panel toggle explains that it isn't set up.

Callers: main.py's `_desktop_push_loop` (watches for new alerts -- highs/lows, breakouts, institutional
deal/insider clusters) and `_refresh_news_and_push` (News Channel refreshes that actually added stories),
plus news_sync.merge (the same, for stories that arrived via the GitHub-workflow fetch instead); this
module only knows how to deliver a message once given one, and (news_target/alert_target) how to shape
it -- a single new item deep-links straight to itself, several fall back to a summary that opens the
relevant view."""
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


def send_to_all(db: Session, title: str, body: str, url: str = "/", tag: str | None = None) -> int:
    """Sends one notification to every subscribed browser. Returns how many it attempted to reach (0 if
    web push isn't configured or nobody has subscribed). A subscription the push service reports as gone
    (404/410 -- the browser unsubscribed, cleared site data, or the endpoint expired) is removed so the
    list doesn't grow stale forever."""
    if not configured():
        return 0
    subs = db.query(WebPushSubscription).all()
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
