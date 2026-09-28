"""
Finds one new investment theme, from a blank page, every day -- the same judgment call
as when a person hands you a news article about, say, India's semiconductor push
creating a market for photoresist chemicals -- and writes
backend/sectors/<slug>/brief.json for it, then hands off to sector_research.py to write
its first edition immediately. Meant to run once a day, every day, each time on a
different topic; see existing_themes() for how it avoids repeating one.

    cd backend
    python scripts/theme_discovery.py               # find and publish today's theme
    python scripts/theme_discovery.py --dry-run      # find a theme, print it, write nothing

COST NOTE
----------
This runs a full sector_research.py first edition (a real web-search Claude Code
session) every single day, on top of the existing per-sector monthly re-verification
(sector_research.py's own SECTORS_PER_RUN loop) and the daily "latest developments"
check (sector_latest.py) that every published theme gets forever. As the number of
published themes grows, the monthly-refresh and latest-developments workload grows
with it -- SECTORS_PER_RUN and the workflow timeouts may need raising over time so
older themes don't go stale while new ones keep being added.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep_report import claude_code  # noqa: E402
import sector_research  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
SECTORS = BACKEND / "sectors"
IST = timezone(timedelta(hours=5, minutes=30))
TOOLS = "Read,Write,Edit,WebSearch,WebFetch"
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,58}[a-z0-9]$")

FORMAT = """{
 "slug": "kebab-case, 2-60 chars, not already in use",
 "name": "...", "icon": "chip",
 "scope": "one line: what the theme covers and why it's investable now",
 "questions": ["8-10 questions for the full research pass to answer -- what the theme is, how it works, how "
               "India participates, what named companies are doing about it (capex, JVs, capacity), why each "
               "could be a beneficiary, risks, catalysts -- mirror the shape of an existing brief.json"],
 "bucket_labels": {"snake_case_key": ["Group title", "one-line description"], "...": ["...", "..."]},
 "universe": {"snake_case_key": ["SYMBOL", "..."], "...": ["..."]},
 "refresh": "weekly"
}"""


def today() -> date:
    return datetime.now(IST).date()


def existing_themes() -> list[dict]:
    """{slug, name, scope} for every theme that already has a brief.json, plus the
    hand-curated "coming next" backlog -- both are off-limits for a fresh proposal."""
    out = []
    if SECTORS.exists():
        for d in sorted(p for p in SECTORS.iterdir() if p.is_dir()):
            b = d / "brief.json"
            if b.exists():
                try:
                    brief = json.loads(b.read_text(encoding="utf-8"))
                    out.append({"slug": d.name, "name": brief.get("name"), "scope": brief.get("scope")})
                except (OSError, ValueError):
                    pass
    try:
        for p in json.loads((SECTORS / "planned.json").read_text(encoding="utf-8")):
            out.append({"slug": None, "name": p.get("name"), "scope": p.get("note")})
    except (OSError, ValueError):
        pass
    return out


def newest_theme_age_days() -> int | None:
    """Days since the most recently created theme's first edition, or None if there
    are no themes with a published edition yet (a first theme is always due)."""
    if not SECTORS.exists():
        return None
    created = []
    for d in SECTORS.iterdir():
        if not d.is_dir():
            continue
        eds = sorted(p.stem for p in d.glob("????-W??.json"))
        if not eds:
            continue
        try:
            first = json.loads((d / f"{eds[0]}.json").read_text(encoding="utf-8"))
            created.append(date.fromisoformat(str(first.get("updated", ""))[:10]))
        except (OSError, ValueError):
            continue
    if not created:
        return None
    return (today() - max(created)).days


def task(existing: list[dict]) -> str:
    avoid = "\n".join(f"- {e['name']}" + (f" ({e['scope']})" if e.get("scope") else "") for e in existing if e.get("name"))
    return f"""# Find one new investment theme for Indian equity investors

Today is {today().isoformat()}. You are looking for ONE genuinely new, structurally-driven investment theme --
the kind a sharp reader spots in a news article or an industry market-research report and immediately asks
"which listed companies benefit from this, and can any of them actually participate". A good theme has a real,
CURRENT driver -- either of these shapes both count equally:
  (a) a broad catalyst: policy change, technology shift, global supply-chain move, regulatory approval, a capex
      cycle, developing in roughly the last 1-3 months, not old news; or
  (b) a specific product/material/component/equipment niche that industry market-research is flagging as
      entering a demand growth phase (the kind of report IndexBox, Mordor Intelligence, Fortune Business
      Insights, MarketsandMarkets, GlobalData and similar publish -- e.g. "epoxy encapsulation compounds for
      semiconductor packaging", a specialty gas, a battery material, a niche component tied to EVs, renewables,
      electronics or another growing end market) -- these are often narrower and less obvious than macro
      business news, and are exactly the kind of theme worth surfacing here.
Either way you need at least 5-8 India-listed companies with real, evidenced grounds to be called a beneficiary:
prefer companies with checkable evidence of involvement already (capex announced, a JV signed, a facility
commissioned, a product qualified, a regulatory filing); where the niche is early enough globally that no Indian
company has announced anything yet, a company with a genuine, cited adjacency -- an existing manufacturing
capability, chemistry, process or customer relationship that plausibly lets it move into this niche as it grows
-- is acceptable, but say so plainly (this is visibility/potential, not a done deal) and still cite the source
for that capability. Do not include a company only because it is broadly "in the same sector" with no specific
connection to this niche.

## Do not propose any of these -- already covered or already on the backlog
{avoid or "(nothing yet -- this is the first theme)"}

## What to do
1. Search both: (i) recent Indian business and financial news/analysis (what a market newsletter, a broking
   house's morning note, or a business paper would cover), and (ii) global industry/market-research reports and
   trade publications on specific product, material, component or equipment categories and their growth drivers
   (IndexBox-style sources, sector trade press, company/industry-association reports). Either can be where a good
   theme comes from.
2. Pick the single strongest one: verify with WebSearch/WebFetch that it is real and current, and that the
   companies you plan to list actually have the evidence you're crediting them with (involvement or adjacency)
   -- do not invent names, figures or capabilities.
3. Write out/brief.json in exactly this format (valid JSON, double quotes):
{FORMAT}
   - bucket_labels: 2-4 groups that fit THIS theme's actual value chain (do not reuse another theme's groups
     unless they genuinely apply) plus you do not need to add "other" -- it is always available automatically.
   - universe: 5-15 real NSE/BSE symbols total, grouped under the same keys as bucket_labels.
4. If, after searching, nothing clears the bar (no current driver, or you can't find real company evidence or a
   genuine cited adjacency), write out/brief.json as {{"slug": null}} and stop -- a skipped day is better than a
   weak or duplicate theme.

Read the file back once and fix any JSON error. Reply DONE when finished.
"""


def _slug_ok(slug: str) -> bool:
    return bool(slug) and bool(SLUG_RE.match(slug)) and not (SECTORS / slug).exists()


def validate(brief: dict) -> list[str]:
    errs = []
    if not _slug_ok(str(brief.get("slug") or "")):
        errs.append("slug missing, malformed, or already in use -- pick a fresh kebab-case slug")
    for k in ("name", "scope", "bucket_labels", "universe"):
        if not brief.get(k):
            errs.append(f"missing {k}")
    q = brief.get("questions") or []
    if len(q) < 5:
        errs.append("need at least 5 questions")
    bl = brief.get("bucket_labels") or {}
    uni = brief.get("universe") or {}
    for k in uni:
        if k not in bl and k != "other":
            errs.append(f"universe key {k!r} has no matching bucket_labels entry")
    total_symbols = sum(len(v) for v in uni.values() if isinstance(v, list))
    if total_symbols < 5:
        errs.append(f"only {total_symbols} symbols in universe (need at least 5)")
    return errs


def discover(dry_run: bool) -> str:
    existing = existing_themes()
    work = Path(tempfile.mkdtemp(prefix="theme-discovery-"))
    (work / "out").mkdir()
    (work / "TASK.md").write_text(task(existing), encoding="utf-8")
    model = __import__("os").environ.get("THEME_DISCOVERY_MODEL") or "sonnet"
    timeout = int(__import__("os").environ.get("THEME_DISCOVERY_MINUTES", "40")) * 60
    try:
        prompt = "Read TASK.md in this folder and carry out the task it describes, exactly."
        errs = ["not run"]
        brief = None
        for attempt in range(2):
            claude_code._claude(work, prompt, tools=TOOLS, timeout=timeout, model=model)
            try:
                brief = json.loads((work / "out" / "brief.json").read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                brief, errs = None, [f"out/brief.json unreadable: {e}"]
                prompt = f"Read TASK.md. out/brief.json {errs[0]}. Fix it and reply DONE."
                continue
            if brief.get("slug") is None:
                return "skipped: no theme cleared the bar today"
            errs = validate(brief)
            if not errs:
                break
            prompt = f"Read TASK.md. out/brief.json has problems: {'; '.join(errs)}. Fix them and reply DONE."
        if errs:
            return f"failed validation: {'; '.join(errs)}"
    except claude_code.UsageLimitReached as e:
        return f"usage limit reached ({e})"
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)

    slug = brief["slug"]
    if dry_run:
        return f"[dry run] would create {slug!r}: {brief.get('name')} -- {json.dumps(brief, ensure_ascii=False)[:300]}..."

    (SECTORS / slug).mkdir(parents=True)
    (SECTORS / slug / "brief.json").write_text(json.dumps(brief, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    why = sector_research.run(slug, force=True)
    return f"created theme {slug!r} ({brief.get('name')}); first edition: {why}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="publish another theme even if one was already created today")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not claude_code.available():
        print("theme discovery: Claude Code CLI not installed - skipping")
        return 0
    import os
    if os.environ.get("GITHUB_ACTIONS") and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print("theme discovery: CLAUDE_CODE_OAUTH_TOKEN not set - skipping")
        return 0

    # One a day, every day -- the only gate is against running twice on the same
    # calendar day (e.g. a manual dispatch alongside the scheduled run).
    age = newest_theme_age_days()
    if not args.force and not args.dry_run and age is not None and age < 1:
        print("theme discovery: a theme was already created today - skipping")
        return 0

    print("theme discovery: looking for today's theme")
    print("theme discovery:", discover(args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
