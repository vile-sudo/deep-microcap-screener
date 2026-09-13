"""
Quarterly deep-dive research reports, one per board company, kept current
automatically.

    cd backend
    python scripts/deep_reports.py                   # today's batch (Claude Code login or CLAUDE_CODE_OAUTH_TOKEN)
    python scripts/deep_reports.py --codes SIKA,QLINE --force
    python scripts/deep_reports.py --dry-run --codes SIKA   # fetch documents + build the work folder only

Runs as a step of .github/workflows/daily.yml.

When a company is due
---------------------
  * it has no report yet (highest board score first), or
  * screener shows a newer results quarter than its latest report - and
    either an earnings-call transcript dated after that quarter is out, or
    WAIT_FOR_CALL_DAYS have passed since the new results were first seen
    (so the report reads what management said about the quarter).
At most --max-reports a day (default 2 with Claude Code), new results before first coverage,
so the ~390 companies are covered in a few weeks and then refresh as each one
files; a quarter's results arrive over about six weeks, which spreads the load.

What is written
---------------
  backend/reports/<CODE>/<FY27-Q1>.json   the report (every quarter is kept)
  backend/reports/index.json              latest report per company, for the dashboard
  automation/data/deep-reports.json       schedule state + token usage per report
  reports-pdf/<CODE>-<FY27-Q1>.pdf        rendered later by automation/render-report-pdfs.mjs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deep_report import claude_code, fetch, model, writer  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
COMPANIES = BACKEND / "data" / "companies_raw.json"
REPORTS = BACKEND / "reports"
INDEX = REPORTS / "index.json"
STATE = ROOT / "automation" / "data" / "deep-reports.json"
WAIT_FOR_CALL_DAYS = 21
# claude-code: Claude Code on a Pro/Max subscription (default); api: the paid Anthropic API
ENGINE = os.environ.get("REPORT_ENGINE") or "claude-code"
IST = timezone(timedelta(hours=5, minutes=30))
MONTH = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def month_index(label: str | None) -> int | None:
    try:
        m, y = label.split()
        return int(y) * 12 + MONTH[m]
    except (AttributeError, ValueError, KeyError):
        return None


def cost_usd(usage: dict) -> float | None:
    """Only when prices are configured (REPORT_PRICE_IN / _OUT, $ per million tokens)."""
    try:
        pin, pout = float(os.environ["REPORT_PRICE_IN"]), float(os.environ["REPORT_PRICE_OUT"])
    except (KeyError, ValueError):
        return None
    cache_w = usage.get("cache_creation_input_tokens", 0) * pin * 1.25
    cache_r = usage.get("cache_read_input_tokens", 0) * pin * 0.1
    return round((usage.get("input_tokens", 0) * pin + cache_w + cache_r + usage.get("output_tokens", 0) * pout) / 1e6, 3)


def due_list(companies, index, state, today, codes=None, force=False):
    by_code = {c["code"]: c for c in companies}
    if codes:
        return [by_code[c] for c in codes if c in by_code]
    new_results, first = [], []
    for c in companies:
        if c.get("screen") == "auto" and not c.get("final_score"):
            continue
        rep = index.get(c["code"])
        if not rep:
            first.append(c)
            continue
        latest = c.get("latest_results")
        if force or (latest and month_index(latest) and month_index(rep.get("results_quarter")) and month_index(latest) > month_index(rep["results_quarter"])):
            seen = state.setdefault("seen_new_results", {}).setdefault(c["code"], {"quarter": latest, "on": today})
            if seen.get("quarter") != latest:
                seen.update(quarter=latest, on=today)
            new_results.append((seen["on"], c))
    new_results.sort(key=lambda x: x[0])
    first.sort(key=lambda c: -(c.get("final_score") or 0))
    return [c for _, c in new_results] + first


def report_for(rec: dict, previous: dict | None, dry_run: bool, today: str) -> tuple[dict | None, str]:
    company = fetch.fetch_company([rec.get("code"), rec.get("nse_code"), rec.get("bse_code")])
    if not company:
        return None, "not on screener.in"
    quant = model.build(company)
    latest = quant["latest_quarter"]
    period = fetch.period_id(latest) or f"{today}"
    label = fetch.quarter_label(latest) or latest
    if previous and previous.get("results_quarter") == latest and not dry_run and os.environ.get("REPORT_FORCE") != "1":
        return None, f"already covers {latest}"
    # wait for the earnings call on the new quarter, up to WAIT_FOR_CALL_DAYS
    if previous and latest and os.environ.get("REPORT_FORCE") != "1":
        calls = [c for c in company["documents"]["concalls"] if c.get("transcript")]
        newest_call = month_index(calls[0]["date"]) if calls else None
        seen_on = rec.get("_seen_on")
        waited = seen_on and (date.fromisoformat(today) - date.fromisoformat(seen_on)).days >= WAIT_FOR_CALL_DAYS
        if not (newest_call and month_index(latest) and newest_call > month_index(latest)) and not waited:
            return None, f"waiting for the {label} earnings call transcript"
    engine = ENGINE
    if engine == "api":
        pack, sources = writer.build_pack(company, quant, rec, previous)
    else:
        pack, sources = "", []
    report = {
        "code": rec["code"], "name": company["name"] or rec.get("name"), "period": period, "period_label": label,
        "results_quarter": latest, "generated_at": datetime.now(IST).isoformat(timespec="minutes"),
        "basis": quant["basis"], "screener_url": company["url"], "sources": sources, "quant": quant,
        "pack_chars": len(pack),
    }
    if dry_run:
        if engine != "api":
            work, sources = claude_code.prepare(company, quant, rec, previous, label)
            report["sources"] = sources
            print(f"    work folder for Claude Code: {work}")
        report["sections"], report["summary"], report["usage"] = [], {}, {"dry_run": True}
        return report, "dry run"
    if engine == "api":
        sections, summary, usage = writer.write_sections(pack, report["name"], label)
        usage["cost_usd"] = cost_usd(usage)
    else:
        sections, summary, usage, sources = claude_code.write_report(company, quant, rec, previous, label,
                                                                     keep_workdir=os.environ.get("REPORT_KEEP_WORKDIR") == "1")
        report["sources"] = sources
        usage["cost_usd"] = None   # subscription usage, not billed per token
    report.update(model_id=f"{usage.get('engine', 'api')}:{usage.get('model')}", sections=sections, summary=summary, usage=usage)
    return report, "written"


def index_entry(r: dict, pdf_base: str | None) -> dict:
    dcf = r["quant"]["valuation"].get("dcf") or {}
    sc = dcf.get("scenarios") or {}
    return {
        "name": r["name"], "period": r["period"], "period_label": r["period_label"], "results_quarter": r["results_quarter"],
        "generated_at": r["generated_at"], "one_line": (r.get("summary") or {}).get("one_line"),
        "price": r["quant"]["valuation"].get("price"),
        "fair_value": {k: (sc.get(k) or {}).get("per_share") for k in ("bear", "base", "bull")} if dcf.get("available") else None,
        "flags": {lvl: sum(1 for f in r["quant"]["red_flags"] if f["level"] == lvl) for lvl in ("red", "amber", "green")},
        "periods": sorted({p.stem for p in (REPORTS / r["code"]).glob("*.json")} | {r["period"]}, reverse=True),
        "pdf": f"{pdf_base}/{r['code']}-{r['period']}.pdf" if pdf_base else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", help="comma-separated board codes (ignores the schedule)")
    ap.add_argument("--max-reports", type=int, default=int(os.environ.get("REPORTS_PER_DAY") or ("2" if ENGINE != "api" else "8")))
    ap.add_argument("--max-cost-usd", type=float, default=float(os.environ.get("REPORT_MAX_COST_USD", "0") or 0),
                    help="stop the run once this much has been spent (needs REPORT_PRICE_IN/_OUT)")
    ap.add_argument("--force", action="store_true", help="rewrite even if the latest quarter is already covered")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-list", help="write the codes of reports written today to this file (for the PDF step)")
    args = ap.parse_args()
    if args.force:
        os.environ["REPORT_FORCE"] = "1"
    if not args.dry_run and ENGINE == "api" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("deep-reports: REPORT_ENGINE=api but ANTHROPIC_API_KEY is not set - skipping")
        return 0
    if not args.dry_run and ENGINE != "api":
        if not claude_code.available():
            print("deep-reports: Claude Code CLI not installed - skipping")
            return 0
        if os.environ.get("GITHUB_ACTIONS") and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            print("deep-reports: CLAUDE_CODE_OAUTH_TOKEN secret is not set - skipping (run `claude setup-token` and add it to GitHub)")
            return 0

    today = datetime.now(IST).date().isoformat()
    companies = load(COMPANIES, [])
    index = load(INDEX, {})
    state = load(STATE, {"runs": [], "seen_new_results": {}, "reports": {}})
    codes = [c.strip() for c in args.codes.split(",")] if args.codes else None
    pdf_base = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/releases/download/reports" if os.environ.get("GITHUB_REPOSITORY") else None

    queue = due_list(companies, index, state, today, codes, args.force)
    print(f"deep-reports: {len(queue)} due, writing at most {args.max_reports} (engine {ENGINE}, model {claude_code.MODEL if ENGINE != 'api' else writer.MODEL})")
    written, spent, outcomes = [], 0.0, {}
    for rec in queue:
        if len(written) >= args.max_reports:
            break
        if args.max_cost_usd and spent >= args.max_cost_usd:
            print(f"deep-reports: cost cap ${args.max_cost_usd} reached")
            break
        code = rec["code"]
        seen = state.get("seen_new_results", {}).get(code)
        rec = {**rec, "_seen_on": seen["on"] if seen else None}
        prev_entry = index.get(code)
        previous = load(REPORTS / code / f"{prev_entry['period']}.json", None) if prev_entry else None
        t0 = time.time()
        try:
            report, why = report_for(rec, previous, args.dry_run, today)
        except claude_code.UsageLimitReached as e:
            outcomes[code] = f"paused: usage limit ({e})"
            print(f"  {code}: Claude usage limit reached - stopping; the batch resumes on the next run")
            break
        except Exception as e:  # noqa: BLE001 - one company's failure never stops the batch
            traceback.print_exc()
            outcomes[code] = f"error: {str(e)[:160]}"
            print(f"  {code}: ERROR {e}")
            continue
        outcomes[code] = why
        if not report:
            print(f"  {code}: skipped - {why}")
            continue
        u = report.get("usage", {})
        print(f"  {code}: {why} {report['period_label']} in {time.time()-t0:.0f}s, pack {report['pack_chars']:,} chars, "
              f"{len(report.get('sections', []))} sections, tokens in {u.get('input_tokens', 0)+u.get('cache_read_input_tokens', 0)+u.get('cache_creation_input_tokens', 0):,} / out {u.get('output_tokens', 0):,}"
              + (f", ${u['cost_usd']}" if u.get("cost_usd") is not None else "")
              + (f", {u['sessions']} Claude Code session(s), {u['turns']} turns" if u.get("sessions") else ""))
        if args.dry_run:
            out = ROOT / "reports-dry" / f"{code}-{report['period']}.json"
            save(out, report)
            continue
        spent += u.get("cost_usd") or 0
        save(REPORTS / code / f"{report['period']}.json", report)
        index[code] = index_entry(report, pdf_base)
        state.setdefault("reports", {})[code] = {"period": report["period"], "on": today, "engine": u.get("engine", "api"), "tokens_out": u.get("output_tokens"),
                                                 "cost_usd": u.get("cost_usd"), "api_equivalent_usd": u.get("estimated_api_cost_usd"), "minutes": round((time.time() - t0) / 60, 1)}
        state.get("seen_new_results", {}).pop(code, None)
        written.append(f"{code}:{report['period']}")
        save(INDEX, dict(sorted(index.items())))   # saved as it goes, so a timeout keeps finished reports

    if not args.dry_run:
        state["runs"] = (state.get("runs", []) + [{"on": today, "written": written, "spent_usd": round(spent, 2) if spent else None,
                                                   "skipped": {k: v for k, v in outcomes.items() if not v.startswith("written")}}])[-90:]
        save(STATE, state)
    if args.out_list:
        Path(args.out_list).write_text("\n".join(written) + ("\n" if written else ""), encoding="utf-8")
    print(f"deep-reports: wrote {len(written)}: {', '.join(written) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
