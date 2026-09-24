"""
Push notification device registration -- mobile/ app only.

POST   /api/push/register     {token, platform}   the app calls this once
                               it has a Firebase token and the user is
                               logged in (see mobile/README.md); upserts,
                               so re-registering on every app open is fine
DELETE /api/push/register     {token}              called on logout, so a
                               shared or resold device stops getting a
                               previous user's notifications

Requires a login the same way watchlist/settings do -- see auth._user()'s
twin here. The plain website never calls either endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import auth
from ..database import get_db
from ..models import DeviceToken

router = APIRouter(prefix="/api/push", tags=["push"])


class RegisterIn(BaseModel):
    token: str
    platform: str   # "android" | "ios"


class UnregisterIn(BaseModel):
    token: str


def _user(request: Request) -> dict:
    user = getattr(request.state, "user", None) or auth.user_for_token(request.cookies.get(auth.COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


@router.post("/register")
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    if body.platform not in ("android", "ios"):
        raise HTTPException(status_code=400, detail="platform must be android or ios")
    if not body.token or len(body.token) > 255:
        raise HTTPException(status_code=400, detail="Bad token")
    user, now = _user(request), auth.utcnow()
    row = db.get(DeviceToken, body.token)
    if row is None:
        db.add(DeviceToken(token=body.token, user_id=user["id"], platform=body.platform,
                            created_at=now, last_seen_at=now))
    else:
        row.user_id, row.platform, row.last_seen_at = user["id"], body.platform, now
    db.commit()
    return {"status": "registered"}


@router.delete("/register")
def unregister(body: UnregisterIn, request: Request, db: Session = Depends(get_db)):
    _user(request)   # a login is required, but any logged-in user may remove a token from their own device
    db.query(DeviceToken).filter(DeviceToken.token == body.token).delete()
    db.commit()
    return {"status": "removed"}
