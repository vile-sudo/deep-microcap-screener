"""
Loads the research dataset (data/companies_raw.json + data/meta_raw.json)
into the database. Safe to re-run: it upserts every row, so re-seeding
after you refresh the source JSON (see scripts/refresh_data.py) just
updates existing companies and inserts any new ones -- nothing is
duplicated.

Usage:
    python -m app.seed
"""
import json
import sys

from .config import get_settings
from .database import Base, SessionLocal, engine
from .models import Company, MetaKV

# Known data-integrity gap in the source export: 20 companies carry
# screen="v8-weekly", a code the original board's SCREENS legend never
# defined (so opening their detail card, or exporting them to CSV, would
# throw in the original file). We patch the legend here rather than drop
# the companies, so every row on the board renders correctly.
SCREEN_PATCHES = {
    "v8-weekly": {
        "lab": "v8 weekly",
        "cls": "b-v8",
        "full": "v8 · weekly refresh sweep, added 23 Aug 2026 — not yet folded into the methodology write-up",
    },
}


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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

        db.commit()
        print(f"Seed complete: {inserted} inserted, {updated} updated, "
              f"{len(companies)} total companies.")
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(seed() or 0)
