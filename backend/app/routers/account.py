"""
Everything in the account menu that belongs to one person.

GET    /api/me/settings                 {prefs}                       e.g. {"dark": true}
PATCH  /api/me/settings                 {prefs: {...}}                merged into the saved prefs
PATCH  /api/me/profile                  {name}
POST   /api/me/password                 {current, new}                other devices are logged out

GET    /api/me/filters                  saved dashboard views
POST   /api/me/filters                  {name, state}
PATCH  /api/me/filters/{id}             {name?, state?}
DELETE /api/me/filters/{id}

GET    /api/me/updates                  what's new, newest first, with an unread count
POST   /api/me/updates/seen

POST   /api/feedback                    {category, message, page}     "Write to us"
GET    /api/admin/feedback              every message, newest first  (admin)
POST   /api/admin/feedback/{id}/{read|done|new}                        (admin)
DELETE /api/admin/feedback/{id}                                        (admin)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .. import auth
from ..config import BASE_DIR
from ..database import get_db
from ..models import AuthSession, Company, Feedback, SavedFilter, User, UserSettings, WatchItem
from .auth import require_admin
from .reports import REPORTS

router = APIRouter(tags=["account"])
UPDATES_FILE = BASE_DIR / "data" / "updates.json"
UPDATE_DAYS = 45
MAX_FILTERS = 50
CATEGORIES = {"feedback": "Feedback", "data-error": "Data error", "feature": "Feature request", "question": "Question", "other": "Other"}


def _user(request: Request) -> dict:
    user = getattr(request.state, "user", None) or auth.user_for_token(request.cookies.get(auth.COOKIE))
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def _settings(db: Session, user_id: int) -> UserSettings:
    row = db.get(UserSettings, user_id)
    if row is None:
        row = UserSettings(user_id=user_id, prefs={})
        db.add(row)
        db.flush()
    return row


# ---------------------------------------------------------------- settings + profile
class SettingsIn(BaseModel):
    prefs: dict


@router.get("/api/me/settings")
def get_settings_(request: Request, db: Session = Depends(get_db)):
    row = db.get(UserSettings, _user(request)["id"])
    return {"prefs": (row.prefs if row else None) or {}}


@router.patch("/api/me/settings")
def patch_settings(body: SettingsIn, request: Request, db: Session = Depends(get_db)):
    user = _user(request)
    row = _settings(db, user["id"])
    prefs = dict(row.prefs or {})
    for k, v in body.prefs.items():
        if k == "dark":
            prefs[k] = bool(v)
        elif k == "alerts_seen" and isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            prefs[k] = v
        elif k == "us_alerts_seen" and isinstance(v, list):
            prefs[k] = [str(x)[:120] for x in v if isinstance(x, (str, int))][-800:]
        elif k in ("us_alerts_wl", "us_alerts_notify"):
            prefs[k] = bool(v)
        elif k == "alerts" and isinstance(v, dict):
            windows = ["1D", "1W", "1M", "6M", "1Y"]
            prefs[k] = {"highs": [w for w in windows if w in (v.get("highs") or [])],
                        "lows": [w for w in windows if w in (v.get("lows") or [])],
                        "ipo": bool(v.get("ipo", True)), "vcp": bool(v.get("vcp", True)),
                        "scope": v.get("scope") if v.get("scope") in ("watchlist", "board", "market") else "board",
                        "notify": bool(v.get("notify", False))}
    row.prefs = prefs
    db.commit()
    return {"prefs": prefs}


class ProfileIn(BaseModel):
    name: str


@router.patch("/api/me/profile")
def patch_profile(body: ProfileIn, request: Request, db: Session = Depends(get_db)):
    user = db.get(User, _user(request)["id"])
    name = re.sub(r"\s+", " ", body.name or "").strip()[:120]
    if not name:
        raise HTTPException(status_code=400, detail="Enter a name")
    user.name = name
    db.commit()
    auth._cache_clear()   # the menu reads the name from the session cache
    return {"user": auth.public_user(user)}


class PasswordIn(BaseModel):
    current: str
    new: str


@router.post("/api/me/password")
def change_password(body: PasswordIn, request: Request, db: Session = Depends(get_db)):
    me = _user(request)
    if auth.rate_limited(f"password:{me['id']}", 8, 900):
        raise HTTPException(status_code=429, detail="Too many attempts - try again in a few minutes")
    owner = (auth.get_settings().admin_login or ("",))[0]
    user = db.get(User, me["id"])
    if owner and user.email.lower() == owner.lower():
        raise HTTPException(status_code=400, detail="The main admin's password is set in the server settings (Render environment), not here")
    if not auth.verify_password(body.current or "", user.password_hash):
        raise HTTPException(status_code=400, detail="Your current password is not right")
    if len(body.new or "") < 8:
        raise HTTPException(status_code=400, detail="Use a new password of at least 8 characters")
    if body.new == body.current:
        raise HTTPException(status_code=400, detail="The new password is the same as the current one")
    user.password_hash = auth.hash_password(body.new)
    keep = auth._digest(request.cookies.get(auth.COOKIE) or "")
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id, AuthSession.token_hash != keep))
    db.commit()
    auth._cache_clear()
    return {"ok": True, "message": "Password changed. Any other devices have been logged out."}


# ---------------------------------------------------------------- saved filters
class FilterIn(BaseModel):
    name: str | None = None
    state: str | None = None
    market: str | None = None      # "IN" (default, the India board) or "US" -- each board keeps its own saved views


def _clean_state(state: str) -> str:
    state = (state or "").lstrip("#").strip()
    if len(state) > 2000 or not re.match(r"^[A-Za-z0-9%&=,.:_~+\-]*$", state):
        raise HTTPException(status_code=400, detail="That view can't be saved")
    return "&".join(part for part in state.split("&") if part and not part.startswith("w="))


def _filter_out(f: SavedFilter) -> dict:
    return {"id": f.id, "name": f.name, "state": f.state, "created_at": f.created_at.isoformat() + "Z", "updated_at": f.updated_at.isoformat() + "Z"}


@router.get("/api/me/filters")
def list_filters(request: Request, market: str = Query("in", pattern="^(in|us)$"), db: Session = Depends(get_db)):
    uid = _user(request)["id"]
    rows = (db.query(SavedFilter).filter(SavedFilter.user_id == uid, SavedFilter.market == market.upper())
            .order_by(SavedFilter.updated_at.desc()).all())
    return {"filters": [_filter_out(f) for f in rows]}


@router.post("/api/me/filters")
def create_filter(body: FilterIn, request: Request, db: Session = Depends(get_db)):
    uid = _user(request)["id"]
    name = re.sub(r"\s+", " ", body.name or "").strip()[:80]
    if not name:
        raise HTTPException(status_code=400, detail="Give the filter a name")
    state = _clean_state(body.state or "")
    if not state:
        raise HTTPException(status_code=400, detail="Nothing to save yet - pick some filters first")
    market = (body.market or "IN").upper()
    if market not in ("IN", "US"):
        raise HTTPException(status_code=400, detail="Unknown market")
    if db.query(SavedFilter).filter(SavedFilter.user_id == uid, SavedFilter.market == market).count() >= MAX_FILTERS:
        raise HTTPException(status_code=400, detail=f"You can keep up to {MAX_FILTERS} saved filters - delete one first")
    now = auth.utcnow()
    f = SavedFilter(user_id=uid, market=market, name=name, state=state, created_at=now, updated_at=now)
    db.add(f)
    db.commit()
    return _filter_out(f)


def _own_filter(db: Session, uid: int, fid: int) -> SavedFilter:
    f = db.get(SavedFilter, fid)
    if not f or f.user_id != uid:
        raise HTTPException(status_code=404, detail="No such saved filter")
    return f


@router.patch("/api/me/filters/{fid}")
def update_filter(fid: int, body: FilterIn, request: Request, db: Session = Depends(get_db)):
    f = _own_filter(db, _user(request)["id"], fid)
    if body.name is not None:
        name = re.sub(r"\s+", " ", body.name).strip()[:80]
        if not name:
            raise HTTPException(status_code=400, detail="Give the filter a name")
        f.name = name
    if body.state is not None:
        f.state = _clean_state(body.state)
    f.updated_at = auth.utcnow()
    db.commit()
    return _filter_out(f)


@router.delete("/api/me/filters/{fid}")
def delete_filter(fid: int, request: Request, db: Session = Depends(get_db)):
    f = _own_filter(db, _user(request)["id"], fid)
    db.delete(f)
    db.commit()
    return {"deleted": fid}


# ---------------------------------------------------------------- updates
def _updates(db: Session, watch: set[str]) -> list[dict]:
    """Features from data/updates.json, companies added to the board and new
    deep-dive reports, from the last UPDATE_DAYS days. Items that touch the
    user's watchlist are flagged."""
    since = (auth.utcnow() - timedelta(days=UPDATE_DAYS)).date().isoformat()
    items = []
    try:
        for u in json.loads(UPDATES_FILE.read_text(encoding="utf-8")):
            if u.get("date", "") >= since:
                items.append({**u, "id": f"feature:{u['date']}:{u['title']}"})
    except (OSError, ValueError):
        pass
    by_day: dict[str, list[Company]] = {}
    for c in db.query(Company).filter(Company.added_on >= since):
        by_day.setdefault(c.added_on, []).append(c)
    for day, cos in by_day.items():
        cos.sort(key=lambda c: -(c.final_score or 0))
        auto = sum(1 for c in cos if c.screen == "auto")
        names = ", ".join(c.name for c in cos[:6]) + (f" and {len(cos) - 6} more" if len(cos) > 6 else "")
        items.append({"id": f"companies:{day}", "date": day, "kind": "companies",
                      "title": f"{len(cos)} {'company' if len(cos) == 1 else 'companies'} added to the board" + (f" ({auto} auto-added)" if auto else ""),
                      "body": names, "new_since": day, "codes": [c.code for c in cos],
                      "watch": sorted({c.code for c in cos} & watch)})
    try:
        index = json.loads((REPORTS / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    for code, r in index.items():
        day = (r.get("generated_at") or "")[:10]
        if day >= since:
            items.append({"id": f"report:{code}:{r.get('period')}", "date": day, "kind": "report",
                          "title": f"Deep-dive report: {r.get('name')} ({r.get('period_label')})",
                          "body": r.get("one_line") or "", "code": code, "watch": [code] if code in watch else []})
    items.sort(key=lambda i: (i["date"], i["kind"] != "feature"), reverse=True)
    return items[:60]


@router.get("/api/me/updates")
def get_updates(request: Request, db: Session = Depends(get_db)):
    uid = _user(request)["id"]
    row = db.get(UserSettings, uid)
    seen = row.updates_seen_at.date().isoformat() if row and row.updates_seen_at else ""
    watch = {w.code for w in db.query(WatchItem).filter(WatchItem.user_id == uid)}
    items = _updates(db, watch)
    for i in items:
        i["unread"] = i["date"] > seen if seen else True
    return {"items": items, "unread": sum(1 for i in items if i["unread"]), "seen": seen or None}


@router.post("/api/me/updates/seen")
def mark_updates_seen(request: Request, db: Session = Depends(get_db)):
    row = _settings(db, _user(request)["id"])
    row.updates_seen_at = auth.utcnow()
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- write to us
class FeedbackIn(BaseModel):
    category: str = "feedback"
    message: str
    page: str = ""


@router.post("/api/feedback")
def send_feedback(body: FeedbackIn, request: Request, db: Session = Depends(get_db)):
    me = _user(request)
    if auth.rate_limited(f"feedback:{me['id']}", 10, 3600):
        raise HTTPException(status_code=429, detail="That's a lot of messages - please try again later")
    message = (body.message or "").strip()
    if len(message) < 5:
        raise HTTPException(status_code=400, detail="Write a little more so we can help")
    if len(message) > 5000:
        raise HTTPException(status_code=400, detail="Please keep it under 5,000 characters")
    page = (body.page or "")[:500]
    if page and not page.startswith("/"):
        page = ""
    db.add(Feedback(user_id=me["id"], name=me.get("name") or "", email=me.get("email") or "",
                    category=body.category if body.category in CATEGORIES else "other",
                    message=message, page=page, status="new", created_at=auth.utcnow()))
    db.commit()
    return {"ok": True, "message": "Thanks - your message has reached the team."}


@router.get("/api/admin/feedback")
def list_feedback(admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(Feedback).order_by(Feedback.created_at.desc()).limit(500).all()
    return {"new": sum(1 for f in rows if f.status == "new"), "categories": CATEGORIES,
            "messages": [{"id": f.id, "name": f.name, "email": f.email, "category": f.category, "message": f.message,
                          "page": f.page, "status": f.status, "created_at": f.created_at.isoformat() + "Z"} for f in rows]}


@router.post("/api/admin/feedback/{fid}/{status}")
def set_feedback_status(fid: int, status: str, admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    if status not in ("new", "read", "done"):
        raise HTTPException(status_code=404, detail="Unknown status")
    f = db.get(Feedback, fid)
    if not f:
        raise HTTPException(status_code=404, detail="No such message")
    f.status = status
    db.commit()
    return {"id": fid, "status": status}


@router.delete("/api/admin/feedback/{fid}")
def delete_feedback(fid: int, admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    f = db.get(Feedback, fid)
    if not f:
        raise HTTPException(status_code=404, detail="No such message")
    db.delete(f)
    db.commit()
    return {"deleted": fid}
