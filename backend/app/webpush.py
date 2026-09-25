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
deal clusters -- and sends one summary push per new session), `_refresh_news_and_push` (one push per
News Channel refresh that actually added stories), and news_sync.merge (the same, for stories that
arrived via the GitHub-workflow fetch instead); this module only knows how to deliver a message once
given one.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from .config import get_settings
from .models import WebPushSubscription

log = logging.getLogger("deepsweep.webpush")


def configured() -> bool:
    return get_settings().vapid_configured


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
