"""Institutional flow: which institutions took a NEW position, EXITED entirely, or meaningfully grew/
shrunk an existing one in a US board company, quarter over quarter -- from the full per-holder breakdown
scripts/run_institutions_us.py's Form 13F fetch already collects (`holders`: every institution's name and
share count, not just the scorecard's top 5).

A slower, colder signal than deal/insider clusters -- 13F only updates quarterly, ~45 days after quarter
end -- but it is professional money managers' own disclosed conviction, arguably a stronger "smart money"
tell for a small-cap than any single insider's trade. Matched by institution NAME across quarters (13F's
own accession numbers are per-filing, not a stable identity), the same pragmatic choice
run_institutions_us.py already makes for matching a board company to its 13F issuer name.

INCREASE_PCT/DECREASE_PCT gate an existing (not new/exited) holder's change against being called out --
without it, ordinary quarter-to-quarter share-count noise (a fund's own rebalancing, not a real signal)
would dominate. New and exited positions have no such gate: appearing or disappearing entirely is
unambiguous regardless of size.
"""
from __future__ import annotations

INCREASE_PCT = 25.0     # an existing holder growing its position by at least this much
DECREASE_PCT = 25.0     # ...or shrinking it by at least this much
MIN_SHARES = 1000        # ignore a holder whose position (old or new) never exceeds this -- 13F's own
                          # reporting floor is already ~10,000 shares/$200k, so this is just a guard
                          # against a rounding artifact looking like a "new" position of a few shares


def _holder_change(name: str, before: int, after: int) -> dict | None:
    if before <= MIN_SHARES and after > MIN_SHARES:
        return {"name": name, "kind": "new", "shares": after}
    if before > MIN_SHARES and after <= MIN_SHARES:
        return {"name": name, "kind": "exited", "shares": before}
    if before > MIN_SHARES and after > MIN_SHARES:
        pct = (after - before) / before * 100
        if pct >= INCREASE_PCT:
            return {"name": name, "kind": "increased", "shares": after, "from_shares": before, "change_pct": round(pct, 1)}
        if pct <= -DECREASE_PCT:
            return {"name": name, "kind": "decreased", "shares": after, "from_shares": before, "change_pct": round(pct, 1)}
    return None


def build(current: dict, previous: dict) -> dict:
    """current/previous: the `companies` dict from institutions_us/latest.json and .../previous.json
    (code -> entry, entry["holders"] = {manager name: shares}). Returns {code: {"period", "prev_period",
    "new": [...], "exited": [...], "increased": [...], "decreased": [...]}} for every company with at
    least one notable change -- a company present in one snapshot but not the other (just added to the
    board, or 13F stopped matching its name) is skipped, since there is nothing to compare."""
    out: dict[str, dict] = {}
    for code, cur in current.items():
        prev = previous.get(code)
        if not prev or not cur.get("holders"):
            continue
        cur_holders, prev_holders = cur["holders"], prev.get("holders") or {}
        names = set(cur_holders) | set(prev_holders)
        changes = [c for n in names if (c := _holder_change(n, prev_holders.get(n, 0), cur_holders.get(n, 0)))]
        if not changes:
            continue
        by_kind: dict[str, list[dict]] = {"new": [], "exited": [], "increased": [], "decreased": []}
        for c in changes:
            by_kind[c["kind"]].append(c)
        for k in by_kind:
            by_kind[k].sort(key=lambda c: -c["shares"])
        out[code] = {"period": cur.get("period"), "prev_period": prev.get("period"), **by_kind}
    return out
