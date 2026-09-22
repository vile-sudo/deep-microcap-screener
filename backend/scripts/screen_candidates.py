"""
Screen the candidates queue (backend/data/candidates_raw.json) against the
board's own mechanical rules, and add any that clear them.

    cd backend
    python scripts/screen_candidates.py --dry-run
    python scripts/screen_candidates.py

Where auto_screen.py sweeps the whole market itself every night, this walks
the human-curated discovery queue instead -- new NSE/BSE/SME listings and
small-cap sweep hits the weekly pipeline already found and sourced a moat
quote for (moat_signal/moat_evidence). It reuses auto_screen.py's own
fetch_page/gates/theme_for/moat_evidence/score/record functions unchanged,
so a "candidates-queue" addition is graded by the exact same six-pillar
rubric and "any one of five" bar as an "auto" addition -- the difference is
only where the candidate came from, not how it's judged.

A candidate already hand-reviewed (moat_confirmed or flag set -- see the
United Drilling Tools / Amanta Healthcare / Shish Industries / ABH
Healthcare precedent in backend/data/candidates_raw.json) is left alone:
that was a more careful, independently-sourced check than this mechanical
pass can do, and re-running the mechanical rules over it could only produce
a *worse*-informed verdict, never a better one.

Every candidate this script resolves -- added or ruled out -- gets a
verdict written to both backend/data/candidates_raw.json and
automation/data/candidates-queue.json (the master queue the weekly
discovery pipeline reads from), so it isn't re-flagged next week. A
candidate whose screener.in fetch fails outright is left unresolved
(verdict stays null) rather than guessed at.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
BACKEND = SCRIPTS.parent
ROOT = BACKEND.parent
sys.path.insert(0, str(SCRIPTS))
import auto_screen as A  # noqa: E402

CANDIDATES = BACKEND / "data" / "candidates_raw.json"
QUEUE = ROOT / "automation" / "data" / "candidates-queue.json"


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def cand_dict(row: dict) -> dict:
    sym, board = str(row.get("sym") or ""), (row.get("board") or "").upper()
    nse_code = sym if board in ("NSE", "SME") else None
    bse_code = sym if board == "BSE" else None
    return {"isin": row.get("isin"), "name": row.get("name"), "nse_code": nse_code, "bse_code": bse_code}


def sync_queue(resolved: dict[str, dict]) -> None:
    """Write the same verdict/verdict_reason/research_note onto the matching
    row of the master queue automation/data/candidates-queue.json, keyed by
    ISIN -- the same two-file update the United Drilling Tools / Amanta
    Healthcare precedent used (commit "Mark the two board additions
    resolved in the candidates queue")."""
    master = load(QUEUE, [])
    if not master:
        return
    by_isin = {q.get("isin"): q for q in master if q.get("isin")}
    for isin, patch in resolved.items():
        row = by_isin.get(isin)
        if row is not None:
            row.update(patch)
    QUEUE.write_text(json.dumps(master, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="stop after checking this many (0 = no limit)")
    args = ap.parse_args()

    gates_cfg = A.load_gates()
    A.apply_gates(gates_cfg)
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()

    candidates = load(CANDIDATES, [])
    companies = load(A.COMPANIES, [])
    on_board = set()
    for c in companies:
        for k in ("code", "nse_code", "bse_code"):
            if c.get(k):
                on_board.add(str(c[k]).upper())

    todo = []
    skipped_reviewed = skipped_implausible = skipped_on_board = 0
    for row in candidates:
        sym = str(row.get("sym") or "").upper()
        if row.get("verdict"):
            continue
        if row.get("moat_confirmed") is not None or row.get("flag"):
            skipped_reviewed += 1
            continue
        if sym in on_board:
            row["verdict"] = "added_to_board"
            row["verdict_reason"] = "Already on the board under this symbol -- resolved without a live check."
            skipped_on_board += 1
            continue
        if row.get("plausible") is False:
            row["verdict"] = "ruled_out"
            row["verdict_reason"] = "Flagged implausible by the initial discovery scan; not independently checked further."
            skipped_implausible += 1
            continue
        todo.append(row)

    print(f"screen-candidates: {len(candidates)} in the queue; {skipped_reviewed} already hand-reviewed (left alone), "
          f"{skipped_on_board} already on the board, {skipped_implausible} ruled out as implausible without a fetch, "
          f"{len(todo)} to check live")
    if args.limit:
        todo = todo[: args.limit]
        print(f"screen-candidates: --limit set, checking {len(todo)}")

    added, ruled_out, errors = [], [], []
    for i, row in enumerate(todo, 1):
        cand = cand_dict(row)
        label = f"{row.get('sym')} {row.get('name')}"
        try:
            got = A.fetch_page(cand)
        except Exception as e:  # noqa: BLE001 -- one bad fetch must not kill the run
            row["screen_error"] = str(e)[:200]
            errors.append(label)
            print(f"  [{i}/{len(todo)}] ERROR  {label}: {e}")
            continue
        if not got:
            row["screen_error"] = "not found on screener.in under either code"
            errors.append(label)
            print(f"  [{i}/{len(todo)}] NOT ON SCREENER  {label}")
            continue
        code, soup = got
        p = A.parse(soup)
        fails, tier = A.gates(p)
        if A.LEGACY_BRAND.search(p.get("name") or cand["name"] or ""):
            row["verdict"] = "ruled_out"
            row["verdict_reason"] = "Legacy/PSU brand name -- not a hidden company by this board's own definition."
            ruled_out.append(label)
            print(f"  [{i}/{len(todo)}] RULED OUT  {label}: legacy brand")
            continue
        known = A.known_to_public(p, tier)
        if known:
            row["verdict"] = "ruled_out"
            row["verdict_reason"] = f"Not hidden: {known}."
            ruled_out.append(label)
            print(f"  [{i}/{len(todo)}] RULED OUT  {label}: {known}")
            continue
        theme = A.theme_for(p)
        if not theme:
            path = " / ".join(p.get("sector_path") or []) or "no sector page found"
            row["verdict"] = "ruled_out"
            row["verdict_reason"] = f"Sector doesn't map to a board theme: {path}."
            ruled_out.append(label)
            print(f"  [{i}/{len(todo)}] RULED OUT  {label}: sector {path}")
            continue
        ev, guidance, capacity_util, product_pivot = A.moat_evidence(p, soup)
        turned = A.pat_turnaround(p.get("pat_series_cr") or [])
        guidance_pct = guidance.get("pct") if guidance else None
        over_guidance = guidance_pct is not None and guidance_pct > A.GUIDANCE_OVER_PCT
        if not (ev or turned or over_guidance or capacity_util or product_pivot):
            row["verdict"] = "ruled_out"
            row["verdict_reason"] = ("No moat evidence, management guidance over "
                                     f"{A.GUIDANCE_OVER_PCT}%, PAT turnaround, capacity-utilisation guidance or "
                                     "product-mix pivot found in the company's own profile or annual report -- "
                                     "the weekly scan's original hit didn't hold up under the board's own rules."
                                     + (f" (also fails: {'; '.join(fails)})" if fails else ""))
            ruled_out.append(label)
            print(f"  [{i}/{len(todo)}] RULED OUT  {label}: no evidence of any of the five")
            continue
        rec = A.record(code, cand, p, ev, tier, theme, today, guidance, fails, capacity_util, product_pivot)
        rec["screen"] = rec["source"] = rec["rubric"] = "candidates-queue"
        rec["warnings"] = [w for w in rec["warnings"]] + [
            "Sourced from the weekly discovery queue (new listing / small-cap sweep), graded by the board's "
            "own mechanical rules -- see backend/scripts/screen_candidates.py. Same evidence bar as an "
            "auto-added company; nobody has independently verified this company's claims."
        ]
        companies.append(rec)
        on_board.add(code.upper())
        if cand.get("nse_code"):
            on_board.add(cand["nse_code"].upper())
        if cand.get("bse_code"):
            on_board.add(cand["bse_code"].upper())
        row["verdict"] = "added_to_board"
        row["verdict_reason"] = f"Added to the board {today}, score {rec['final_score']} -- see backend/data/companies_raw.json, code {code}."
        added.append((label, rec["final_score"]))
        why = ", ".join(ev) or ("PAT turnaround" if turned else "capacity utilisation" if capacity_util
                                 else "product pivot" if product_pivot else f"guidance {guidance_pct}%")
        print(f"  [{i}/{len(todo)}] ADDED  {label}  score {rec['final_score']}  {theme}  [{why}]")

    print(f"screen-candidates: {len(added)} added, {len(ruled_out)} ruled out, {len(errors)} errors "
          f"(left unresolved), {len(todo)} checked")

    if args.dry_run:
        print("screen-candidates: --dry-run, nothing written")
        return 0

    resolved = {}
    for row in candidates:
        if row.get("verdict") and row.get("isin"):
            resolved[row["isin"]] = {k: row[k] for k in ("verdict", "verdict_reason") if k in row}
    CANDIDATES.write_text(json.dumps(candidates, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if added:
        A.COMPANIES.write_text(json.dumps(companies, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    sync_queue(resolved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
