"""Web Push subscription endpoints -- the plain website's "Desktop notifications" toggle (India board
Alerts settings). See app/webpush.py for what gets sent and why this is a separate mechanism from the
mobile app's Firebase push (routers/push.py).

GET    /api/webpush/public-key   the VAPID public key the browser needs to create a subscription (empty
                                  string, 200 OK, when this deployment has no VAPID keys configured yet --
                                  the frontend uses that to explain the feature isn't set up rather than
                                  failing oddly)
POST   /api/webpush/subscribe    {subscription: <PushSubscription.toJSON()>}  upserts by endpoint, so
                                  re-subscribing (e.g. the browser rotated keys) is fine
DELETE /api/webpush/subscribe    {endpoint}                                   called when the visitor turns
                                  the toggle back off

No login required -- accounts are optional on this site (see auth.accounts_enabled()), and a desktop
notification is a property of the BROWSER, not an identity; a logged-in user is recorded on the row when
one is available, for a future per-user filtered digest, but subscribing never requires signing in.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import auth
from ..config import get_settings
from ..database import get_db
from ..models import WebPushSubscription

router = APIRouter(prefix="/api/webpush", tags=["webpush"])
MAX_LEN = 4000    # a real endpoint/key is far shorter; this only guards against a malformed request body


class Keys(BaseModel):
    p256dh: str
    auth: str


class SubscriptionIn(BaseModel):
    endpoint: str
    keys: Keys


class SubscribeIn(BaseModel):
    subscription: SubscriptionIn


class UnsubscribeIn(BaseModel):
    endpoint: str


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
    if row is None:
        db.add(WebPushSubscription(endpoint=sub.endpoint, user_id=user_id, p256dh=sub.keys.p256dh, auth=sub.keys.auth,
                                    created_at=now, last_seen_at=now))
    else:
        row.p256dh, row.auth, row.last_seen_at = sub.keys.p256dh, sub.keys.auth, now
        if user_id:
            row.user_id = user_id
    db.commit()
    return {"status": "subscribed"}


@router.delete("/subscribe")
def unsubscribe(body: UnsubscribeIn, db: Session = Depends(get_db)):
    db.query(WebPushSubscription).filter(WebPushSubscription.endpoint == body.endpoint).delete()
    db.commit()
    return {"status": "removed"}
