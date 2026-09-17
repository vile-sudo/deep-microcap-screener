"""
Screen any Chart's "EMA crossover (weekly)" filter.

Deliberately its own file and its own pipeline, not folded into
charts.compute_stats() or the 50/200-day SMA trend classification the rest
of the dashboard uses. Every NSE/BSE stock, board or not, is checked the
same simple way here: a 9-week and a 21-week EMA built from weekly closes,
and whether the fast one crossed the slow one recently, or is converging
toward one without having crossed yet ("forming" -- see forming() below).
It exists only to answer one question -- "which stocks just had, or are
about to have, a 9/21 weekly EMA cross?" -- as a filter, not as a trend
signal, and nothing else on the dashboard reads from it.

scripts/update_charts.py calls write() once a day over every stock and
saves chart_data/ema_crossover.json; routers/charts.py serves it at
GET /api/charts/ema-crossover, and the frontend reads it only inside
Screen any Chart's controls.
"""
import json
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
CROSS_FILE = BACKEND_DIR / "chart_data" / "ema_crossover.json"


def _weekly_closes(rows: list[list]) -> list[tuple[str, float]]:
    """One bar a week from daily rows -- (the week's last trading day, that
    day's close) -- keeping the current, still-forming week too, the same
    way a live weekly chart's rightmost bar updates through the week."""
    weeks: dict[tuple[int, int], tuple[str, float]] = {}
    for r in rows:
        key = date.fromisoformat(r[0]).isocalendar()[:2]
        weeks[key] = (r[0], r[4])
    return list(weeks.values())


def _ema_series(closes: list[float], n: int) -> list[float | None]:
    """A plain average of the first n closes seeds it, then it's carried
    forward with the usual EMA weighting. One entry per close, None until
    the seed, so index i always lines up with closes[i]."""
    if len(closes) < n:
        return [None] * len(closes)
    k = 2 / (n + 1)
    out: list[float | None] = [None] * (n - 1)
    ema = sum(closes[:n]) / n
    out.append(ema)
    for c in closes[n:]:
        ema = c * k + ema * (1 - k)
        out.append(ema)
    return out


def crossover(rows: list[list], fast: int = 9, slow: int = 21, lookback_weeks: int = 8) -> dict | None:
    """Whether the `fast`-week EMA crossed the `slow`-week EMA recently, on
    weekly bars built from daily candles -- None if there isn't enough
    weekly history yet, or no cross inside lookback_weeks."""
    weekly = _weekly_closes(rows)
    if len(weekly) < slow + 2:
        return None
    closes = [c for _, c in weekly]
    ef, es = _ema_series(closes, fast), _ema_series(closes, slow)
    t = len(closes) - 1
    for i in range(t, max(slow, t - lookback_weeks), -1):
        if None in (ef[i], es[i], ef[i - 1], es[i - 1]):
            break
        now_up, prev_up = ef[i] > es[i], ef[i - 1] > es[i - 1]
        if now_up != prev_up:
            return {"state": "crossed", "direction": "bull" if now_up else "bear",
                    "weeks_ago": t - i, "week": weekly[i][0]}
    return None


def forming(rows: list[list], fast: int = 9, slow: int = 21, converge_weeks: int = 3,
            max_gap_pct: float = 3.0) -> dict | None:
    """A stock with no recent cross (crossover() found nothing), but whose
    9 and 21-week EMA are closing in on each other -- within max_gap_pct of
    one another, and closer now than converge_weeks ago. Catches the setup
    a week or two before the cross itself, rather than after."""
    weekly = _weekly_closes(rows)
    if len(weekly) < slow + converge_weeks + 1:
        return None
    closes = [c for _, c in weekly]
    ef, es = _ema_series(closes, fast), _ema_series(closes, slow)
    t = len(closes) - 1
    p = t - converge_weeks
    if None in (ef[t], es[t], ef[p], es[p]) or es[t] == 0 or es[p] == 0:
        return None
    gap_now, gap_before = abs(ef[t] - es[t]) / es[t] * 100, abs(ef[p] - es[p]) / es[p] * 100
    if gap_now <= max_gap_pct and gap_now < gap_before:
        return {"state": "forming", "direction": "bull" if ef[t] < es[t] else "bear",
                "gap_pct": round(gap_now, 2)}
    return None


def screen(rows: list[list]) -> dict | None:
    """One stock's result for the EMA crossover filter: either it just
    crossed, or it's converging toward a cross without one yet -- never
    both, since forming() only runs once crossover() finds nothing."""
    return crossover(rows) or forming(rows)


def load() -> dict:
    try:
        return json.loads(CROSS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"as_of": None, "count": 0, "crossovers": {}}


def write(days: list, board: dict, universe_builder) -> int:
    """Runs the screen over every board company (keyed by its board code)
    and every other actively traded NSE/BSE stock (keyed the same way
    universe.json is), and writes the combined result.

    `universe_builder` is charts.build_universe, passed in by the caller
    rather than imported here, so this module never has to share any of
    charts.py's own rules to reach every stock."""
    on_isins = frozenset(s.get("isin") for s in board.values() if s.get("isin"))
    on_keys = frozenset((s["exchange"], s.get("key") or s["symbol"]) for s in board.values())
    out = {}
    for code, s in board.items():
        c = screen(s["rows"])
        if c:
            out[code] = c
    for key, s in universe_builder(days, on_isins, on_keys):
        c = screen(s["rows"])
        if c:
            out[key] = c
    CROSS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CROSS_FILE.write_text(json.dumps({
        "as_of": days[-1][0].isoformat() if days else None,
        "count": len(out),
        "crossovers": out,
    }, separators=(",", ":")), encoding="utf-8")
    return len(out)
