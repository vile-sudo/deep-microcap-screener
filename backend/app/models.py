"""
SQLAlchemy models.

Design choice: the research records in this dataset are wide and
heterogeneous -- 70+ possible fields, and which ones exist depends on
which of the nine research "screens" (v3 deep, v4 moat, v8-moat, ...) a
company came from. Forcing every field into its own rigid column would
mean a migration every time a new screen adds a new field.

So each company gets:
  - a handful of real, indexed columns for the fields the API/DB actually
    needs to filter or sort on (score, sector, theme, market cap, ...)
  - one JSON column (`data`) holding the *complete* original record,
    which is what the frontend actually renders. This is what lets the
    dashboard show "all companies with the same data" as the source file,
    field-for-field, without the backend needing to know every field name
    in advance.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Company(Base):
    __tablename__ = "companies"

    # market+code is the real identity -- code alone is NOT unique across
    # markets (an Indian NSE/BSE code and a US ticker can collide as bare
    # strings). code itself is left exactly as every India record already
    # has it (no "US:" prefix mangling) so every existing screener.in link,
    # CSV, bookmark and filter keeps working unchanged.
    market: Mapped[str] = mapped_column(String(8), primary_key=True, default="IN")
    code: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, index=True)
    sector: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    theme: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    screen: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    rubric: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    claim_grade: Mapped[str | None] = mapped_column(String, nullable=True)

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, index=True, nullable=True)
    market_cap_cr: Mapped[float | None] = mapped_column(Float, index=True, nullable=True)
    pe: Mapped[float | None] = mapped_column(Float, index=True, nullable=True)
    roce_pct: Mapped[float | None] = mapped_column(Float, index=True, nullable=True)
    roe_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    promoter_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    fii_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    dii_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    num_shareholders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cwip_pct_net_block: Mapped[float | None] = mapped_column(Float, nullable=True)
    guidance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # US-market equivalents -- kept as separate columns rather than reusing
    # the India ones above (which stay null for US rows, and vice versa):
    # nothing ranks a US company against an India one on the same numeric
    # scale, so there's no cross-market FX normalization to do here, and
    # e.g. "insider %" is a different concept from "promoter %", not just a
    # renamed one -- see backend/scripts/us_auto_screen.py.
    market_cap_usd: Mapped[float | None] = mapped_column(Float, index=True, nullable=True)
    insider_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    inst_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    capex_overhang: Mapped[bool] = mapped_column(Boolean, default=False)
    capex_heavy: Mapped[bool] = mapped_column(Boolean, default=False)
    guidance_over15: Mapped[bool] = mapped_column(Boolean, default=False)
    guidance_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    pat_turnaround: Mapped[bool] = mapped_column(Boolean, default=False)
    has_lens_data: Mapped[bool] = mapped_column(Boolean, default=False)

    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    added_on: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    # The complete original record -- every field the research produced,
    # verbatim. This is the single source of truth returned to the frontend.
    data: Mapped[dict] = mapped_column(JSON)


class MetaKV(Base):
    """Small key/value store for board-wide metadata: BUILD (version +
    changelog), SCREENS (badge labels), SHORT (theme abbreviations),
    BUILD_STAMP (freshness), CANDIDATES (new-listings queue), BUILD_NEW.
    """

    __tablename__ = "meta_kv"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class User(Base):
    """A dashboard account. Sign-ups start as "pending" and cannot log in
    until an admin approves them (status "approved")."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|approved|rejected|disabled
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuthSession(Base):
    """A logged-in browser. Only the SHA-256 of the cookie token is stored,
    so a copy of the database cannot be used to sign in."""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class WatchItem(Base):
    """One starred company in one user's watchlist. Private to that user and
    the same on every device they log in from."""

    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("user_id", "market", "code", name="uq_watch_user_market_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    market: Mapped[str] = mapped_column(String(8), default="IN")
    code: Mapped[str] = mapped_column(String(40))
    added_at: Mapped[datetime] = mapped_column(DateTime)


class UserSettings(Base):
    """Per-account preferences (dark mode, ...) and when the user last opened Updates."""

    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    prefs: Mapped[dict] = mapped_column(JSON, default=dict)
    updates_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SavedFilter(Base):
    """A named dashboard view a user saved: the dashboard's own URL state (view,
    themes, toggles, sliders, search, sort), re-applied when they pick it."""

    __tablename__ = "saved_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    market: Mapped[str] = mapped_column(String(8), default="IN")
    name: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class Feedback(Base):
    """A "Write to us" message. Kept even if the account is later deleted."""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    category: Mapped[str] = mapped_column(String(40), default="feedback")
    message: Mapped[str] = mapped_column(String(5000))
    page: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(12), default="new", index=True)   # new | read | done
    created_at: Mapped[datetime] = mapped_column(DateTime)


class DeviceToken(Base):
    """One installed copy of the mobile app (mobile/, the Capacitor
    Android/iOS wrapper around this same dashboard) registered for push
    notifications -- see app/push.py for what gets sent and
    routers/push.py for how a token lands here. The plain website's own
    desktop-notification opt-in is a separate mechanism: see
    WebPushSubscription below.

    A token is unique to one device/app-install, not to one user: logging
    out and back in on the same phone reuses the row and just swaps
    user_id (see routers/push.py's upsert), so a shared device only ever
    holds one live registration."""

    __tablename__ = "device_tokens"

    token: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(8))          # android | ios
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)


class WebPushSubscription(Base):
    """One browser that turned on "Desktop notifications" in the India board's Alerts settings (the plain
    website, not the mobile app -- see DeviceToken above for that). Standard Web Push: the browser's own
    push service (Chrome -> FCM, Firefox -> Mozilla's, ...) holds the actual delivery endpoint; this row is
    just what app/webpush.py needs to address it (`endpoint` + the two keys the browser generated) and,
    optionally, whose account asked for it -- accounts are optional on this site, so user_id may be null.

    Keyed by the endpoint URL itself (unique per browser+origin subscription), not by the user: the same
    person logged in on two computers gets two rows, each notified independently, and a subscription
    survives a login/logout on the same browser."""

    __tablename__ = "web_push_subscriptions"

    # Text, not String(n): push-service endpoint URLs vary a lot in length across browsers
    # and are opaque to us, so there is no safe fixed cap to pick -- SQLite and Postgres
    # both index a TEXT primary key exactly as they would a VARCHAR one.
    endpoint: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)

