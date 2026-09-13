"""
Log in / sign up, and admin approval of new accounts.

POST /api/auth/signup                 {name, email, password} -> account "pending"
POST /api/auth/login                  {email, password}       -> session cookie
POST /api/auth/logout
GET  /api/auth/me                     the logged-in user (401 if none)

GET  /api/admin/users                 every account, pending first    (admin)
POST /api/admin/users/{id}/{action}   approve | reject | disable | enable | make-admin | remove-admin   (admin)
DELETE /api/admin/users/{id}                                            (admin)
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from .. import auth
from ..database import get_db
from ..models import User

router = APIRouter(tags=["auth"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupIn(BaseModel):
    name: str = ""
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


def _no_accounts():
    raise HTTPException(status_code=404, detail="Accounts are not enabled on this server")


@router.post("/api/auth/signup")
def signup(body: SignupIn, request: Request, db: Session = Depends(get_db)):
    if not auth.accounts_enabled():
        _no_accounts()
    if auth.rate_limited(f"signup:{auth.client_ip(request)}", 5, 3600):
        raise HTTPException(status_code=429, detail="Too many sign-up attempts. Try again in an hour.")
    email = body.email.strip().lower()
    name = body.name.strip()[:120]
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Use a password of at least 8 characters.")
    if len(body.password) > 200:
        raise HTTPException(status_code=400, detail="That password is too long.")
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        # the same answer whether or not the address exists, so sign-up can't
        # be used to find out who has an account
        return {"status": "pending", "message": "Thanks for signing up. You'll be able to log in as soon as the admin approves your account."}
    db.add(User(email=email, name=name, password_hash=auth.hash_password(body.password), status="pending",
                is_admin=False, created_at=auth.utcnow()))
    db.commit()
    return {"status": "pending", "message": "Thanks for signing up. You'll be able to log in as soon as the admin approves your account."}


@router.post("/api/auth/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    if not auth.accounts_enabled():
        _no_accounts()
    ip = auth.client_ip(request)
    ident = body.email.strip().lower()
    if auth.rate_limited(f"login:{ip}", 20, 900) or auth.rate_limited(f"login:{ip}:{ident}", 8, 900):
        raise HTTPException(status_code=429, detail="Too many attempts. Wait a few minutes and try again.")
    user = db.execute(select(User).where(User.email == ident)).scalar_one_or_none()
    # verify even when the user is missing, so response time doesn't reveal it
    ok = auth.verify_password(body.password, user.password_hash if user else auth.hash_password("x" * 12))
    if user is None or not ok:
        raise HTTPException(status_code=401, detail="That email and password don't match.")
    if user.status == "pending":
        raise HTTPException(status_code=403, detail="Your account is waiting for approval. You'll be able to log in once an admin approves it.")
    if user.status != "approved":
        raise HTTPException(status_code=403, detail="This account doesn't have access. Contact the admin.")
    token = auth.create_session(db, user)
    resp = JSONResponse({"user": auth.public_user(user)})
    auth.set_cookie(resp, request, token)
    return resp


@router.post("/api/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    auth.end_session(db, request.cookies.get(auth.COOKIE))
    resp = JSONResponse({"ok": True})
    auth.clear_cookie(resp)
    return resp


@router.get("/api/auth/me")
def me(request: Request):
    if not auth.accounts_enabled():
        return {"user": None, "accounts": False}
    user = auth.user_for_token(request.cookies.get(auth.COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="Login required")
    return {"user": user, "accounts": True}


# ---------------------------------------------------------------- admin
def require_admin(request: Request) -> dict:
    user = getattr(request.state, "user", None) or auth.user_for_token(request.cookies.get(auth.COOKIE))
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admins only")
    return user


@router.get("/api/admin/users")
def list_users(admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    order = case((User.status == "pending", 0), (User.status == "approved", 1), else_=2)
    users = db.execute(select(User).order_by(order, User.created_at.desc())).scalars().all()
    owner = (auth.get_settings().admin_login or ("",))[0]
    return {"users": [{**auth.public_user(u), "is_owner": u.email == owner} for u in users],
            "pending": sum(1 for u in users if u.status == "pending")}


ACTIONS = {
    "approve": {"status": "approved"},
    "reject": {"status": "rejected"},
    "disable": {"status": "disabled"},
    "enable": {"status": "approved"},
    "make-admin": {"is_admin": True},
    "remove-admin": {"is_admin": False},
}


def _target(db: Session, user_id: int, admin: dict) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such user")
    login = auth.get_settings().admin_login
    if login and user.email == login[0]:
        raise HTTPException(status_code=400, detail="The main admin account is managed from the server settings.")
    return user


@router.post("/api/admin/users/{user_id}/{action}")
def act_on_user(user_id: int, action: str, admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    if action not in ACTIONS:
        raise HTTPException(status_code=400, detail="Unknown action")
    user = _target(db, user_id, admin)
    for k, v in ACTIONS[action].items():
        setattr(user, k, v)
    if action == "approve" and not user.approved_at:
        user.approved_at = auth.utcnow()
    db.commit()
    if user.status != "approved" or action == "remove-admin":
        auth.end_all_sessions(db, user.id)    # losing access takes effect immediately
    return {"user": auth.public_user(user)}


@router.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(require_admin), db: Session = Depends(get_db)):
    user = _target(db, user_id, admin)
    auth.end_all_sessions(db, user.id)
    db.delete(user)
    db.commit()
    return {"deleted": user_id}
