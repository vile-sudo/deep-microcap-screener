"""Join the evidence for each flagged move into a single, ranked explanation.

Evidence is ordered by how much it actually proves. A company's own filing is
what it told the exchange, under obligation, in a window when it could have moved
the price; a news headline is somebody's account of events. Where both agree the
verdict is strong, and where neither exists the honest answer is that no reason
was disclosed, which is itself worth reporting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from .announcements import Filing
from .corpactions import Adjustment
from .deals import Deal
from .news import Story

LOGGER = logging.getLogger(__name__)

# How far a deal has to go before it is worth mentioning on its own.
DEAL_CRORE = 1.0

# Filing labels too vague to serve as an explanation on their own.
GENERIC_KINDS = {"other disclosure", "routine filing"}
# "Explainer" says a reason exists without saying what it is, so a story that
# names the event is preferred for the verdict even when it ranks lower.
VAGUE_STORY_KINDS = {"explainer"}


@dataclass(frozen=True, slots=True)
class Evidence:
    tier: str
    kind: str
    detail: str
    url: str = ""
    source: str = ""


@dataclass(slots=True)
class Explanation:
    symbol: str
    headline: str
    confidence: str
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def has_reason(self) -> bool:
        return self.confidence != "none"


def explain(
    symbol: str,
    *,
    adjustment: Adjustment | None = None,
    filings: Iterable[Filing] = (),
    stories: Iterable[Story] = (),
    deals: Iterable[Deal] = (),
) -> Explanation:
    """Build one explanation from whatever evidence exists for a symbol."""
    evidence: list[Evidence] = []

    if adjustment is not None:
        evidence.append(
            Evidence("corporate action", adjustment.kind, adjustment.subject)
        )

    material = [f for f in filings if not f.is_routine]
    for filing in material[:3]:
        detail = filing.summary or filing.category
        when = filing.filed_at.strftime("%d %b %H:%M") if filing.filed_at else ""
        evidence.append(
            Evidence("filing", filing.kind, detail[:300], url=filing.url, source=when)
        )

    causal_news = list(stories)[:3]
    for story in causal_news:
        evidence.append(
            Evidence("news", story.kind, story.headline, url=story.url, source=story.source)
        )

    big_deals = [d for d in deals if d.value_crore >= DEAL_CRORE]
    for deal in big_deals[:2]:
        evidence.append(
            Evidence(
                "deal",
                f"{deal.kind} deal",
                f"{deal.client} {deal.side.lower()} {deal.quantity:,} shares "
                f"(Rs {deal.value_crore:.1f} crore)",
            )
        )

    headline, confidence = _verdict(adjustment, material, causal_news, big_deals)
    return Explanation(symbol=symbol, headline=headline, confidence=confidence, evidence=evidence)


def _verdict(
    adjustment: Adjustment | None,
    filings: list[Filing],
    stories: list[Story],
    deals: list[Deal],
) -> tuple[str, str]:
    """Pick the one-line reason and how much to trust it."""
    if adjustment is not None and not adjustment.quantifiable:
        return f"{adjustment.kind.title()} took effect; the move is not comparable", "mechanical"

    best_story = _most_specific(stories)

    if filings and stories:
        # The company disclosed something and the press reported it: strongest case.
        # When the filing is only generically labelled, the reporting is the more
        # informative description of the same event, so it names the verdict.
        kind = best_story.kind if filings[0].kind in GENERIC_KINDS else filings[0].kind
        return f"{kind.title()}, reported by {stories[0].source}", "high"
    if filings:
        # Fall back to the exchange's own category rather than a generic label.
        label = filings[0].category if filings[0].kind in GENERIC_KINDS else filings[0].kind
        return f"{label.title()} disclosed to the exchange", "high"
    if stories:
        # A second-tier service is accurate but machine-written, so a verdict that
        # rests only on one is reported as weaker than an editorial desk's account.
        only_secondary = all(story.tier == "secondary" for story in stories)
        return (
            f"{best_story.kind.title()} ({stories[0].source})",
            "low" if only_secondary else "medium",
        )
    if deals:
        largest = deals[0]
        return (
            f"{largest.kind.title()} deal of Rs {largest.value_crore:.1f} crore",
            "medium",
        )
    if adjustment is not None:
        return f"{adjustment.kind.title()}, adjusted out of the change", "mechanical"
    return "No disclosed reason", "none"


def _most_specific(stories: list[Story]):
    """The first story naming what happened, falling back to the best-ranked one."""
    for story in stories:
        if story.kind not in VAGUE_STORY_KINDS:
            return story
    return stories[0] if stories else None


def summarise(explanations: Iterable[Explanation]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0, "mechanical": 0, "none": 0}
    for item in explanations:
        counts[item.confidence] = counts.get(item.confidence, 0) + 1
    return counts
