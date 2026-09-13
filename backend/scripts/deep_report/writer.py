"""
The analysis half of a deep-dive report, written by Claude from the source
documents and the numbers model.py computed.

Four passes (the parts in sections.py) over one cached document pack,
each returning its sections through a forced tool call, so the output is
always valid, schema-shaped JSON. The rules the model works under are in
SYSTEM below: label every claim, cite the document, never invent a number,
no buy/sell rating and no price target of its own.
"""
from __future__ import annotations

import json
import os
import re

from . import fetch, sections as S

MODEL = os.environ.get("REPORT_MODEL", "claude-sonnet-5")
MAX_OUTPUT = int(os.environ.get("REPORT_MAX_OUTPUT_TOKENS", "16000"))

SYSTEM = ("You are a skeptical buy-side equity analyst writing a forensic deep-dive research report "
          "on an Indian listed small/micro-cap company.\n\n" + S.RULES)

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


SUMMARY_SCHEMA = {"type": "object", "properties": {
    "one_line": {"type": "string", "description": S.SUMMARY_SPEC["one_line"]},
    **{k: {"type": "array", "items": {"type": "string"}, "description": v} for k, v in S.SUMMARY_SPEC.items() if k != "one_line"},
}, "required": list(S.SUMMARY_SPEC)}

PASSES = [{"tool": f"submit_{pid}", "sections": secs} for pid, _, secs in S.PARTS]
PASSES[-1]["extra"] = {"summary": SUMMARY_SCHEMA}


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
