"""
The analysis half of a deep-dive report, written by Claude from the source
documents and the numbers model.py computed.

Three passes over one cached document pack (business, forensics, verdict),
each returning its sections through a forced tool call, so the output is
always valid, schema-shaped JSON. The rules the model works under are in
SYSTEM below: label every claim, cite the document, never invent a number,
no buy/sell rating and no price target of its own.
"""
from __future__ import annotations

import json
import os
import re

from . import fetch

MODEL = os.environ.get("REPORT_MODEL", "claude-sonnet-5")
MAX_OUTPUT = int(os.environ.get("REPORT_MAX_OUTPUT_TOKENS", "16000"))

SYSTEM = """You are a skeptical buy-side equity analyst writing a forensic deep-dive research report on an Indian listed small/micro-cap company for sophisticated investors. You are not an IPO marketer and not a promoter.

EVIDENCE RULES (non-negotiable)
- Label every factual or analytical statement inline with exactly one of: [F] fact directly evidenced by a document or the numbers pack; [MC] management claim not independently verified; [AI] your own inference; [E] an estimate or calculation.
- Cite the source right after the label using the document ids given in the pack, e.g. "[F] (AR2026 p.112)", "[MC] (CALL Aug 2026)", "[F] (NUMBERS)", "[F] (RATING)". Page numbers only for annual reports, and only pages that appear in the pack.
- Never invent a number, customer, contract, order book, capacity, market share, guidance or date. If something is not disclosed in the pack, say plainly that it is not disclosed. "Not disclosed" is a finding, not a gap to fill.
- All valuation numbers (fair values, DCF, multiples, implied growth) come ONLY from the NUMBERS pack. You may interpret them; you may not produce new fair values, price targets or valuation figures.
- Do NOT give a buy/sell/hold/overweight/underweight rating or a price target. Present evidence, scenarios and the fair-value ranges from NUMBERS.
- Separate facts from management claims. Treat industry TAM figures as industry context, never as company revenue.
- Where two sources disagree, show both and say they are not reconciled.
- Rs crore unless stated; Indian fiscal years (FY26 = April 2025 - March 2026).
- Be balanced: give the strongest bull case and the strongest bear case.
- Write crisply. Tables where the content is tabular. No filler, no marketing adjectives.
"""

BLOCKS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["p", "bullets", "table", "callout"]},
            "text": {"type": "string", "description": "for p and callout"},
            "tone": {"type": "string", "enum": ["info", "good", "warn", "bad"], "description": "for callout"},
            "items": {"type": "array", "items": {"type": "string"}, "description": "for bullets"},
            "caption": {"type": "string", "description": "for table"},
            "columns": {"type": "array", "items": {"type": "string"}, "description": "for table"},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "for table"},
        },
        "required": ["type"],
    },
}


def _tool(name, section_ids, extra=None):
    props = {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string", "enum": section_ids}, "title": {"type": "string"},
                               "subsections": {"type": "array", "items": {"type": "object", "properties": {
                                   "title": {"type": "string"}, "blocks": BLOCKS}, "required": ["title", "blocks"]}}},
                "required": ["id", "title", "subsections"],
            },
        }
    }
    if extra:
        props.update(extra)
    return {"name": name, "description": "Submit the finished report sections.",
            "input_schema": {"type": "object", "properties": props, "required": list(props)}}


PASSES = [
    {
        "tool": "submit_business",
        "sections": {
            "executive_summary": "What the company actually does (2 short paragraphs); a bull-thesis TABLE with columns Thesis | Evidence | Financial impact | Time horizon | Confidence (4-8 rows); the strongest bear theses as bullets (5-10, each with evidence); key debates (3-5 bullets: what the market may be getting wrong, both ways); variant perception (1 paragraph).",
            "company_history": "Corporate timeline TABLE (Date | Event) from the documents; subsidiaries / group structure (table if disclosed); what the timeline tells an investor.",
            "business_model": "The production / value chain and where value is captured; how the company earns money; capital intensity.",
            "products_segments": "Product family TABLE (Product | Application | Segment | Customers | Competitive intensity | Growth outlook | Margin potential) as far as disclosed; segment revenue TABLE if disclosed with growth; why each segment moved per management [MC] and your read [AI].",
            "revenue_drivers": "Revenue bridge for the latest year (disclosed drivers only, residuals labelled as residuals); export vs domestic and geography TABLE if disclosed; currency and tariff exposure.",
            "customers": "Customer concentration trend (top 1/5/10 if disclosed), named customers, stickiness, contract terms (long-term agreements or purchase orders), whether concentration is improving; say clearly when not disclosed.",
            "order_book": "Order book / backlog as disclosed (or an explicit statement that none is disclosed); a TABLE separating A confirmed revenue, B contracted not recognised, C qualified opportunity, D tender pipeline, E management aspiration, F industry TAM - never conflate them; execution timeline if disclosed.",
            "capacity_capex": "Facilities, installed capacity and utilisation TABLE if disclosed; capex plans, funding and status; operating-leverage implications; leases and single-location risks.",
        },
    },
    {
        "tool": "submit_forensics",
        "sections": {
            "margins_costs": "Raw materials and key costs, import dependence, pass-through ability, margin trend from NUMBERS with structural vs temporary drivers; EBITDA sensitivity only if it can be computed from NUMBERS (label [E]).",
            "financial_forensics": "Read the statements in NUMBERS like a forensic analyst: revenue and profit trajectory, earnings quality (CFO vs PAT, other income, tax rate), working capital (debtor/inventory/payable days, cash conversion cycle), capex and returns (ROCE/ROE trend), leverage and interest cover, balance-sheet composition. Quote the figures from NUMBERS with years. Do not repeat whole tables - the report shows them separately.",
            "accounting_quality": "Auditor, audit opinion, key audit matters, emphasis of matter, CARO observations, audit-trail compliance, contingent liabilities and guarantees, restatements, related-party accounting - from the annual report pages provided; say what was not in the pages provided.",
            "governance": "Management and board TABLE (Role | Name | Since | Background) as disclosed; promoter holding and changes (NUMBERS); pledges; related-party transactions with economic substance; remuneration; KMP / auditor / director changes; a management & governance scorecard TABLE (Factor | Score 1-10 | Rationale) with a simple average.",
            "competition_moat": "Competitive landscape and named peers; a moat scorecard TABLE (Moat factor | Score 1-10 | Rationale) covering brand, switching costs, regulatory/approval barriers, technology/IP, scale/cost, relationships, network effects, input control; the key questions the scores cannot answer; overall moat conclusion.",
            "industry_themes": "Industry structure and channel; TAM figures only as context with why they cannot be read as company revenue; structural themes that ARE supported by company evidence vs themes present in the narrative but NOT supported (table or two bullet lists).",
            "guidance_tracker": "TABLE (Item | Guidance given? | What management said [MC] | Status vs actuals) covering revenue growth, margins, order book, capex, utilisation, new products; if PREVIOUS REPORT guidance is provided, check each item against the latest NUMBERS and say met / missed / pending.",
        },
    },
    {
        "tool": "submit_verdict",
        "sections": {
            "valuation_view": "Interpret the NUMBERS valuation: current multiples, the bear/base/bull DCF fair values and their assumptions, the WACC x terminal-growth sensitivity, and the reverse DCF (growth the price implies) vs the company's history. Also give the strongest counter-argument to that conclusion. No new valuation numbers and no rating.",
            "catalysts": "Near-term (0-3 months), medium-term (3-12 months) and long-term (1-3 years) catalysts, plus overhangs (lock-ins, dilution, pledges, litigation) - only ones evidenced in the pack.",
            "risks": "Risks matrix TABLE (Risk | Category | Evidence | Severity 1-5 | Likelihood 1-5), 8-14 rows, most serious first.",
            "thesis_breakers": "TABLE (Thesis breaker | Measurable threshold | What it would confirm), 5-8 rows, plus bullets of thesis-confirming signals.",
            "monitoring": "Quarterly monitoring dashboard TABLE (Metric | Latest value (with period) | What to watch), 8-12 rows using NUMBERS and disclosures.",
            "red_flag_checklist": "Three bullet groups GREEN / AMBER / RED (use three subsections) combining the rule-based flags in NUMBERS with findings from the documents; each item labelled and cited.",
            "management_questions": "15-25 specific forensic questions for the next earnings call, grouped by theme (one subsection per theme), that the pack leaves unanswered.",
            "scorecard": "Investment scorecard TABLE (Factor | Score 1-10 | Basis) with 12-15 factors (revenue visibility, customer concentration, margin quality, earnings quality, balance sheet, working capital, accounting quality, governance, related-party discipline, moat durability, growth durability, optionality, valuation vs base-case DCF, valuation vs bull-case DCF, disclosure candour) and a simple average. This is not a rating.",
            "final_thesis": "The case for owning, the case against, what the market may be missing in both directions, a fair-value framing TABLE using only the NUMBERS bear/base/bull per-share values vs the current price, and a five-year possibility space. Close with what would make you re-review. No rating, no price target.",
            "changes_since_last": "If a PREVIOUS REPORT summary is provided: what changed this quarter in numbers, disclosures, guidance delivery, risks and the thesis (bullets, most important first). If none is provided, one paragraph saying this is initial coverage.",
            "quality_control": "Self-check bullets confirming: no TAM/revenue conflation, no invented order book or customers, management claims labelled, fair values only from NUMBERS, both bull and bear cases given, discrepancies flagged, and any sections where the evidence was thin.",
        },
        "extra": {"summary": {"type": "object", "properties": {
            "one_line": {"type": "string", "description": "one sentence on what the report concludes, no rating"},
            "bull_points": {"type": "array", "items": {"type": "string"}},
            "bear_points": {"type": "array", "items": {"type": "string"}},
            "watch_points": {"type": "array", "items": {"type": "string"}},
            "guidance": {"type": "array", "items": {"type": "string"}, "description": "each explicit management guidance item this quarter, for next quarter's tracker"},
        }, "required": ["one_line", "bull_points", "bear_points", "watch_points", "guidance"]}},
    },
]


# ------------------------------------------------------------------ document pack
def build_pack(company: dict, quant: dict, board: dict | None, previous: dict | None, budgets=None) -> tuple[str, list[dict]]:
    """The cached context every pass reads: numbers, documents, board notes, previous report."""
    budgets = budgets or {"ar": 150_000, "call": 70_000, "prev_call": 30_000, "ppt": 25_000, "rating": 20_000}
    docs = company["documents"]
    parts, sources = [], []

    def add_source(sid, kind, title, url, date=None):
        sources.append({"id": sid, "type": kind, "title": title, "url": url, "date": date})

    numbers = {k: quant[k] for k in ("basis", "units", "annual", "quarterly", "derived", "shareholding", "valuation", "red_flags", "latest_quarter")}
    parts.append("<document id=\"NUMBERS\" title=\"Screener.in statements and this report's computed metrics\">\n"
                 + json.dumps(numbers, separators=(",", ":"), ensure_ascii=False) + "\n</document>")
    add_source("NUMBERS", "numbers", f"screener.in {quant['basis']} statements + computed metrics", company["url"])
    parts.append(f"<document id=\"SCREENER\" title=\"screener.in company profile\">\nName: {company['name']}\nSector: {' > '.join(company['sector_path'])}\n"
                 f"About: {company['about']}\nKey points: {company['key_points']}\n</document>")

    ar = next((a for a in docs["annual_reports"] if a.get("url", "").lower().split("?")[0].endswith(".pdf")), None)
    if ar:
        try:
            pages = fetch.pdf_pages(ar["url"])
            chosen = fetch.select_pages(pages, budgets["ar"])
            if chosen:
                sid = f"AR{ar['year'] or ''}"
                body = "\n".join(f"[page {n}]\n{t}" for n, t in chosen)
                parts.append(f"<document id=\"{sid}\" title=\"{ar['title']}\" pages_included=\"{len(chosen)} of {len(pages)}\">\n{body}\n</document>")
                add_source(sid, "annual_report", ar["title"], ar["url"], str(ar["year"] or ""))
        except Exception as e:  # noqa: BLE001 - a missing document is reported, not fatal
            parts.append(f"<note>Annual report could not be read: {str(e)[:120]}</note>")

    calls = [c for c in docs["concalls"] if c.get("transcript")]
    for idx, call in enumerate(calls[:2]):
        try:
            text = "\n".join(fetch.pdf_pages(call["transcript"], max_pages=80))
        except Exception:  # noqa: BLE001
            continue
        text = re.sub(r"\n{3,}", "\n\n", text)[: budgets["call" if idx == 0 else "prev_call"]]
        if len(text) > 500:
            sid = f"CALL {call['date']}"
            parts.append(f"<document id=\"{sid}\" title=\"Earnings call transcript {call['date']}\">\n{text}\n</document>")
            add_source(sid, "transcript", f"Earnings call transcript, {call['date']}", call["transcript"], call["date"])
    ppt = next((c for c in docs["concalls"] if c.get("ppt")), None)
    if ppt:
        try:
            text = "\n".join(fetch.pdf_pages(ppt["ppt"], max_pages=60))[: budgets["ppt"]]
            if len(text) > 300:
                sid = f"PPT {ppt['date']}"
                parts.append(f"<document id=\"{sid}\" title=\"Investor presentation {ppt['date']}\">\n{text}\n</document>")
                add_source(sid, "presentation", f"Investor presentation, {ppt['date']}", ppt["ppt"], ppt["date"])
        except Exception:  # noqa: BLE001
            pass
    if docs["credit_ratings"]:
        rating = docs["credit_ratings"][0]
        try:
            text = fetch.html_text(rating["url"])[: budgets["rating"]]
            if len(text) > 300:
                parts.append(f"<document id=\"RATING\" title=\"{rating['title']}\">\n{text}\n</document>")
                add_source("RATING", "credit_rating", rating["title"], rating["url"])
        except Exception:  # noqa: BLE001
            pass
    if docs["announcements"]:
        parts.append("<document id=\"ANNOUNCEMENTS\" title=\"Latest exchange announcements (titles only)\">\n"
                     + "\n".join(f"- {a['title']}" for a in docs["announcements"]) + "\n</document>")
    if board:
        keep = {k: board.get(k) for k in ("theme", "business", "moat_note", "import_substitution", "risk_note", "why_obscure",
                                          "verify_comment", "pricing_power_note", "guidance_quote", "guidance_source", "cwip_note",
                                          "warnings", "claim_grade", "verified") if board.get(k)}
        parts.append("<document id=\"BOARD\" title=\"This dashboard's earlier research notes (analyst notes, not company disclosure; verify against the documents)\">\n"
                     + json.dumps(keep, ensure_ascii=False) + "\n</document>")
    if previous:
        prev = {"period": previous.get("period_label"), "summary": previous.get("summary"),
                "fair_values": (((previous.get("quant") or {}).get("valuation") or {}).get("dcf") or {}).get("upside_pct"),
                "red_flags": [f["title"] for f in (previous.get("quant") or {}).get("red_flags", []) if f["level"] != "green"]}
        parts.append("<document id=\"PREVIOUS REPORT\" title=\"Summary of last quarter's report on this company\">\n"
                     + json.dumps(prev, ensure_ascii=False) + "\n</document>")
    return "\n\n".join(parts), sources


# ------------------------------------------------------------------ Claude
def write_sections(pack: str, company_name: str, period_label: str, client=None) -> tuple[list[dict], dict, dict]:
    import anthropic  # imported here so --dry-run works without the package

    client = client or anthropic.Anthropic()
    sections, summary, usage = [], {}, {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "model": MODEL}
    context = [{"type": "text", "text": f"<pack company=\"{company_name}\" period=\"{period_label}\">\n{pack}\n</pack>", "cache_control": {"type": "ephemeral"}}]
    for p in PASSES:
        ids = list(p["sections"])
        tool = _tool(p["tool"], ids, p.get("extra"))
        brief = "\n".join(f"- {sid}: {desc}" for sid, desc in p["sections"].items())
        prompt = (f"Write these sections of the deep-dive report on {company_name} ({period_label}), in this order, using only the pack above. "
                  f"Each section has 1-5 subsections; each subsection has blocks.\n{brief}\n"
                  f"Submit them with the {p['tool']} tool.")
        data, budget = None, MAX_OUTPUT
        for attempt in range(2):
            try:
                with client.messages.stream(
                    model=MODEL, max_tokens=budget, system=SYSTEM, tools=[tool],
                    tool_choice={"type": "tool", "name": p["tool"]},
                    messages=[{"role": "user", "content": context + [{"type": "text", "text": prompt}]}],
                ) as stream:
                    msg = stream.get_final_message()
            except ValueError as e:   # json.JSONDecodeError included: a tool call cut off mid-JSON
                if attempt == 0:
                    budget = min(64000, budget * 2)
                    continue
                raise RuntimeError(f"{p['tool']}: unreadable tool output ({e})") from e
            for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                usage[k] += getattr(msg.usage, k, 0) or 0
            if msg.stop_reason == "max_tokens" and attempt == 0:
                budget = min(64000, budget * 2)   # truncated sections would be incomplete: write them again
                continue
            block = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
            if block is None:
                raise RuntimeError(f"{p['tool']}: model returned no tool call (stop_reason {msg.stop_reason})")
            data = block.input if isinstance(block.input, dict) else json.loads(block.input)
            if msg.stop_reason == "max_tokens":
                usage.setdefault("truncated", []).append(p["tool"])
            break
        if data is None:
            raise RuntimeError(f"{p['tool']}: no usable output")
        got = {s.get("id"): s for s in data.get("sections", []) if isinstance(s, dict)}
        sections += [got[i] for i in ids if i in got]
        if "summary" in data:
            summary = data["summary"]
    return sections, summary, usage
