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
from sqlalchemy import Boolean, Float, Integer, JSON, String
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
