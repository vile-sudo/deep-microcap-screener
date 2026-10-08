"""Earnings-call transcripts, laid out for reading -- India board.

    cd backend
    python scripts/run_transcripts_in.py                  # read up to --budget new transcripts
    python scripts/run_transcripts_in.py --codes BEL,HIRECT --budget 10

Each board company's newest KEEP_CALLS earnings calls (the list scripts/run_results_in.py takes from
screener.in: mostly the BSE/NSE transcript filings) are downloaded once and turned from a PDF into a
structured transcript: who spoke (with their title from the participant list, or the analyst's firm from
the moderator's introduction), each turn as numbered paragraphs, where the Q&A begins, and which
quarter it was. The cover letter, the page headers/footers and the "Page x of y" lines are dropped.

One gzip file per company, data/transcripts_in/<code>.json.gz (GET /api/transcripts-in/<code>), plus
data/transcripts_in/index.json listing each company's readable calls (GET /api/transcripts-in). Calls that
drop out of the newest KEEP_CALLS are removed; a call that can't be downloaded or read is recorded in the
index's `failed` map and not retried for RETRY_DAYS.

It reads the layout by rule (the "Name: text" convention almost every Indian transcript uses), so a
transcript in an unusual layout is kept as plain paragraphs without speakers rather than dropped; the
original PDF is always linked.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW = BACKEND_DIR / "data" / "companies_raw.json"
RESULTS = BACKEND_DIR / "data" / "results_in" / "latest.json"
OUT_DIR = BACKEND_DIR / "data" / "transcripts_in"
INDEX = OUT_DIR / "index.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Referer": "https://www.bseindia.com/"}
KEEP_CALLS = 4
DELAY = 1.0
MAX_PAGES = 80
RETRY_DAYS = 30
SAVE_EVERY = 25
PARSER_VERSION = 2          # bump when the layout rules improve: stored calls are read again
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

# "Abhishek Somany: Yes. Good afternoon..." -- a turn starts with a name and a colon at the start of a line
TURN = re.compile(r"^\s*((?:(?:Mr|Ms|Mrs|Dr|Shri)\.?\s+)?[A-Z][A-Za-z.'’\-]*(?:\s+[A-Z(][A-Za-z.'’\-)]*){0,5})\s*[:：]\s*(.*)$")
NOT_A_NAME = re.compile(r"^(?:note|notes|disclaimer|management|participants?|subject|sub|ref|date|time|encl|"
                        r"q\d|fy\d*|total|revenue|ebitda|pat|page|website|email|tel|phone|regd|cin|scrip.*|symbol|"
                        r"the|this|we|our|so|and|but|also|yes|no|ok|okay|thank you|thanks|for|in|on|as|at|answer|question)$", re.I)
TITLE_ENTRY = re.compile(r"\b(?:MR|MS|MRS|DR|SHRI|SMT)\.?\s+([A-Z][A-Z.'\- ]+?)\s*[–—\-:,]\s*(.+?)(?=\s+\b(?:MR|MS|MRS|DR|SHRI|SMT)\.?\s+[A-Z]|\s+MODERATOR\b|\s+MANAGEMENT\b|$)")
ANALYST_INTRO = re.compile(r"(?:line of|from|is)\s+(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?)?\s*([A-Z][\w.'’\-]+(?:\s+[A-Z][\w.'’\-]+){0,3})\s+"
                           r"(?:from|of|with)\s+([A-Z][\w&.'’\-]*(?:\s+(?:[A-Z&][\w&.'’\-]*|and|of|de))*)")
QA_START = re.compile(r"first question|question[- ]and[- ]answer|question[- ]answer|begin the q|open the floor|Q\s*&\s*A session|queue for questions|"
                      r"open (?:the )?(?:line|call) for (?:the )?q", re.I)
QUARTER = re.compile(r"\b(?:Q([1-4])|([1-4])(?:st|nd|rd|th)?\s+quarter)\s*(?:of\s+)?(?:FY|F\.Y\.|financial year)?\s*'?[ -]?(\d{2,4})(?:\s*[-‐–/]\s*'?(\d{2,4}))?", re.I)
H1 = re.compile(r"\b(H[12])\s*(?:FY)\s*'?(\d{2,4})(?:\s*[-‐–/]\s*'?(\d{2,4}))?", re.I)
INLINE_TURN = re.compile(r"\s(?=(?:(?:Mr|Ms|Mrs|Dr)\.\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}(?:\s+\([A-Za-z ]{3,30}\))?|Moderator):\s)")
CALL_DATE_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|August|September|October|November|December),?\s+(20\d{2})\b")
INTRO_TITLE = re.compile(r"\b(?:Mr|Ms|Mrs|Dr)\.?\s+([A-Z][\w.'’\-]+(?:\s+[A-Z][\w.'’\-]+){0,3})\s*\(([^()]{3,60})\)")
CALL_DATE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(20\d{2})\b")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def call_ym(label: str) -> str | None:
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", label or "")
    return f"{m.group(2)}-{MONTHS.index(m.group(1).lower()) + 1:02d}" if m and m.group(1).lower() in MONTHS else None


def quarter_from_month(ym: str) -> str:
    """The quarter a call in this month most likely discusses: the one that ended just before it."""
    y, m = int(ym[:4]), int(ym[5:])
    if m <= 3:
        return f"Q3 FY{y % 100:02d}"
    if m <= 6:
        return f"Q4 FY{y % 100:02d}"
    if m <= 9:
        return f"Q1 FY{(y + 1) % 100:02d}"
    return f"Q2 FY{(y + 1) % 100:02d}"


def _fy(a: str, b: str | None) -> int | None:
    v = int(b or a) % 100 if (b or a) else None
    return v


def quarter_label(head: str, ym: str) -> str:
    """'Q1 FY27' from the transcript's own title (first pages), else from the call month."""
    guess = quarter_from_month(ym)
    for m in QUARTER.finditer(head):
        q, fy = m.group(1) or m.group(2), _fy(m.group(3), m.group(4))
        if fy is not None and 10 <= fy <= 60:
            return f"Q{q} FY{fy:02d}"
    m = H1.search(head)
    if m:
        fy = _fy(m.group(2), m.group(3))
        if fy is not None and 10 <= fy <= 60:
            return f"{m.group(1).upper()} FY{fy:02d}"
    return guess


def page_lines(content: bytes) -> list[list[str]]:
    import logging

    from pypdf import PdfReader
    logging.getLogger("pypdf").setLevel(logging.ERROR)   # malformed-but-readable PDFs warn on every page
    rd = PdfReader(io.BytesIO(content))
    pages = []
    for i, page in enumerate(rd.pages):
        if i >= MAX_PAGES:
            break
        try:
            pages.append((page.extract_text() or "").replace("‐", "-").replace(" ", " ").splitlines())
        except Exception:  # noqa: BLE001 - one bad page must not lose the call
            pages.append([])
    return pages


def _norm(line: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", line).strip().lower())


def clean_lines(pages: list[list[str]]) -> list[str]:
    """All lines in order, without the running header/footer each page repeats."""
    n = len(pages)
    counts = Counter()
    for p in pages:
        for k in {_norm(l) for l in p if l.strip()}:
            counts[k] += 1
    repeated = {k for k, c in counts.items() if n >= 3 and c >= max(3, n * 0.5) and len(k) < 120}
    out = []
    for p in pages:
        for l in p:
            k = _norm(l)
            if not k or k in repeated or re.fullmatch(r"page # of #|page #|#|- # -|#/#", k):
                continue
            out.append(l.rstrip())
    return out


def _turn(line: str):
    """A "Name: text" line, but not the participant list's "MODERATOR: MR. OMKAR BAGWE – ..." """
    m = TURN.match(line)
    if m and re.match(r"(?:MR|MS|MRS|DR|SHRI)\.?\s+[A-Z]{2,}", m.group(2)):
        return None
    return m


def _is_name(name: str) -> bool:
    name = name.strip()
    if NOT_A_NAME.match(name) or len(name) > 48 or len(name) < 2:
        return False
    words = name.split()
    return all(w[0].isupper() or w[0] == "(" for w in words)


def parse(content: bytes, ym: str) -> dict:
    pages = page_lines(content)
    lines = clean_lines(pages)
    if lines and sum(map(len, lines)) / len(lines) > 300:
        # some PDFs give each page as one long line: start a new line at every "Mr. Name:" / "Moderator:"
        lines = [x for l in lines for x in INLINE_TURN.split(l) if x.strip()]
    text_all = "\n".join(lines)
    # the transcript proper starts at the first speaker turn; before it: cover letter + participant list
    cand = Counter()
    for l in lines:
        m = _turn(l)
        if m and _is_name(m.group(1)):
            cand[m.group(1).strip()] += 1
    names = {n for n, c in cand.items() if n.lower() == "moderator" or (len(n.split()) >= 2 and c >= 1) or c >= 2}
    start = next((i for i, l in enumerate(lines) if (m := _turn(l)) and m.group(1).strip() in names
                  and (m.group(1).strip().lower() == "moderator" or cand[m.group(1).strip()] >= 2)), None)
    preamble = " ".join(lines[:start] if start is not None else lines[:60])
    head = preamble + " " + " ".join(lines[start:start + 30] if start is not None else [])

    titles = {}
    pre = re.sub(r"\bM\s+R\.", "MR.", re.sub(r"\bM\s+S\.", "MS.", re.sub(r"\s+", " ", preamble)))
    for m in TITLE_ENTRY.finditer(pre):
        nm = re.sub(r"\s+", " ", m.group(1)).strip().upper()
        t = re.split(r"\s*[–—]\s*|\s+-\s+", m.group(2).strip())[0].strip(" –—-,")
        if nm and t and len(t) < 90:
            titles[nm] = t.title().replace("And ", "and ").replace("Of ", "of ")
    host = re.search(r"MODERATOR\s*:?\s*(?:MR|MS|MRS|DR)\.?\s+([A-Z][A-Z.'\- ]+?)\s*[–—\-]", pre)
    host = re.sub(r"\s+", " ", host.group(1)).strip().upper() if host else None

    turns: list[dict] = []
    if start is not None:
        cur = None
        for l in lines[start:]:
            m = _turn(l)
            if m and m.group(1).strip() in names:
                cur = {"s": m.group(1).strip(), "p": [m.group(2).strip()]}
                turns.append(cur)
                continue
            if cur is None:
                continue
            s = l.strip()
            if not s:
                continue
            prev = cur["p"][-1]
            new_para = l.startswith(" ") and re.search(r"[.?!:\"”’)]$", prev or "")
            if new_para or not prev:
                cur["p"].append(s)
            elif prev.endswith("-") and not prev.endswith(" -"):
                cur["p"][-1] = prev + s
            else:
                cur["p"][-1] = prev + " " + s
        for t in turns:
            t["p"] = [q for p in t["p"] if p.strip() for q in _split_long(re.sub(r"\s+", " ", p).replace(" -looking", "-looking").strip())]
        turns = [t for t in turns if t["p"]]

    if len(turns) < 4:   # not the usual layout: keep the text as plain paragraphs
        body = re.split(r"\n(?=\s)", "\n".join(lines[start or 0:]))
        turns = [{"s": "", "p": [q for b in body if len(b.strip()) > 2 for q in _split_long(re.sub(r"\s+", " ", b).strip())]}]
        return {"label": quarter_label(head, ym), "call_date": _call_date(head), "structured": False,
                "speakers": {}, "qa_start": None, "turns": turns}

    # roles: moderator, management (named in the participant list or speaking before the Q&A), analysts
    firms = {}
    qa_start = None
    # some calls run on a video platform with a named host and no "Moderator": the first speaker hosts
    chair = next((t["s"].lower() for t in turns if "moderator" in t["s"].lower()), turns[0]["s"].lower())
    for i, t in enumerate(turns):
        if t["s"].lower() == chair or (host and t["s"].upper() == host):
            txt = " ".join(t["p"])
            if qa_start is None and QA_START.search(txt) and i > 1:
                qa_start = i
            for m in ANALYST_INTRO.finditer(txt):
                firm = re.split(r"\.\s|\s+(?:Please|Thank|Go|Mr|Ms|Sir|Ma'am)\b", m.group(2))[0].strip(" .,")
                if len(firm) >= 3 and not re.match(r"(?:the|this|our|a|an)\b", firm, re.I):
                    firms[m.group(1).strip()] = firm[:80]

    # the host's opening often introduces management as "Mr. Sandeep Aggarwal (Whole-time Director)"
    for t in turns[:3]:
        for m in INTRO_TITLE.finditer(" ".join(t["p"])):
            titles.setdefault(m.group(1).upper(), m.group(2).strip())

    def title_for(name: str) -> str | None:
        up = re.sub(r"^(?:MR|MS|MRS|DR|SHRI)\.?\s+", "", name.upper())
        if up in titles:
            return titles[up]
        last = up.split()[-1] if up.split() else ""
        hits = [v for k, v in titles.items() if k.split() and (k.split()[-1] == last and k.split()[0][:1] == up[:1])]
        return hits[0] if len(hits) == 1 else None

    speakers: dict[str, dict] = {}
    for i, t in enumerate(turns):
        sp = speakers.setdefault(t["s"], {"turns": 0, "first": i, "after_chair": 0})
        sp["turns"] += 1
        # in the Q&A, whoever speaks straight after the moderator is (almost always) the next questioner
        if qa_start is not None and i > qa_start and turns[i - 1]["s"].lower() == chair:
            sp["after_chair"] += 1
    for name, sp in speakers.items():
        firm = firms.get(name) or next((f for n, f in firms.items() if n.split()[-1] == name.split()[-1] and n[:1] == name[:1]), None)
        title = title_for(name)
        if name.lower() == chair:
            sp["role"] = "moderator"
        elif host and name.upper().replace("MR. ", "") == host:
            sp["role"], sp["title"] = "host", "Call host"
        elif title:
            sp["role"], sp["title"] = "management", title
        elif firm:
            sp["role"], sp["firm"] = "analyst", firm
        elif qa_start is not None and sp["first"] > qa_start and sp["after_chair"]:
            sp["role"] = "analyst"
        else:
            sp["role"] = "management"
        sp.pop("first")
        sp.pop("after_chair")
    return {"label": quarter_label(head, ym), "call_date": _call_date(head), "structured": True,
            "speakers": speakers, "qa_start": qa_start, "turns": turns}


def _split_long(p: str, size: int = 700) -> list[str]:
    """Transcripts whose PDF gives no paragraph breaks come out as one block per turn: cut at sentence ends."""
    if len(p) <= size * 1.6:
        return [p]
    out, cur = [], ""
    for sent in re.split(r"(?<=[.?!])\s+(?=[A-Z“\"])", p):
        if cur and len(cur) + len(sent) > size:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return out


def _dates(head: str):
    """(position, ISO date) of every written-out date: "August 12, 2026" and "12th August, 2026"."""
    for rx, order in ((CALL_DATE, (1, 2, 3)), (CALL_DATE_DMY, (2, 1, 3))):
        for m in rx.finditer(head):
            mon, day, yr = (m.group(i) for i in order)
            try:
                yield m.start(), datetime.strptime(f"{mon} {day} {yr}", "%B %d %Y").date().isoformat()
            except ValueError:
                continue


def _call_date(head: str) -> str | None:
    """The day of the call: a date right after "held on" / "call on" first, then the one under the call's
    title, else the first date that isn't a quarter-end ("quarter ended June 30") -- the cover letter's own
    date usually comes before both, and is a few days later than the call."""
    best = None
    for pos, iso in sorted(_dates(head)):
        before = head[max(0, pos - 40):pos]
        if re.search(r"(?:ended|ending|as on|as of)\s*(?:on\s*)?(?:the\s*)?$", before, re.I):
            continue
        rank = 0 if re.search(r"(?:held|call|conference|meeting)\s+on\s*(?:\w+day,?\s*)?(?:the\s*)?$", before, re.I) \
            else 1 if re.search(r"call\W{0,3}\s*$", before, re.I) else 2
        if best is None or rank < best[0]:
            best = (rank, iso)
    return best[1] if best else None


def company_file(code: str) -> Path:
    return OUT_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '_', code)}.json.gz"


def read_company(code: str) -> dict:
    try:
        return json.loads(gzip.decompress(company_file(code).read_bytes()).decode("utf-8"))
    except (OSError, ValueError):
        return {"code": code, "calls": []}


def write_company(code: str, data: dict) -> None:
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    company_file(code).write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=200)
    ap.add_argument("--codes", default="")
    args = ap.parse_args()

    board = [c["code"] for c in load_json(RAW, [])]
    names = {c["code"]: c.get("name") for c in load_json(RAW, [])}
    if args.codes:
        want = {c.strip().upper() for c in args.codes.split(",") if c.strip()}
        board = [c for c in board if c.upper() in want]
    results = (load_json(RESULTS, {}).get("companies")) or {}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = load_json(INDEX, {})
    idx_companies = index.get("companies") or {}
    failed = index.get("failed") or {}
    now = datetime.now(timezone.utc)
    retry_before = (now - timedelta(days=RETRY_DAYS)).isoformat(timespec="seconds")

    # the work list: each company's newest unread call first, then its second newest, ...
    work, stored = [], {}
    for code in board:
        calls = [c for c in ((results.get(code) or {}).get("concalls") or [])[:KEEP_CALLS] if call_ym(c["date"])]
        have = {c["source"]: c for c in (idx_companies.get(code) or [])}
        stored[code] = calls
        for rank, call in enumerate(calls):
            prev = have.get(call["transcript"])
            if prev and prev.get("v") == PARSER_VERSION:
                continue
            f = failed.get(call["transcript"])
            if f and f.get("at", "") > retry_before:
                continue
            work.append((rank, code, call))
    work.sort(key=lambda w: (w[0], w[1]))
    work = work[: args.budget]

    def flush(codes) -> None:
        """Keep only each company's newest KEEP_CALLS and write its file and the index -- every SAVE_EVERY
        reads too, so a run cut short (timeout, network) keeps what it read."""
        for code in codes:
            keep = {c["transcript"] for c in stored.get(code, [])}
            data = touched.pop(code, None)
            if data is None and code in idx_companies and {c["source"] for c in idx_companies[code]} - keep:
                data = read_company(code)
            if data is None:
                continue
            data["calls"] = sorted([c for c in data["calls"] if c["source"] in keep], key=lambda c: c["ym"], reverse=True)
            data["code"], data["name"] = code, names.get(code)
            if data["calls"]:
                write_company(code, data)
                idx_companies[code] = [{"ym": c["ym"], "label": c["label"], "date": c["date"], "call_date": c.get("call_date"),
                                        "source": c["source"], "ppt": c.get("ppt"), "v": c["v"], "structured": c["structured"],
                                        "turns": len(c["turns"])} for c in data["calls"]]
            else:
                company_file(code).unlink(missing_ok=True)
                idx_companies.pop(code, None)
        new_index = {"companies": idx_companies, "failed": {u: f for u, f in failed.items() if f.get("at", "") > retry_before}}
        if json.dumps(new_index, sort_keys=True) != saved[0]:
            INDEX.write_text(json.dumps({"fetched_at": now.isoformat(timespec="seconds"), **new_index}, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
            saved[0] = json.dumps(new_index, sort_keys=True)

    saved = [json.dumps({"companies": index.get("companies") or {}, "failed": index.get("failed") or {}}, sort_keys=True)]
    session = requests.Session()
    session.headers.update(HEADERS)
    touched: dict[str, dict] = {}
    read = bad = 0
    for n, (_rank, code, call) in enumerate(work, 1):
        url = call["transcript"]
        try:
            r = session.get(url, timeout=90)
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                raise ValueError(f"HTTP {r.status_code}" if r.status_code != 200 else "not a PDF")
            ym = call_ym(call["date"])
            doc = parse(r.content, ym)
            if sum(len(p) for t in doc["turns"] for p in t["p"]) < 3000:
                raise ValueError("no readable text")
        except Exception as e:  # noqa: BLE001 - one bad PDF or network hiccup skips that call, never the run
            failed[url] = {"code": code, "date": call["date"], "error": str(e)[:80], "at": now.isoformat(timespec="seconds")}
            bad += 1
        else:
            failed.pop(url, None)
            data = touched.get(code) or read_company(code)
            data["calls"] = [c for c in data.get("calls", []) if c["source"] != url]
            data["calls"].append({"ym": ym, "date": call["date"], "source": url, "ppt": call.get("ppt"), "v": PARSER_VERSION, **doc})
            touched[code] = data
            read += 1
        if n % SAVE_EVERY == 0:
            flush(list(touched))
            print(f"  {n}/{len(work)} done ({read} read, {bad} unreadable)", flush=True)
        time.sleep(DELAY)

    flush(board)
    for code in list(idx_companies):
        if code not in names:
            company_file(code).unlink(missing_ok=True)
            idx_companies.pop(code)
    flush([])
    total = sum(len(v) for v in idx_companies.values())
    print(f"run_transcripts_in: {read} transcripts read ({bad} unreadable); {total} on file for {len(idx_companies)} companies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
