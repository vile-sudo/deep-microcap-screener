"""Web Push subscription endpoints -- the "Desktop notifications" toggle on either board's Alerts UI (India
board's Alerts settings, US board's Alerts panel). See app/webpush.py for what gets sent and why this is a
separate mechanism from the mobile app's Firebase push (routers/push.py).

GET    /api/webpush/public-key   the VAPID public key the browser needs to create a subscription (empty
                                  string, 200 OK, when this deployment has no VAPID keys configured yet --
                                  the frontend uses that to explain the feature isn't set up rather than
                                  failing oddly)
POST   /api/webpush/subscribe    {subscription: <PushSubscription.toJSON()>, board: "in"|"us"}  upserts by
                                  endpoint, so re-subscribing (e.g. the browser rotated keys) is fine.
                                  `board` (default "in", so an old India-only client body still works)
                                  turns ONE of notify_india/notify_us on, never both from a single call
                                  and never turning the other back off -- the same browser can be
                                  subscribed to both boards' alerts independently.
DELETE /api/webpush/subscribe    {endpoint, board: "in"|"us"}   called when the visitor turns that board's
                                  toggle back off. Only clears that board's flag; the row itself is only
                                  deleted once neither board wants it any more, so turning off India alerts
                                  does not silently kill an also-active US subscription on the same browser.

No login required -- accounts are optional on the India board, and a desktop notification is a property of
the BROWSER, not an identity; a logged-in user is recorded on the row when one is available. US alerts are
per-user-watchlist-scoped (see us_alerts.py) so notify_us only ever does anything for a row that has a
user_id -- an anonymous India-only subscriber simply never matches a US send.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import auth
from ..config import get_settings
from ..database import get_db
from ..models import WebPushSubscription

router = APIRouter(prefix="/api/webpush", tags=["webpush"])
MAX_LEN = 4000    # a real endpoint/key is far shorter; this only guards against a malformed request body

Board = Literal["in", "us"]


class Keys(BaseModel):
    p256dh: str
    auth: str


class SubscriptionIn(BaseModel):
    endpoint: str
    keys: Keys


class SubscribeIn(BaseModel):
    subscription: SubscriptionIn
    board: Board = "in"


class UnsubscribeIn(BaseModel):
    endpoint: str
    board: Board = "in"


def _logged_in_user_id(request: Request) -> int | None:
    user = getattr(request.state, "user", None) or (auth.user_for_token(request.cookies.get(auth.COOKIE)) if auth.accounts_enabled() else None)
    return user["id"] if user else None


@router.get("/public-key")
def public_key():
    return {"key": get_settings().vapid_public_key}


@router.post("/subscribe")
def subscribe(body: SubscribeIn, request: Request, db: Session = Depends(get_db)):
    sub = body.subscription
    if not sub.endpoint or len(sub.endpoint) > MAX_LEN or len(sub.keys.p256dh) > MAX_LEN or len(sub.keys.auth) > MAX_LEN:
        raise HTTPException(status_code=400, detail="Malformed subscription")
    now = auth.utcnow()
    row = db.get(WebPushSubscription, sub.endpoint)
    user_id = _logged_in_user_id(request)
    flag = "notify_india" if body.board == "in" else "notify_us"
    if row is None:
        kwargs = {"notify_india": False, "notify_us": False, flag: True}
        db.add(WebPushSubscription(endpoint=sub.endpoint, user_id=user_id, p256dh=sub.keys.p256dh, auth=sub.keys.auth,
                                    created_at=now, last_seen_at=now, **kwargs))
    else:
        row.p256dh, row.auth, row.last_seen_at = sub.keys.p256dh, sub.keys.auth, now
        setattr(row, flag, True)
        if user_id:
            row.user_id = user_id
    db.commit()
    return {"status": "subscribed"}


@router.delete("/subscribe")
def unsubscribe(body: UnsubscribeIn, db: Session = Depends(get_db)):
    row = db.get(WebPushSubscription, body.endpoint)
    if row is not None:
        setattr(row, "notify_india" if body.board == "in" else "notify_us", False)
        if not row.notify_india and not row.notify_us:
            db.delete(row)
        db.commit()
    return {"status": "removed"}
