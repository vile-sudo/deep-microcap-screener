"""Column headings of screener.in's results tables ("Sep 2026").

Since 16 Nov 2026 a consolidated table fills the periods the company has no consolidated figures for with its
standalone ones and marks those columns "Sep 2026 (S)" (the text arrives with line breaks inside it). Everything
downstream expects a plain "Mon YYYY", so the scrapers clean the heading as they read it, and a company whose
newest quarter is a standalone column is recorded as standalone -- the consolidated page is then showing nothing
but the standalone history, which is what the standalone page gave before the change.

heal() does the same to an entry already on file, so results fetched before the fix read correctly and the
scrapers rewrite them clean on their next run.
"""
from __future__ import annotations

import re

_LABEL = re.compile(r"\s*([A-Z][a-z]{2})[a-z]*\s+(\d{4})\b")


def clean(label) -> str:
    """"Sep 2026" from "Sep 2026 (S)" in any spacing; any other heading ("TTM") with its spacing collapsed."""
    m = _LABEL.match(str(label or ""))
    return f"{m.group(1)} {m.group(2)}" if m else " ".join(str(label or "").split())


def is_standalone(label) -> bool:
    """A column of standalone figures in a consolidated table."""
    return "(S)" in str(label or "")


def cleaned(obj):
    """A copy of a stored table / entry / profile with every "periods" list cleaned."""
    if isinstance(obj, dict):
        return {k: [clean(p) for p in v] if k == "periods" and isinstance(v, list) else cleaned(v) for k, v in obj.items()}
    return obj


def heal(entry):
    """A copy of a results entry ({"quarters": {...}, "basis", ...}) with clean headings and the basis they imply."""
    if not isinstance(entry, dict):
        return entry
    periods = (entry.get("quarters") or {}).get("periods") or []
    out = cleaned(entry)
    if periods and is_standalone(periods[-1]):
        out["basis"] = "standalone"
    return out
