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

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Company(Base):
    __tablename__ = "companies"

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
    __table_args__ = (UniqueConstraint("user_id", "code", name="uq_watch_user_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    added_at: Mapped[datetime] = mapped_column(DateTime)
