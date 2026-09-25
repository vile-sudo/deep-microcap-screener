"""Management guidance from earnings-call transcripts, and whether it came true -- India board.

    cd backend
    python scripts/run_guidance_in.py                    # read up to --budget new transcripts, re-check every target
    python scripts/run_guidance_in.py --budget 150
    python scripts/run_guidance_in.py --codes BEL,ACCENTMIC --budget 20
    python scripts/run_guidance_in.py --verify-only      # no downloads: re-check targets against the latest results

WHAT IT DOES
1. Takes each board company's earnings-call list (from scripts/run_results_in.py: the transcript PDFs
   screener.in links to, mostly the BSE filings) and reads each transcript once: newest calls first, then
   older ones, at most --budget a run, so all ~455 companies fill in over a few daily runs.
2. Pulls out the forward-looking sentences where management states a NUMBER for the future -- revenue
   growth of 15%+, an EBITDA margin of 18-20%, revenue of Rs 5,000 crore, capex of Rs 1,200 crore, order inflow
   of Rs 55,000 crore -- with the time frame it named (FY27, "this year", "next three years") and the quote.
3. Later, when the year it was about has been reported, compares the target with what happened (annual
   figures from run_results_in.py) and marks it Met / Narrowly missed / Missed. Targets that cannot be checked
   from the numbers (capex, order inflow, multi-year ambitions, no time frame) are kept and labelled as such,
   never counted for or against management.

It is rule-based text reading, not a person: it can miss a target or, rarely, misread one, which is why every
row shows the exact sentence and links the transcript. Written to data/guidance_in/latest.json
(GET /api/guidance-in). A transcript that cannot be downloaded or has no text layer is recorded and not retried
for 30 days.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW = BACKEND_DIR / "data" / "companies_raw.json"
RESULTS = BACKEND_DIR / "data" / "results_in" / "latest.json"
OUT_FILE = BACKEND_DIR / "data" / "guidance_in" / "latest.json"
# BSE and NSE serve their filings to browsers; a UA that names a research script gets a 403 from BSE's firewall
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Referer": "https://www.bseindia.com/"}
DELAY = 1.0
MAX_PAGES = 120
MAX_STATEMENTS_PER_CALL = 10
RETRY_DAYS = 30
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
PCT_WORD = r"(?:%|percent|per cent|pc)"
RANGE = re.compile(NUM + r"\s*" + PCT_WORD + r"?\s*(?:-|–|—|to)\s*" + NUM + r"\s*" + PCT_WORD, re.I)
SINGLE = re.compile(NUM + r"\s*" + PCT_WORD, re.I)
_UNIT = r"(lakh\s+crores?|crores?|crs?\b|cr\b|billion|bn\b|million|mn\b)"
MONEY_RANGE = re.compile(r"(?:inr|rs\.?|₹)\s*" + NUM + r"\s*(?:" + _UNIT[1:-1] + r")?\s*(?:-|–|—|to)\s*(?:inr|rs\.?|₹)?\s*" + NUM + r"\s*" + _UNIT, re.I)
MONEY = re.compile(r"(?:inr|rs\.?|₹)\s*" + NUM + r"\s*(lakh\s+crores?|crores?|crs?\b|cr\b|billion|bn\b|million|mn\b)", re.I)
FORWARD = re.compile(r"\b(expect\w*|guid(?:e|ed|es|ing|ance)|target\w*|aim\w*|anticipat\w+|project\w*|plan(?:s|ning|ned)?|on track|will|would|should|"
                     r"looking (?:at|to|for)|going to|confident|endeavou?r\w*|retain(?:s|ing)?|maintain(?:s|ing)?|budget\w*|hope\w*|likely)\b", re.I)
STRONG_FWD = re.compile(r"\b(expect\w*|guid(?:e|ed|es|ing|ance)|target\w*|aim\w*|anticipat\w+|project\w*|will|on track|confident|retain(?:s|ing)?|maintain(?:s|ing)?|budget\w*)\b", re.I)
# an analyst's question or a comparison with past guidance, not a statement of a target
QUESTION_LIKE = re.compile(r"\b(question|given that|could you|can you|would you|may i|sir|madam|higher than|lower than|compared (?:to|with)|as against|versus|guided number|thank you)\b", re.I)
PAST = re.compile(r"\b(grew|achieved|reported|delivered|registered|clocked|clocking|posted|recorded|saw|was|were|had been|reached|closed|stood at|came in|ended)\b", re.I)
# a percentage right after these words is a cost ratio, tax rate, payout... not a business target
NOISY_BEFORE = re.compile(r"(employee|cost|expense|tax|dividend|payout|debt|interest|utili[sz]ation|depreciation|discount|inflation|gst|duty|commission|rate)\w*\W+(?:\w+\W+){0,4}$", re.I)
# a target for one part of the business, not the company: kept out rather than mis-checked against company totals
PARTIAL = re.compile(r"\b(segment|division|vertical|export|exports|domestic|product|products|category|region|geograph\w*|subsidiary|plant|unit|customer|business line|contribution)\b", re.I)
GUIDANCE_CTX = re.compile(r"guid|committed|retain|maintain|year[- ]end|start(?:ed)? the year|for the year|this year|current year|on track", re.I)
FIRST_PERSON = re.compile(r"\b(we|our|us|i|company|management)\b", re.I)
CMP_MIN = re.compile(r"(more than|greater than|at least|over|above|minimum|upwards of|north of|in excess of|exceed\w*|≥|\+)\s*$", re.I)
CMP_MAX = re.compile(r"(up to|less than|below|under|maximum|at most|not more than|within)\s*$", re.I)
CMP_APPROX = re.compile(r"(around|about|approximately|approx\.?|roughly|close to|almost|nearly|~|circa)\s*$", re.I)

REV_NOUN = r"(?:revenue|revenues|sales|top ?line|turnover)"
PROFIT_NOUN = r"(?:pat|net profit|profit after tax|profits|earnings|ebitda|ebidta)"
GROWTH = r"(?:grow\w*|growth|increase\w*|cagr|expan\w*|rise|rising)"
MARGIN_KINDS = [("ebitda_margin", r"(?:ebitda|ebidta|operating|opm|ebit)\s*(?:profit\s*)?margins?"), ("pat_margin", r"(?:pat|net|net profit)\s*margins?"),
                ("gross_margin", r"gross\s*(?:profit\s*)?margins?")]
AMOUNT_KINDS = [("revenue_amount", REV_NOUN), ("capex", r"(?:capex|capital expenditure|capital investment|capital outlay)"),
                ("order_inflow", r"order\s*(?:inflow|intake|book|pipeline|receipt|win)s?")]


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def fy_current(call_ym: str) -> int:
    """Indian fiscal year (ends March) a call in 'YYYY-MM' is talking about when it says 'this year'."""
    y, m = int(call_ym[:4]), int(call_ym[5:7])
    return y + 1 if m >= 4 else y


def find_horizon(s: str, call_ym: str) -> dict | None:
    m = re.search(r"\bfy\s*['’]?\s*(20)?(\d{2})\s*(?:[-–/]\s*(?:20)?(\d{2}))?", s, re.I)
    if m:
        a, b = int(m.group(2)), m.group(3)
        yr = int(b) if b else a                     # FY27 -> 27; FY 2026-27 -> 27
        return {"fy": 2000 + yr}
    m = re.search(r"financial year\s*(20\d{2})\s*(?:[-–/]\s*(\d{2,4}))?", s, re.I)
    if m:
        end = m.group(2)
        return {"fy": int(end) + (2000 if len(end) == 2 else 0) if end else int(m.group(1))}
    if re.search(r"\bq[1-4]\s*fy|\bh[12]\s*fy|\b(?:first|second|third|fourth) quarter\b|\bnext quarter\b|\bcoming quarter\b", s, re.I):
        return {"quarter": True}
    if re.search(r"next (?:two|three|four|five|2|3|4|5|\d+)\s*(?:to\s*\w+\s*)?years?|over the (?:next|medium|long)|medium[- ]term|long[- ]term|cagr|coming (?:two|three|five|2|3|5) years|3[-–]5 years", s, re.I):
        return {"multi": True}
    if re.search(r"coming years|upcoming (?:times|years)|next few years|couple of years|in the years ahead", s, re.I):
        return {"multi": True}
    if re.search(r"\b(?:this|current|ongoing) (?:financial )?(?:year|fiscal)|for the year\b|current fy\b|for fy\b", s, re.I):
        return {"fy": fy_current(call_ym)}
    if re.search(r"\b(?:next|coming|following) (?:financial )?(?:year|fiscal)\b", s, re.I):
        return {"fy": fy_current(call_ym) + 1}
    return None


def cmp_before(text: str, start: int) -> str:
    head = text[max(0, start - 28):start]
    return "min" if CMP_MIN.search(head) else "max" if CMP_MAX.search(head) else "approx" if CMP_APPROX.search(head) else "about"


def pct_after(s: str, pos: int, window: int = 90):
    """(low, high, cmp) of the first percent (or range) within `window` chars after pos, else None."""
    seg = s[pos:pos + window]
    m = RANGE.search(seg)
    m2 = SINGLE.search(seg)
    if m and (not m2 or m.start() <= m2.start() + 1):
        lo, hi = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
        return min(lo, hi), max(lo, hi), cmp_before(s, pos + m.start())
    if m2:
        v = float(m2.group(1).replace(",", ""))
        return v, v, cmp_before(s, pos + m2.start())
    return None


def to_crore(val: float, unit: str) -> float:
    u = unit.lower()
    if "lakh" in u:
        return val * 100000
    if u.startswith("bill") or u.startswith("bn"):
        return val * 100
    if u.startswith("mill") or u.startswith("mn"):
        return val / 10
    return val


def _noisy(low: str) -> bool:
    """True when the first percentage in the sentence sits right after a cost/tax/payout style word."""
    m = RANGE.search(low) or SINGLE.search(low)
    return bool(m and NOISY_BEFORE.search(low[max(0, m.start() - 60):m.start()]))


def extract(text: str, call_ym: str, url: str) -> list[dict]:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(?<=\d)\s(?=\d\s?(?:%|percent|per cent))", "", text)      # the PDF's "2 8 %" is 28%
    sentences = re.split(r"(?<=[.?!])\s+(?=[A-Z0-9])", text)
    out, seen = [], set()

    def add(metric, lo, hi, cmp, unit, s, horizon):
        key = (metric, round(lo, 2), round(hi, 2), json.dumps(horizon, sort_keys=True))
        if key in seen:
            return
        seen.add(key)
        out.append({"id": hashlib.sha1((url + "|" + "|".join(map(str, key))).encode()).hexdigest()[:12], "call": call_ym, "metric": metric,
                    "low": lo, "high": hi, "cmp": cmp, "unit": unit, "horizon": horizon, "quote": s.strip()[:300], "source": url})

    for s in sentences:
        if len(s) < 40 or len(s) > 700 or "?" in s or not FIRST_PERSON.search(s) or not FORWARD.search(s) or QUESTION_LIKE.search(s):
            continue
        if PAST.search(s) and not STRONG_FWD.search(s):
            continue
        low = s.lower()
        horizon = find_horizon(s, call_ym)
        if horizon is None and GUIDANCE_CTX.search(s):        # "we retain revenue growth of more than 15%": this year's guidance
            horizon = {"fy": fy_current(call_ym)}
        if horizon and horizon.get("fy") and horizon["fy"] < fy_current(call_ym) and not re.search(r"expect\w*|guid\w*|target\w*|will\b", s, re.I):
            continue                                          # a year already over, no forward word: results, not a target

        # growth targets: revenue / profit growth with a percentage after the growth word
        for noun_re, metric in ((REV_NOUN, "revenue_growth"), (PROFIT_NOUN, "profit_growth")):
            if re.search(noun_re, low) and re.search(GROWTH, low) and not PARTIAL.search(low):
                g = re.search(GROWTH, low)
                hit = pct_after(low, g.start(), 60)
                if hit is None:                                          # "15% growth in revenue"
                    m = RANGE.search(low) or SINGLE.search(low)
                    if m and m.end() <= g.start() and g.start() - m.end() <= 25:
                        hit = pct_after(low, max(0, m.start() - 1), 45)
                if hit and hit[1] <= 300 and not _noisy(low):
                    add(metric, hit[0], hit[1], hit[2], "%", s, horizon)
                    break
        # margin targets
        for metric, mre in MARGIN_KINDS:
            m = re.search(mre, low)
            if m and not PARTIAL.search(low):
                hit = pct_after(low, m.end(), 70)
                if hit and hit[1] <= 100 and not _noisy(low):
                    add(metric, hit[0], hit[1], hit[2], "%", s, horizon)
                    break
        # amounts in rupees (revenue, capex, order inflow)
        for metric, nre in AMOUNT_KINDS:
            for mm in re.finditer(nre, low):
                seg = s[mm.end():mm.end() + 110]
                rng, money = MONEY_RANGE.search(seg), MONEY.search(seg)
                hit = rng if rng and (not money or rng.start() <= money.start()) else money
                if hit and not re.search(r"\b(usd|us\$|\$|dollar|growth|margin|order|capex|profit|ebitda|debt|cash|investment|expenditure)\b", seg[:hit.start()], re.I):
                    if hit is rng:
                        unit = rng.group(rng.lastindex)
                        a, z = to_crore(float(rng.group(1).replace(",", "")), unit), to_crore(float(rng.group(rng.lastindex - 1).replace(",", "")), unit)
                        add(metric, min(a, z), max(a, z), "about", "₹ cr", s, horizon)
                    else:
                        val = to_crore(float(money.group(1).replace(",", "")), money.group(2))
                        add(metric, val, val, cmp_before(seg, money.start()), "₹ cr", s, horizon)
                    break
    # closing "guidance" remarks come last in a call and are the highest-value sentences: keep those first
    out.sort(key=lambda x: (0 if re.search(r"guid", x["quote"], re.I) else 1, 0 if x["metric"] in ("revenue_growth", "ebitda_margin", "profit_growth", "revenue_amount") else 1))
    return out[:MAX_STATEMENTS_PER_CALL]


def pdf_text(content: bytes) -> str:
    from pypdf import PdfReader
    rd = PdfReader(io.BytesIO(content))
    parts = []
    for i, page in enumerate(rd.pages):
        if i >= MAX_PAGES:
            break
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page must not lose the call
            continue
    return re.sub(r"\s+", " ", " ".join(parts))


def call_ym(label: str) -> str | None:
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", label or "")
    return f"{m.group(2)}-{MONTHS[m.group(1).lower()]:02d}" if m and m.group(1).lower() in MONTHS else None


# ------------------------------------------------------------------ checking a target against what happened
def _fy_index(annual: dict, fy: int) -> int | None:
    periods = annual.get("periods") or []
    return periods.index(f"Mar {fy}") if f"Mar {fy}" in periods else None


def _val(annual: dict, key: str, i: int | None):
    arr = annual.get(key) or []
    return arr[i] if i is not None and 0 <= i < len(arr) else None


def verify(st: dict, annual: dict | None) -> dict:
    """{'status', 'actual', ...} for one statement. Status: met | narrow_miss | missed (checked), or
    pending | long_term | no_horizon | quarter | not_checkable (not held against management)."""
    h, metric = st.get("horizon"), st["metric"]
    if not h:
        return {"status": "no_horizon"}
    if h.get("multi"):
        return {"status": "long_term"}
    if h.get("quarter"):
        return {"status": "quarter"}
    if metric in ("capex", "order_inflow", "gross_margin"):
        return {"status": "not_checkable"}
    fy = h.get("fy")
    if not fy or not annual:
        return {"status": "pending"}
    i = _fy_index(annual, fy)
    if i is None:
        return {"status": "pending"}
    sales, prof, opm = _val(annual, "sales", i), _val(annual, "net_profit", i), _val(annual, "opm", i)
    prev_sales, prev_prof = _val(annual, "sales", i - 1), _val(annual, "net_profit", i - 1)
    actual, pp = None, False            # pp: the target is a level in percentage points (margins), so the tolerance is in points
    if metric == "revenue_growth" and sales and prev_sales:
        actual = (sales / prev_sales - 1) * 100
    elif metric == "profit_growth" and prof is not None and prev_prof and prev_prof > 0:
        actual = (prof / prev_prof - 1) * 100
    elif metric == "ebitda_margin" and opm is not None:
        actual, pp = opm, True
    elif metric == "pat_margin" and prof is not None and sales:
        actual, pp = prof / sales * 100, True
    elif metric == "revenue_amount" and sales is not None and 0.5 <= st["low"] / max(prev_sales or sales, 1e-9) <= 2.2:
        actual = sales                       # only a target of company-total size; a segment's figure is not held against the company
    if actual is None:
        return {"status": "not_checkable"}
    lo, hi, cmp = st["low"], st["high"], st.get("cmp", "about")
    res = {"actual": round(actual, 1), "fy": fy}
    if cmp == "max":
        res["status"] = "met" if actual <= hi else "missed"
        return res
    target = lo
    if cmp == "approx":
        band = 0.15 * max(abs(target), 1e-9)
        res["status"] = "met" if actual >= target - band else ("narrow_miss" if actual >= target - 2 * band else "missed")
        return res
    tol = 0.6 if pp else (0.03 * abs(target) if metric == "revenue_amount" else 1.0)   # points for margins/growth, 3% for a rupee target
    shortfall = target - actual
    if shortfall <= tol:
        res["status"] = "met"
    elif shortfall <= (3.0 if pp else 0.2 * abs(target)):
        res["status"] = "narrow_miss"
    else:
        res["status"] = "missed"
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=150)
    ap.add_argument("--codes", default="")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    board = [c["code"] for c in load_json(RAW, [])]
    if args.codes:
        want = {c.strip().upper() for c in args.codes.split(",") if c.strip()}
        board = [c for c in board if c.upper() in want]
    results = (load_json(RESULTS, {}).get("companies")) or {}
    data = load_json(OUT_FILE, {})
    companies = data.get("companies") or {}
    before = json.dumps(companies, sort_keys=True)          # to rewrite the file only if this run changed something
    now = datetime.now(timezone.utc)

    # the work list: newest un-read call of each company first, then the second newest, ...
    work = []
    if not args.verify_only:
        retry_before = (now - timedelta(days=RETRY_DAYS)).isoformat(timespec="seconds")
        for code in board:
            done = (companies.get(code) or {}).get("calls") or {}
            for rank, call in enumerate((results.get(code) or {}).get("concalls") or []):
                prev = done.get(call["transcript"])
                if prev and (not prev.get("error") or (prev.get("at") or "") > retry_before):
                    continue
                if call_ym(call["date"]):
                    work.append((rank, code, call))
        work.sort(key=lambda w: (w[0], w[1]))
        work = work[: args.budget]

    session = requests.Session()
    session.headers.update(HEADERS)
    read = failed = 0
    for rank, code, call in work:
        ent = companies.setdefault(code, {"calls": {}, "statements": []})
        rec = {"date": call["date"], "at": now.isoformat(timespec="seconds")}
        try:
            r = session.get(call["transcript"], timeout=90)
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                rec["error"] = f"HTTP {r.status_code}" if r.status_code != 200 else "not a PDF"
            else:
                text = pdf_text(r.content)
                if len(text) < 3000:
                    rec["error"] = "no readable text"
                else:
                    ym = call_ym(call["date"])
                    sts = extract(text, ym, call["transcript"])
                    ent["statements"] = [s for s in ent["statements"] if s["source"] != call["transcript"]] + sts
                    rec["statements"] = len(sts)
                    read += 1
        except Exception as e:  # noqa: BLE001 - a bad PDF or network hiccup skips that call, never the run
            rec["error"] = str(e)[:80]
        if rec.get("error"):
            failed += 1
        ent["calls"][call["transcript"]] = rec
        time.sleep(DELAY)

    # re-check every target against the latest reported years
    checked = {"met": 0, "narrow_miss": 0, "missed": 0}
    for code in board:
        ent = companies.get(code)
        if not ent:
            continue
        annual = (results.get(code) or {}).get("annual")
        for st in ent["statements"]:
            st["verify"] = verify(st, annual)
            if st["verify"]["status"] in checked:
                checked[st["verify"]["status"]] += 1
        ent["statements"].sort(key=lambda s: (s["call"], s["id"]), reverse=True)

    if json.dumps(companies, sort_keys=True) != before:   # rewrite (and so commit) the file only when something changed
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUT_FILE.write_text(json.dumps({"fetched_at": now.isoformat(timespec="seconds"), "companies": companies}, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    n = sum(len(e["statements"]) for e in companies.values())
    print(f"run_guidance_in: {read} transcripts read ({failed} unreadable); {n} targets on file for {sum(1 for e in companies.values() if e['statements'])} companies; "
          f"checked so far: {checked['met']} met, {checked['narrow_miss']} narrowly missed, {checked['missed']} missed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
