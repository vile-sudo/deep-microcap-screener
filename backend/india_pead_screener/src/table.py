"""Terminal tables with ruled borders:

    ┌────────┬───────┐
    │ symbol │   SUE │
    ├────────┼───────┤
    │ TCS    │  1.67 │
    └────────┴───────┘

Numbers (and +x.x% / Rs-style figures) are right-aligned, text left-aligned. Set PEAD_ASCII=1 for +-| borders
(for a console that shows box characters as junk).
"""
from __future__ import annotations

import os
import re
import sys

import pandas as pd

try:                                         # box characters need UTF-8 when output is redirected on Windows
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

_NUM = re.compile(r"^[~+\-]?[\d,]*\.?\d+%?$")
_UNI = dict(tl="┌", tm="┬", tr="┐", ml="├", mm="┼", mr="┤", bl="└", bm="┴", br="┘", h="─", v="│")
_ASC = dict(tl="+", tm="+", tr="+", ml="+", mm="+", mr="+", bl="+", bm="+", br="+", h="-", v="|")


def _cell(v) -> str:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if isinstance(v, float):
        return f"{v:,.0f}" if v.is_integer() or abs(v) >= 1e6 else f"{v:,.2f}"
    return str(v)


def box(df: pd.DataFrame, header: bool = True, max_width: int = 40) -> str:
    """The frame as a ruled table (index dropped). Cells longer than max_width are cut with '…'."""
    if df is None or len(df) == 0:
        return "  (none)"
    g = _ASC if os.environ.get("PEAD_ASCII") else _UNI
    cols = [str(c) for c in df.columns]
    rows = [[_cell(v) for v in r] for r in df.itertuples(index=False)]
    rows = [[c if len(c) <= max_width else c[: max_width - 1] + ("…" if g is _UNI else "~") for c in r] for r in rows]
    right = [all(_NUM.match(r[i].replace(" ", "")) or r[i] == "" for r in rows) and any(r[i] for r in rows)
             for i in range(len(cols))]
    w = [max([len(cols[i]) if header else 0] + [len(r[i]) for r in rows]) for i in range(len(cols))]

    def line(l, m, r):
        return l + m.join(g["h"] * (x + 2) for x in w) + r

    def fmt(vals):
        return g["v"] + g["v"].join(f" {v.rjust(w[i]) if right[i] else v.ljust(w[i])} " for i, v in enumerate(vals)) + g["v"]

    out = [line(g["tl"], g["tm"], g["tr"])]
    if header:
        out += [g["v"] + g["v"].join(f" {c.center(w[i])} " for i, c in enumerate(cols)) + g["v"], line(g["ml"], g["mm"], g["mr"])]
    out += [fmt(r) for r in rows]
    out.append(line(g["bl"], g["bm"], g["br"]))
    return "\n".join(out)


def kv(pairs: list[tuple[str, object]]) -> str:
    """A two-column item / value table without a header."""
    return box(pd.DataFrame(pairs, columns=["item", "value"]), header=False, max_width=80)
