"""
Finds one new investment theme, from a blank page, every day -- the same judgment call
as when a person hands you a news article about, say, India's semiconductor push
creating a market for photoresist chemicals -- and writes
backend/sectors/<slug>/brief.json for it, then hands off to sector_research.py to write
its first edition immediately. Meant to run once a day, every day, each time on a
different topic; see existing_themes() for how it avoids repeating one.

The point isn't just a real theme -- see task()'s "get there before the market does"
section: it's told to actively favour a theme mainstream financial media hasn't already
covered as a group and whose candidate stocks haven't already had a big theme-driven
re-rating, over an obvious one that's already common knowledge and already reflected in
the price.

    cd backend
    python scripts/theme_discovery.py               # find and publish today's theme
    python scripts/theme_discovery.py --dry-run      # find a theme, print it, write nothing

COST NOTE
----------
This runs a full sector_research.py first edition (a real web-search Claude Code
session) every single day, on top of the existing per-sector weekly re-verification
(sector_research.py's own SECTORS_PER_RUN loop) and the daily "latest developments"
check (sector_latest.py) that every published theme gets forever. As the number of
published themes grows, the weekly-refresh and latest-developments workload grows
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
 "scope": "one line: what the theme covers, why it's investable now, and how early/undiscovered it still is",
 "questions": ["8-10 questions for the full research pass to answer -- what the theme is, how it works, how "
               "India participates, what named companies are doing about it (capex, JVs, capacity), why each "
               "could be a beneficiary, whether the market has already priced this in (stock performance and "
               "media/broker coverage to date) or it's still early, risks, catalysts -- mirror the shape of an "
               "existing brief.json"],
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
## Non-negotiable: this only matters if an Indian listed company can actually be in it
This board is for buying India-listed stocks, not for admiring a global trend from the sidelines. However real
and interesting the global driver is, a theme is only valid here if, for at least one Indian listed company, you
can confirm one of these two things with a real, cited source:
  (a) it is ALREADY WORKING in this space -- capex spent, a JV signed, a facility commissioned, a product
      qualified, a regulatory filing, revenue from it; or
  (b) it has PUBLICLY ANNOUNCED PLANS to enter it or set up a new facility/plant/capacity for it -- a board
      approval, an investor-presentation slide, an exchange filing, a credit-rating rationale, reported capex
      guidance -- even if construction hasn't started yet, as long as the company itself has said so, not you
      guessing it might.
If a niche is real worldwide but you cannot confirm (a) or (b) for any Indian listed company, that is not a
usable theme for this board: keep looking, or skip the day. A genuine, cited technical/chemistry/customer
adjacency (a company plausibly positioned to move into this niche, without yet a company-confirmed statement of
intent) is a weaker, third-best case -- acceptable only when the niche is genuinely too early globally for any
company statement to exist yet, and only if you say plainly that this is inference, not (a) or (b). Do not force
a fit by including a company only because it is broadly "in the same sector" with no specific connection to this
niche.

List as many companies as genuinely qualify under (a) or (b) -- that could be just 2-3 for a very early or narrow
niche, or a dozen for a broad one; do not pad the list with tenuous names to hit a target count. Quality and
genuineness matter far more than count.

## Look across small, mid and large cap, not just whichever names come up first
When more than one genuine company qualifies, actively check across the market-cap spectrum -- small cap, mid
cap and large cap -- rather than settling for whichever few names surface first in search results, which tend to
skew toward the biggest, most-covered companies. A large cap where this theme is one small line item is easy to
find and is often the same name every broker note already mentions; a genuinely involved small or mid cap is
more likely to be the under-the-radar find this whole exercise is for, and a large cap with real, direct
involvement is still worth including for scale and credibility. Aim to represent more than one size tier when
the evidence genuinely supports it -- but this is about searching harder for qualifying names across sizes, not
about forcing a small-cap in just to tick a box: every company, of any size, still has to clear the non-negotiable
evidence bar above.

## The whole point: get there before the market does
The goal is not just a real theme -- it's a theme the market hasn't fully priced in yet, so there's still room to
act before it's obvious. Actively favour a theme where:
- Mainstream financial media hasn't yet run "stocks to play this theme" pieces naming these companies as a
  group -- it's still mostly visible in niche trade press, industry/market-research reports, or scattered company
  disclosures nobody has connected yet, not in broker thematic notes everyone has already read.
- The candidate stocks have NOT already had a large, theme-driven re-rating -- check: has the stock run up
  sharply (e.g. 50%++) over the last several months in a way news coverage already attributes to this theme? If
  most of your candidates have already re-rated hard and are being written about everywhere, the market has
  already found this one -- either look further for something earlier, or still write it up if the driver is
  genuinely intact, but say plainly in scope/summary that it's already partly discovered and re-rated, don't
  present it as an undiscovered opportunity when it visibly isn't.
- Prefer relatively under-the-radar companies actually doing the work over the obvious, already-heavily-covered
  large caps -- a smaller name with real evidence of involvement is a better find than a mega-cap where this is
  one line item among many analysts already track closely.
This is a judgment call, not a hard rule -- verify it with search rather than assume either way.

## Do not propose any of these -- already covered or already on the backlog
{avoid or "(nothing yet -- this is the first theme)"}

## What to do, in this order
1. Search both: (i) recent Indian business and financial news/analysis (what a market newsletter, a broking
   house's morning note, or a business paper would cover), and (ii) global industry/market-research reports and
   trade publications on specific product, material, component or equipment categories and their growth drivers
   (IndexBox-style sources, sector trade press, company/industry-association reports). Either can be where a good
   theme comes from -- (ii) is often where the earliest, least-discovered themes are. Shortlist candidate themes.
2. For your strongest candidate, VERIFY BEFORE DOING ANYTHING ELSE: is there at least one Indian listed company
   that is (a) already working in this space, OR (b) has publicly announced plans to enter it or set up a new
   facility/plant/capacity for it? Search specifically for this -- company announcements, exchange filings,
   investor presentations, credit-rating rationales, news coverage of the company itself, not just of the global
   trend. If you cannot confirm this for your top candidate, it fails the non-negotiable gate above: try your next
   candidate theme, or if none of them clear it, skip the day (step 4). Do not move on to writing anything until
   this is confirmed for at least one real company.
3. Only once Indian participation is verified: confirm the theme's driver is real and current, verify every
   company you plan to include has the evidence you're crediting it with (working in it, or planning to
   enter/build), check how much media/market attention and stock re-rating this has already had (per "get there
   before the market does" above), then write out/brief.json in exactly this format (valid JSON, double quotes) --
   do not invent names, figures or capabilities:
{FORMAT}
   - bucket_labels: 2-4 groups that fit THIS theme's actual value chain (do not reuse another theme's groups
     unless they genuinely apply) plus you do not need to add "other" -- it is always available automatically.
   - universe: real NSE/BSE symbols only, grouped under the same keys as bucket_labels -- as many as genuinely
     qualify (see the non-negotiable section above), not a count to hit.
4. If nothing clears the bar (no current driver, no confirmed Indian listed participation, or everything you
   found is already widely known and re-rated with nothing earlier available), write out/brief.json as
   {{"slug": null}} and stop -- a skipped day is better than a weak, duplicate, India-less, or already-obvious
   theme.

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
    if total_symbols < 2:
        errs.append(f"only {total_symbols} symbol(s) in universe -- need genuine Indian listed participation "
                     "(at least 2; a real early niche can be this narrow, but zero or one isn't a theme)")
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
    brief["kind"] = "theme"   # this script only ever creates themes, never the broader hand-curated sectors
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
