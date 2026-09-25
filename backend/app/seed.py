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

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import Company, MetaKV, SavedFilter, WatchItem

# Known data-integrity gap in the source export: 20 companies carry
# screen="v8-weekly", a code the original board's SCREENS legend never
# defined (so opening their detail card, or exporting them to CSV, would
# throw in the original file). We patch the legend here rather than drop
# the companies, so every row on the board renders correctly.
SCREEN_PATCHES = {
    "auto": {
        "lab": "Auto-added",
        "cls": "b-auto",
        "full": "Auto-added by the daily 2 AM screen: passes the board's gates and the company states a moat "
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


def _mkey(key: str, market: str) -> str:
    """India keeps the original unprefixed MetaKV key (no forced reseed of
    an existing production row on the day this shipped); other markets get
    a prefixed one. Mirrors routers/companies.py's hidden_key() and
    routers/meta.py's _mkey()."""
    return key if market == "IN" else f"{market}_{key}"


def seed(market: str = "IN") -> None:
    settings = get_settings()
    market = market.upper()
    migrate_schema()
    Base.metadata.create_all(bind=engine)

    seed_file = settings.seed_file if market == "IN" else settings.seed_file_us
    meta_file = settings.meta_file if market == "IN" else settings.meta_file_us
    companies = _load_json(seed_file)
    meta = _load_json(meta_file)
    for c in companies:
        c["market"] = market

    # The weekly discovery pipeline (automation/, runs on your machine) writes
    # these two small files independently of the 375-company dataset. If
    # they exist, they win over whatever meta_raw.json shipped with -- that's
    # what lets a candidates-only push update the live queue without a full
    # re-export of companies_raw.json. India-only for now -- no US discovery
    # pipeline exists yet.
    if market == "IN":
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
            existing = db.get(Company, (market, code))
            fields = dict(
                market=market,
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
                market_cap_usd=rec.get("market_cap_usd"),
                insider_pct=rec.get("insider_pct"),
                inst_pct=rec.get("inst_pct"),
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

        # US only: a company taken out of companies_us_raw.json used to stay
        # on the live board forever, since this loop only adds and updates.
        # Guarded so a bad or empty file can never wipe the board: it only
        # runs when the file listed at least one company, and India's board
        # keeps its long-standing add/update-only behaviour.
        removed = 0
        if market == "US":
            keep = {rec.get("code") for rec in companies if rec.get("code")}
            if keep:
                stale = [c.code for c in db.query(Company).filter(Company.market == "US") if c.code not in keep]
                if stale:
                    db.query(WatchItem).filter(WatchItem.market == "US", WatchItem.code.in_(stale)).delete(synchronize_session=False)
                    db.query(Company).filter(Company.market == "US", Company.code.in_(stale)).delete(synchronize_session=False)
                    removed = len(stale)

        for key in ("BUILD", "SCREENS", "SHORT", "BUILD_STAMP", "CANDIDATES", "BUILD_NEW"):
            if key not in meta:
                continue
            mkey = _mkey(key, market)
            row = db.get(MetaKV, mkey)
            if row:
                row.value = meta[key]
            else:
                db.add(MetaKV(key=mkey, value=meta[key]))

        hash_key = _mkey("SEED_HASH", market)
        row = db.get(MetaKV, hash_key)
        if row:
            row.value = data_hash(market)
        else:
            db.add(MetaKV(key=hash_key, value=data_hash(market)))
        db.commit()
        print(f"Seed complete ({market}): {inserted} inserted, {updated} updated, "
              f"{len(companies)} total companies" + (f", {removed} removed." if removed else "."))
    finally:
        db.close()


def data_hash(market: str = "IN") -> str:
    """Fingerprint of every file that market's seed reads, so a deploy only
    re-seeds that market when its data changed."""
    settings = get_settings()
    market = market.upper()
    paths = ((settings.seed_file, settings.meta_file, settings.candidates_file, settings.build_stamp_file)
             if market == "IN" else (settings.seed_file_us, settings.meta_file_us))
    h = hashlib.sha256()
    for path in paths:
        try:
            h.update(path.read_bytes())
        except OSError:
            h.update(b"-")
    return h.hexdigest()


# Distinct Postgres advisory-lock ids per market so India and US seeding
# never serialize behind each other across workers, while each market is
# still race-safe against its own concurrent workers.
_LOCK_IDS = {"IN": 804211, "US": 804212}


def _seed_market_if_changed(market: str, required: bool) -> None:
    """required=False (US today): a missing seed file just means that
    market isn't populated yet, not a startup failure."""
    log = logging.getLogger("deepsweep")
    settings = get_settings()
    seed_file = settings.seed_file if market == "IN" else settings.seed_file_us
    if not required and not seed_file.exists():
        return
    want = data_hash(market)
    hash_key = _mkey("SEED_HASH", market)

    def seeded(conn):
        """The hash matches AND that market actually has companies -- a matching hash over an
        emptied table (see _recover_sqlite) must not stop the reseed."""
        if current_hash(conn) != want:
            return False
        try:
            return bool(conn.execute(text("SELECT COUNT(*) FROM companies WHERE market = :m"), {"m": market}).scalar())
        except Exception:  # noqa: BLE001
            conn.rollback()
            return False

    def current_hash(conn):
        try:
            v = conn.execute(text("SELECT value FROM meta_kv WHERE key = :k"), {"k": hash_key}).scalar()
        except Exception:  # noqa: BLE001 - table not there yet
            conn.rollback()
            return None
        return v.strip('"') if isinstance(v, str) else v

    if engine.dialect.name != "postgresql":
        with engine.connect() as conn:
            done = seeded(conn)
        if not done:        # SQLite: the connection above is closed first, so the seed can write
            seed(market)
            log.warning("seeded the %s database at startup (data changed)", market)
        return
    lock_id = _LOCK_IDS[market]
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": lock_id})
        conn.commit()
        try:
            if not seeded(conn):
                conn.commit()
                seed(market)
                log.warning("seeded the %s database at startup (data changed)", market)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
            conn.commit()


def seed_if_changed() -> None:
    """Run at app startup: load the bundled data unless this exact data is
    already in the database. With Postgres this is how a deploy gets its data
    (Render's build step may not reach a private database). Several workers
    start together, so on Postgres they queue on an advisory lock and the
    later ones find the work done. India always seeds; US seeds only once
    companies_us_raw.json exists, so an early deploy of this feature (no US
    data yet) doesn't fail startup.

    Runs migrate_schema() unconditionally first -- schema is independent of
    whether either market's *data* happens to have changed since the last
    deploy, so it can't be left to only run inside seed(), which is skipped
    entirely on a startup where the hash already matches."""
    log = logging.getLogger("deepsweep")
    try:
        migrate_schema()
        _seed_market_if_changed("IN", required=True)
        _seed_market_if_changed("US", required=False)
    except Exception as e:  # noqa: BLE001 - never stop the app from starting; the previous data stays
        log.warning("startup seed skipped: %s", e)


def migrate_schema() -> None:
    """One-time, idempotent schema upgrade for a pre-existing database:
    adds the `market` column + composite primary key the US-market feature
    needs to Company/WatchItem/SavedFilter. A brand new database needs
    none of this -- Base.metadata.create_all() (called right after this,
    both here and in main.py's lifespan) already builds the current schema
    straight from models.py.

    Deliberately does NOT swallow exceptions the way seed_if_changed() does:
    starting the app against a half-migrated schema (e.g. the market column
    exists but the primary key wasn't rebuilt yet) is worse than not
    starting at all, so a failure here should stop startup, loudly.

    Handles both Postgres (ALTER TABLE in place) and SQLite -- this
    project's actual live deployment runs SQLite with real user data, not
    Postgres, so "SQLite is only for local dev" cannot be assumed here.
    SQLite can't ALTER a primary key in place at all, so that branch does a
    full rebuild (new table, copy rows, drop old, rename) instead -- this
    was verified against a hand-built pre-migration SQLite file before
    being trusted with anything real: adding the `market` column alone
    (without rebuilding the primary key) leaves the OLD `code`-only
    UNIQUE/PK constraint enforced underneath, which silently rejects the
    exact case this feature exists for -- a US ticker and an India code
    that happen to collide as bare strings.
    """
    insp = inspect(engine)
    if not insp.has_table("companies"):
        return  # brand new database; create_all() builds the current schema directly
    if engine.dialect.name == "postgresql":
        _migrate_postgres(insp)
    elif engine.dialect.name == "sqlite":
        _migrate_sqlite(insp)


def _migrate_postgres(insp) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(804210)"))
        conn.commit()
        try:
            cols = {c["name"] for c in insp.get_columns("companies")}
            if "market" not in cols:
                conn.execute(text("ALTER TABLE companies ADD COLUMN market VARCHAR(8) DEFAULT 'IN'"))
                conn.execute(text("UPDATE companies SET market = 'IN' WHERE market IS NULL"))
                conn.commit()
            for col in ("market_cap_usd", "insider_pct", "inst_pct"):
                if col not in cols:
                    conn.execute(text(f"ALTER TABLE companies ADD COLUMN {col} FLOAT"))
            conn.commit()

            pk = insp.get_pk_constraint("companies")
            if set(pk["constrained_columns"]) == {"code"}:
                pk_name = pk.get("name") or "companies_pkey"
                conn.execute(text(f'ALTER TABLE companies DROP CONSTRAINT "{pk_name}"'))
                conn.execute(text("ALTER TABLE companies ALTER COLUMN market SET NOT NULL"))
                conn.execute(text("ALTER TABLE companies ADD PRIMARY KEY (market, code)"))
                conn.commit()

            if insp.has_table("watchlist_items"):
                wcols = {c["name"] for c in insp.get_columns("watchlist_items")}
                if "market" not in wcols:
                    conn.execute(text("ALTER TABLE watchlist_items ADD COLUMN market VARCHAR(8) DEFAULT 'IN'"))
                    conn.execute(text("UPDATE watchlist_items SET market = 'IN' WHERE market IS NULL"))
                    conn.commit()
                uqs = {u["name"] for u in insp.get_unique_constraints("watchlist_items")}
                if "uq_watch_user_code" in uqs and "uq_watch_user_market_code" not in uqs:
                    conn.execute(text("ALTER TABLE watchlist_items DROP CONSTRAINT uq_watch_user_code"))
                    conn.execute(text(
                        "ALTER TABLE watchlist_items ADD CONSTRAINT uq_watch_user_market_code "
                        "UNIQUE (user_id, market, code)"))
                    conn.commit()

            if insp.has_table("saved_filters"):
                scols = {c["name"] for c in insp.get_columns("saved_filters")}
                if "market" not in scols:
                    conn.execute(text("ALTER TABLE saved_filters ADD COLUMN market VARCHAR(8) DEFAULT 'IN'"))
                    conn.execute(text("UPDATE saved_filters SET market = 'IN' WHERE market IS NULL"))
                    conn.commit()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(804210)"))
            conn.commit()


def _rebuild_sqlite_table(conn, table_name: str, target_table, default_market_cols: tuple[str, ...]) -> None:
    """Rename the old table aside, create the new one from the ORM's own
    Table object (so the target schema can never drift from models.py),
    copy every row across (old columns as-is, default_market_cols filled
    with 'IN' since every pre-existing row predates this feature), then
    drop the renamed-aside original. All within the caller's transaction.

    Inspects via inspect(conn) (bound to this specific connection), not
    inspect(engine) -- the latter opens a second connection under the
    hood, which SQLite's own locking blocks while this transaction is
    mid-write (confirmed live: "database is locked")."""
    old_cols = [c["name"] for c in inspect(conn).get_columns(table_name)]
    tmp_name = f"{table_name}__pre_market_migration"
    conn.execute(text(f'ALTER TABLE "{table_name}" RENAME TO "{tmp_name}"'))
    # An index keeps its name when its table is renamed, so the copies target_table.create()
    # is about to build ("index ix_... already exists") would collide with these. The rows
    # live in the table, not the index; the new table gets its own indexes.
    for (idx,) in conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = :t AND sql IS NOT NULL"),
            {"t": tmp_name}).all():
        conn.execute(text(f'DROP INDEX "{idx}"'))
    target_table.create(bind=conn)
    dest_cols = old_cols + [c for c in default_market_cols if c not in old_cols]
    select_cols = ", ".join(old_cols) + "".join(f", 'IN' AS {c}" for c in default_market_cols if c not in old_cols)
    conn.execute(text(
        f'INSERT INTO "{table_name}" ({", ".join(dest_cols)}) '
        f'SELECT {select_cols} FROM "{tmp_name}"'
    ))
    conn.execute(text(f'DROP TABLE "{tmp_name}"'))


_SQLITE_MIGRATED_TABLES = (("companies", Company.__table__), ("watchlist_items", WatchItem.__table__),
                           ("saved_filters", SavedFilter.__table__))


def _recover_sqlite(conn) -> None:
    """Undo a migration that two workers raced through.

    The rebuild is one transaction, but the gunicorn workers each ran it at the same
    moment, and the loser could leave a new, EMPTY table beside the old one renamed to
    <table>__pre_market_migration -- with the rows stranded in it. The board then loads
    with no companies, and seeding did not repair it because the data hash still matched.
    Called with the write lock held, so nothing else is mid-migration. Never deletes a
    table that still holds rows the live one does not.
    """
    for name, _table in _SQLITE_MIGRATED_TABLES:
        tmp = f"{name}__pre_market_migration"
        insp = inspect(conn)
        if not insp.has_table(tmp):
            continue
        count = lambda t: conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()  # noqa: E731
        if not insp.has_table(name):
            conn.execute(text(f'ALTER TABLE "{tmp}" RENAME TO "{name}"'))   # died between rename and create
            continue
        if count(tmp) and not count(name):
            old = [c["name"] for c in insp.get_columns(tmp)]
            new = [c["name"] for c in insp.get_columns(name)]
            shared = [c for c in old if c in new]
            fill_market = "market" in new and "market" not in old
            dest = ", ".join(shared + (["market"] if fill_market else []))
            src = ", ".join(shared + (["'IN'"] if fill_market else []))
            conn.execute(text(f'INSERT INTO "{name}" ({dest}) SELECT {src} FROM "{tmp}"'))
            if name == "companies":   # what was restored may predate the bundled data: reseed
                conn.execute(text("DELETE FROM meta_kv WHERE key = 'SEED_HASH'"))
            logging.getLogger("deepsweep").warning("restored %s from %s", name, tmp)
        elif count(tmp):
            continue   # the live table already has rows of its own; leave the old one for a human
        for (idx,) in conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = :t AND sql IS NOT NULL"),
                {"t": tmp}).all():
            conn.execute(text(f'DROP INDEX "{idx}"'))
        conn.execute(text(f'DROP TABLE "{tmp}"'))


def _migrate_sqlite(insp) -> None:
    with engine.connect() as conn:
        # Take SQLite's write lock before looking at anything, then decide. The workers
        # start together; without this they all saw "no market column" and all rebuilt.
        # The others now wait here (see the busy timeout in database.py) and, once the
        # first commits, find the column and return.
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            _recover_sqlite(conn)
            # Each table on its own: an earlier, raced migration could leave companies
            # rebuilt while watchlist_items / saved_filters were still the old shape.
            for name, table in _SQLITE_MIGRATED_TABLES:
                insp = inspect(conn)
                if insp.has_table(name) and "market" not in {c["name"] for c in insp.get_columns(name)}:
                    _rebuild_sqlite_table(conn, name, table, ("market",))
            # market_cap_usd/insider_pct/inst_pct are plain nullable columns with
            # no default needed -- the rebuilt table's own schema has them; only
            # `market` needs a value backfilled.
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


if __name__ == "__main__":
    try:
        seed("IN")
        if get_settings().seed_file_us.exists():
            seed("US")
        sys.exit(0)
    except OperationalError as e:
        # e.g. a Docker / Render build that can't reach a private Postgres: the app seeds itself at startup
        print(f"Database not reachable now ({str(e).splitlines()[0][:120]}); the app will load the data when it starts.")
        sys.exit(0)
