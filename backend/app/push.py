"""
Push notifications to the mobile/ app (Android + iOS, via Capacitor's
push-notifications plugin) through Firebase Cloud Messaging -- the one
push backend that reaches both platforms with one call. The plain website
has no equivalent; only an installed copy of the app ever receives these.

Device tokens are registered by routers/push.py and stored in
DeviceToken (models.py). Configured from FIREBASE_CREDENTIALS_JSON (see
config.py) -- unset, every function here silently no-ops, so a server
with no Firebase project configured behaves exactly as it did before this
module existed.

Callers: app/news_channel.py's refresh loop (main.py's _news_refresher) and app/news_sync.py's merge
(a deploy handing the server stories the GitHub workflow fetched) both send "N new stories" when
something actually gets added, and app/us_alerts.py (main.py's _us_alerts_loop) sends each user the new
US alerts -- earnings, insider buys, material 8-Ks, price signals -- on the companies on their US
watchlist.
"""
from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy.orm import Session

from .config import get_settings
from .models import DeviceToken

log = logging.getLogger("deepsweep.push")

_app = None            # firebase_admin.App, lazily created; None means "not configured" or "failed to init"
_init_tried = False


def _firebase_app():
    """Lazy singleton: importing firebase_admin and parsing the service
    account JSON on every call would be wasteful, and importing it at
    module load would make firebase-admin mandatory even for servers that
    never configure push."""
    global _app, _init_tried
    if _init_tried:
        return _app
    _init_tried = True
    creds_json = get_settings().firebase_credentials_json
    if not creds_json:
        return None
    try:
        import json as _json

        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(_json.loads(creds_json))
        _app = firebase_admin.initialize_app(cred)
    except Exception as e:  # noqa: BLE001 - a bad/missing key must never take the server down
        log.error("Firebase init failed -- push notifications disabled: %s", e)
        _app = None
    return _app


def configured() -> bool:
    return _firebase_app() is not None


def send_to_all(db: Session, title: str, body: str, data: dict | None = None) -> int:
    """Sends one notification to every registered device. Returns how many
    tokens it attempted to reach (0 if push isn't configured or nobody has
    registered). Tokens Firebase reports as dead (app uninstalled, etc.)
    are removed from the database so the list doesn't grow stale forever."""
    return _send(db, [t.token for t in db.query(DeviceToken.token)], title, body, data)


def send_to_users(db: Session, user_ids: list[int], title: str, body: str, data: dict | None = None) -> int:
    """Like send_to_all, but only to the devices registered by these users."""
    if not user_ids:
        return 0
    return _send(db, [t.token for t in db.query(DeviceToken.token).filter(DeviceToken.user_id.in_(user_ids))],
                 title, body, data)


def _send(db: Session, tokens: list[str], title: str, body: str, data: dict | None) -> int:
    app = _firebase_app()
    if not app or not tokens:
        return 0

    from firebase_admin import messaging

    dead: list[str] = []
    sent = 0
    for batch in _chunks(tokens, 500):   # FCM's own per-request limit
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            tokens=batch,
        )
        try:
            resp = messaging.send_each_for_multicast(message, app=app)
        except Exception as e:  # noqa: BLE001 - one bad batch must not break the rest, or the caller
            log.warning("push send failed for a batch of %d: %s", len(batch), e)
            continue
        sent += resp.success_count
        for token, result in zip(batch, resp.responses):
            if not result.success and "Unregistered" in type(result.exception).__name__:
                dead.append(token)

    if dead:
        db.query(DeviceToken).filter(DeviceToken.token.in_(dead)).delete(synchronize_session=False)
        db.commit()
    return sent


def _chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
