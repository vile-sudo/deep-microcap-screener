"""
Per-account watchlists.

GET    /api/watchlist           {"accounts": true, "codes": [...]} for the logged-in user
PUT    /api/watchlist           {"codes": [...], "merge": false}  replace the list (merge: add to it)
POST   /api/watchlist/{code}    star one company
DELETE /api/watchlist/{code}    unstar one company

Each user sees and changes only their own list. With accounts switched off
(local development) GET answers {"accounts": false} and the dashboard keeps
the watchlist in the browser instead.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import auth
from ..database import get_db
from ..models import WatchItem

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])
CODE = re.compile(r"^[A-Za-z0-9&._-]{1,40}$")
MAX_ITEMS = 1000
MarketQ = Query("in", pattern="^(in|us)$")


class WatchIn(BaseModel):
    codes: list[str]
    merge: bool = False


def _user(request: Request) -> dict:
    user = getattr(request.state, "user", None) or auth.user_for_token(request.cookies.get(auth.COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def _codes(db: Session, user_id: int, market: str) -> list[str]:
    return [w.code for w in db.query(WatchItem)
            .filter(WatchItem.user_id == user_id, WatchItem.market == market)
            .order_by(WatchItem.added_at, WatchItem.id)]


def _valid(code: str) -> str:
    code = (code or "").strip()
    if not CODE.match(code):
        raise HTTPException(status_code=400, detail="Not a company code")
    return code


@router.get("")
def get_watchlist(request: Request, market: str = MarketQ, db: Session = Depends(get_db)):
    if not auth.accounts_enabled():
        return {"accounts": False, "codes": []}
    return {"accounts": True, "codes": _codes(db, _user(request)["id"], market.upper())}


@router.put("")
def put_watchlist(body: WatchIn, request: Request, market: str = MarketQ, db: Session = Depends(get_db)):
    user = _user(request)
    mkt = market.upper()
    wanted = list(dict.fromkeys(_valid(c) for c in body.codes))
    if len(wanted) > MAX_ITEMS:
        raise HTTPException(status_code=400, detail=f"A watchlist holds at most {MAX_ITEMS} companies")
    have = set(_codes(db, user["id"], mkt))
    if not body.merge:
        drop = have - set(wanted)
        if drop:
            db.query(WatchItem).filter(WatchItem.user_id == user["id"], WatchItem.market == mkt,
                                        WatchItem.code.in_(drop)).delete(synchronize_session=False)
    now = auth.utcnow()
    for code in wanted:
        if code not in have:
            db.add(WatchItem(user_id=user["id"], market=mkt, code=code, added_at=now))
    db.commit()
    codes = _codes(db, user["id"], mkt)
    if len(codes) > MAX_ITEMS:
        raise HTTPException(status_code=400, detail=f"A watchlist holds at most {MAX_ITEMS} companies")
    return {"accounts": True, "codes": codes}


@router.post("/{code}")
def star(code: str, request: Request, market: str = MarketQ, db: Session = Depends(get_db)):
    user, code, mkt = _user(request), _valid(code), market.upper()
    exists = db.query(WatchItem).filter(WatchItem.user_id == user["id"], WatchItem.market == mkt, WatchItem.code == code).first()
    if not exists:
        if db.query(WatchItem).filter(WatchItem.user_id == user["id"], WatchItem.market == mkt).count() >= MAX_ITEMS:
            raise HTTPException(status_code=400, detail=f"A watchlist holds at most {MAX_ITEMS} companies")
        db.add(WatchItem(user_id=user["id"], market=mkt, code=code, added_at=auth.utcnow()))
        try:
            db.commit()
        except Exception:  # noqa: BLE001 - a double-click raced the unique constraint; the star is there either way
            db.rollback()
    return {"code": code, "starred": True}


@router.delete("/{code}")
def unstar(code: str, request: Request, market: str = MarketQ, db: Session = Depends(get_db)):
    user, code, mkt = _user(request), _valid(code), market.upper()
    db.query(WatchItem).filter(WatchItem.user_id == user["id"], WatchItem.market == mkt, WatchItem.code == code).delete()
    db.commit()
    return {"code": code, "starred": False}
