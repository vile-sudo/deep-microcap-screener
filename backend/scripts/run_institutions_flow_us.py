"""Institutional flow: quarter-over-quarter new/exited/growing/shrinking 13F positions in US board
companies, from app/institutions_flow_us.py.

    cd backend
    python scripts/run_institutions_flow_us.py

Reads data/institutions_us/latest.json (this quarter) and data/institutions_us/previous.json (last
quarter, written by run_institutions_us.py the moment a genuinely new quarter's file replaces the old
one -- never on a same-quarter re-run, so this only ever computes a real quarter-over-quarter diff, not
noise from the board simply gaining more matched companies within one quarter). Writes
data/institutions_us/flow.json: {"fetched_at": ..., "companies": {code: {...}}}, read by
app/us_alerts.py (surfaced in the Alerts bell) and frontend/static/us.js (the company drawer's
Institutions section).

Runs in .github/workflows/institutions_us.yml right after the 13F fetch itself -- pure computation over
files already on disk, so it costs nothing extra and never re-hits the SEC. A quarter with no previous
snapshot to diff against (the very first run ever, or a quarter where run_institutions_us.py has not
yet seen TWO different source files) writes nothing -- there is nothing to compare.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
from app import institutions_flow_us as flow  # noqa: E402

CURRENT_FILE = BACKEND_DIR / "data" / "institutions_us" / "latest.json"
PREVIOUS_FILE = BACKEND_DIR / "data" / "institutions_us" / "previous.json"
OUT_FILE = BACKEND_DIR / "data" / "institutions_us" / "flow.json"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def main() -> int:
    current = _load(CURRENT_FILE, {}).get("companies") or {}
    previous = _load(PREVIOUS_FILE, {}).get("companies") or {}
    if not current or not previous:
        print("run_institutions_flow_us: no previous-quarter snapshot yet to diff against; nothing to do")
        return 0

    companies = flow.build(current, previous)
    payload = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "companies": companies}
    prev_out = _load(OUT_FILE, {})
    if prev_out.get("companies") == companies:
        print("run_institutions_flow_us: nothing changed")
        return 0
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"run_institutions_flow_us: {len(companies)} compan{'y' if len(companies) == 1 else 'ies'} "
          f"with a notable quarter-over-quarter change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
