"""
Loads the research dataset (data/companies_raw.json + data/meta_raw.json)
into the database. Safe to re-run: it upserts every row, so re-seeding
after you refresh the source JSON (see scripts/refresh_data.py) just
updates existing companies and inserts any new ones -- nothing is
duplicated.

Usage:
    python -m app.seed
"""
import hashlib
import json
import logging
import sys
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import Company, MetaKV

# Known data-integrity gap in the source export: 20 companies carry
# screen="v8-weekly", a code the original board's SCREENS legend never
# defined (so opening their detail card, or exporting them to CSV, would
# throw in the original file). We patch the legend here rather than drop
# the companies, so every row on the board renders correctly.
SCREEN_PATCHES = {
    "auto": {
        "lab": "Auto-added",
        "cls": "b-auto",
        "full": "Auto-added by the daily 3 AM screen: passes the board's gates and the company states a moat "
                "(import substitution, market leadership, sole maker...). Numbers from screener.in; not yet researched by hand.",
    },
    "v8-weekly": {
        "lab": "v8 weekly",
        "cls": "b-v8",
        "full": "v8 · weekly refresh sweep, added 23 Aug 2026 — not yet folded into the methodology write-up",
    },
}


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _pretty_date(iso: str) -> str:
    """2026-09-01 -> "1 September 2026", the format the board header uses."""
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except (TypeError, ValueError):
        return iso
    return f"{d.day} {d:%B %Y}"


# Fields the frontend always treats as arrays (e.g. `(d.warnings||[]).filter(...)`).
# A handful of records in the source export (35, all carrying a single item) have
# these as a bare string instead of a one-element list, which crashes that code
# in the browser. Normalize on the way in rather than special-casing it in JS.
LIST_FIELDS = ("warnings", "gate_failures", "penalty_detail", "pat_periods", "qualifies_as")


def _normalize_record(rec: dict) -> dict:
    for key in LIST_FIELDS:
        val = rec.get(key)
        if isinstance(val, str):
            rec[key] = [val] if val.strip() else []
    return rec


def seed() -> None:
    settings = get_settings()
    Base.metadata.create_all(bind=engine)

    companies = _load_json(settings.seed_file)
    meta = _load_json(settings.meta_file)

    # The weekly discovery pipeline (automation/, runs on your machine) writes
    # these two small files independently of the 375-company dataset. If
    # they exist, they win over whatever meta_raw.json shipped with -- that's
    # what lets a candidates-only push update the live queue without a full
    # re-export of companies_raw.json.
    if settings.candidates_file.exists():
        meta["CANDIDATES"] = _load_json(settings.candidates_file)
    if settings.build_stamp_file.exists():
        meta["BUILD_STAMP"] = _load_json(settings.build_stamp_file)

    # The header's "Updated <date>" used to be a string hand-typed into
    # meta_raw.json, which nothing in the weekly pipeline touches -- so it sat
    # at "20 August 2026" while the board underneath it kept moving. It is not
    # a judgement call anyway: the board is updated on the day companies are
    # added to it. Derive both it and BUILD_NEW (which drives the NEW badge and
    # the "new in this build" filter) from the newest added_on in the dataset,
    # so a push that adds companies moves the date, and one that doesn't leaves
    # it alone -- and the header, the badge and the filter can never disagree.
    newest_added = max((c.get("added_on") or "" for c in companies), default="")
    if newest_added:
        meta["BUILD_NEW"] = newest_added
        build = dict(meta.get("BUILD") or {})
        build["built"] = _pretty_date(newest_added)
        meta["BUILD"] = build

    # Patch any screen codes present in the data but missing from the legend,
    # so the frontend never hits an undefined lookup.
    screens = dict(meta.get("SCREENS") or {})
    seen_screens = {c.get("screen") for c in companies if c.get("screen")}
    for code in seen_screens - screens.keys():
        screens[code] = SCREEN_PATCHES.get(
            code, {"lab": code, "cls": "b-user", "full": code}
        )
    meta["SCREENS"] = screens

    db = SessionLocal()
    try:
        inserted = 0
        updated = 0
        for rec in companies:
            code = rec.get("code")
            if not code:
                continue
            rec = _normalize_record(rec)
            existing = db.get(Company, code)
            fields = dict(
                code=code,
                name=rec.get("name", ""),
                sector=rec.get("sector"),
                theme=rec.get("theme"),
                screen=rec.get("screen"),
                rubric=rec.get("rubric"),
                claim_grade=rec.get("claim_grade"),
                score=rec.get("score"),
                final_score=rec.get("final_score"),
                market_cap_cr=rec.get("market_cap_cr"),
                pe=rec.get("pe"),
                roce_pct=rec.get("roce_pct"),
                roe_pct=rec.get("roe_pct"),
                promoter_pct=rec.get("promoter_pct"),
                fii_pct=rec.get("fii_pct"),
                dii_pct=rec.get("dii_pct"),
                num_shareholders=rec.get("num_shareholders"),
                cwip_pct_net_block=rec.get("cwip_pct_net_block"),
                guidance_pct=rec.get("guidance_pct"),
                capex_overhang=bool(rec.get("capex_overhang")),
                capex_heavy=bool(rec.get("capex_heavy")),
                guidance_over15=bool(rec.get("guidance_over15")),
                guidance_flag=bool(rec.get("guidance_flag")),
                pat_turnaround=bool(rec.get("pat_turnaround")),
                has_lens_data=bool(rec.get("has_lens_data")),
                rank=rec.get("rank"),
                tier=rec.get("tier"),
                added_on=rec.get("added_on"),
                data=rec,
            )
            if existing:
                for k, v in fields.items():
                    setattr(existing, k, v)
                updated += 1
            else:
                db.add(Company(**fields))
                inserted += 1

        for key in ("BUILD", "SCREENS", "SHORT", "BUILD_STAMP", "CANDIDATES", "BUILD_NEW"):
            if key not in meta:
                continue
            row = db.get(MetaKV, key)
            if row:
                row.value = meta[key]
            else:
                db.add(MetaKV(key=key, value=meta[key]))

        row = db.get(MetaKV, "SEED_HASH")
        if row:
            row.value = data_hash()
        else:
            db.add(MetaKV(key="SEED_HASH", value=data_hash()))
        db.commit()
        print(f"Seed complete: {inserted} inserted, {updated} updated, "
              f"{len(companies)} total companies.")
    finally:
        db.close()


def data_hash() -> str:
    """Fingerprint of every file the seed reads, so a deploy only re-seeds when the data changed."""
    settings = get_settings()
    h = hashlib.sha256()
    for path in (settings.seed_file, settings.meta_file, settings.candidates_file, settings.build_stamp_file):
        try:
            h.update(path.read_bytes())
        except OSError:
            h.update(b"-")
    return h.hexdigest()


def seed_if_changed() -> None:
    """Run at app startup: load the bundled data unless this exact data is
    already in the database. With Postgres this is how a deploy gets its data
    (Render's build step may not reach a private database). Several workers
    start together, so on Postgres they queue on an advisory lock and the
    later ones find the work done."""
    log = logging.getLogger("deepsweep")
    want = data_hash()

    def current_hash(conn):
        try:
            v = conn.execute(text("SELECT value FROM meta_kv WHERE key = 'SEED_HASH'")).scalar()
        except Exception:  # noqa: BLE001 - table not there yet
            conn.rollback()
            return None
        return v.strip('"') if isinstance(v, str) else v

    try:
        if engine.dialect.name != "postgresql":
            with engine.connect() as conn:
                done = current_hash(conn) == want
            if not done:        # SQLite: the connection above is closed first, so the seed can write
                seed()
                log.warning("seeded the database at startup (data changed)")
            return
        with engine.connect() as conn:
            conn.execute(text("SELECT pg_advisory_lock(804211)"))
            conn.commit()
            try:
                if current_hash(conn) != want:
                    conn.commit()
                    seed()
                    log.warning("seeded the database at startup (data changed)")
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(804211)"))
                conn.commit()
    except Exception as e:  # noqa: BLE001 - never stop the app from starting; the previous data stays
        log.warning("startup seed skipped: %s", e)

if __name__ == "__main__":
    try:
        sys.exit(seed() or 0)
    except OperationalError as e:
        # e.g. a Docker / Render build that can't reach a private Postgres: the app seeds itself at startup
        print(f"Database not reachable now ({str(e).splitlines()[0][:120]}); the app will load the data when it starts.")
        sys.exit(0)
