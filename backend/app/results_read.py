"""Claude's read of each results filing (scripts/run_results_read.py) as quarterly numbers.

The evening a company files, Claude reads the PDF and returns that quarter's revenue and net profit
(consolidated and standalone). screener.in usually catches up a day or more later, so until it does, these
are the numbers the earnings-drift strategy and the SUE calculator score the result on -- on the same basis
(consolidated or standalone) as the company's history, and marked as Claude's.
"""
from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
READ_FILE = DATA / "results_read" / "latest.json"
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_cache: dict = {"stamp": None, "by_sym": {}, "basis": None}


def _basis() -> dict:
    """NSE symbol -> the basis its quarterly history is on (screener's choice), "consolidated" by default."""
    if _cache["basis"] is None:
        b = {}
        try:
            raw = json.loads((DATA / "companies_raw.json").read_text(encoding="utf-8"))
            board = json.loads((DATA / "results_in" / "latest.json").read_text(encoding="utf-8")).get("companies") or {}
            for c in raw:
                x = board.get(str(c.get("code") or ""))
                if x and c.get("nse_code"):
                    b[str(c["nse_code"]).upper()] = x.get("basis") or "consolidated"
        except (OSError, ValueError):
            pass
        try:
            uni = json.loads(gzip.decompress((DATA / "results_universe" / "latest.json.gz").read_bytes())).get("companies") or {}
            for k, v in uni.items():
                if k.startswith("NSE:"):
                    b.setdefault(k[4:], v.get("basis") or "consolidated")
        except (OSError, ValueError, EOFError):
            pass
        _cache["basis"] = b
    return _cache["basis"]


def _label(period_end: str | None, filed: str) -> str | None:
    try:
        d = date.fromisoformat(period_end) if period_end else None
    except ValueError:
        d = None
    if d is None or d.month not in (3, 6, 9, 12):
        f = date.fromisoformat(filed[:10])          # the last quarter end before the filing
        m = ((f.month - 1) // 3) * 3
        d = date(f.year - (m == 0), m or 12, 1)
    return f"{MON[d.month - 1]} {d.year}"


def quarters(symbol: str) -> dict:
    """{"Sep 2026": {"np", "np_prev", "revenue", "filed", "basis", "source": "claude"}} from Claude's reads."""
    try:
        st = READ_FILE.stat().st_mtime
    except OSError:
        return {}
    if _cache["stamp"] != st:
        try:
            reads = json.loads(READ_FILE.read_text(encoding="utf-8")).get("reads") or {}
        except (OSError, ValueError):
            reads = {}
        by: dict = {}
        basis = _basis()
        for rid, r in reads.items():
            fig = r.get("figures") or {}
            if not fig:
                continue
            sym, filed = rid.split("|", 1)
            want = basis.get(sym.upper(), "consolidated")
            f = fig.get(want) or {}
            if f.get("net_profit_cr") is None:            # the filing has only the other set: use it, marked
                other = "standalone" if want == "consolidated" else "consolidated"
                if want == "consolidated" and (fig.get(other) or {}).get("net_profit_cr") is not None:
                    f, want = fig[other], other           # no subsidiaries: standalone is the whole company
                else:
                    continue
            lab = _label(r.get("period_end"), filed)
            cur = by.setdefault(sym.upper(), {}).get(lab)
            if cur and cur["filed"] <= filed:
                continue                                  # keep the first filing for the quarter
            by[sym.upper()][lab] = {"np": f["net_profit_cr"], "np_prev": f.get("prev_year_net_profit_cr"),
                                    "revenue": f.get("revenue_cr"), "filed": filed, "basis": want, "source": "claude"}
        _cache.update(stamp=st, by_sym=by)
    return _cache["by_sym"].get(symbol.upper(), {})
