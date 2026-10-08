"""Company logos for the India board's Companies list and company pages.

    cd backend
    python scripts/run_logos_in.py                 # companies without a logo yet (and failures older than RETRY_DAYS)
    python scripts/run_logos_in.py --codes BEL,HIRECT --force

For each board company: its website (the link on its screener.in page), then the best icon that site
publishes for itself -- an apple-touch-icon or a large declared icon (usually the logo mark at 180 px),
else its favicon (from the site, or Google's favicon cache). Saved once into data/logos/<code>.<ext> and served by GET /api/logo-in/{code}, so a
visitor's browser only ever talks to our own server; data/logos/index.json lists what is on file
(GET /api/logos-in). A company with no usable icon keeps the coloured initial and is tried again after
RETRY_DAYS. Logos rarely change, so a daily run only fetches the board's newcomers.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BACKEND_DIR = Path(__file__).resolve().parent.parent
RAW = BACKEND_DIR / "data" / "companies_raw.json"
OUT_DIR = BACKEND_DIR / "data" / "logos"
INDEX = OUT_DIR / "index.json"
SCREENER = "https://www.screener.in/company/{code}/"
SCREENER_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; personal-research-screener/1.0; +https://pkresearch.in)"}
SITE_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
MAX_BYTES = 300_000
MIN_BYTES = 200          # a blank 1x1 or an error page that slipped through
RETRY_DAYS = 30
DELAY = 1.0
WORKERS = 5
SCREENER_GAP = 1.5   # seconds between screener.in requests
TYPES = {"image/png": "png", "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico", "image/svg+xml": "svg",
         "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
SKIP_HOSTS = ("screener.in", "bseindia.com", "nseindia.com", "sebi.gov.in", "crisil.com", "icra.in", "careratings.com",
              "indiaratings.co.in", "youtube.com", "youtu.be", "linkedin.com", "twitter.com", "x.com", "facebook.com")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def website(rec: dict, s: requests.Session) -> str | None:
    """The company's own site, from the "Website" link on its screener.in page."""
    for cand in (rec.get("nse_code"), rec.get("bse_code"), rec.get("code")):
        cand = str(cand or "").strip()
        if not cand:
            continue
        r = s.get(SCREENER.format(code=cand), headers=SCREENER_HEADERS, timeout=30)
        if r.status_code in (403, 429):
            raise RuntimeError("screener.in is refusing requests")
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a[href^='http']"):
            host = urlparse(a["href"]).netloc.lower()
            if a.get_text(strip=True).lower() == "website" or (a.parent and "company-links" in (a.parent.get("class") or [])):
                if host and not any(h in host for h in SKIP_HOSTS):
                    return a["href"]
        for a in soup.select(".company-links a[href^='http'], .company-info a[href^='http']"):
            host = urlparse(a["href"]).netloc.lower()
            if host and not any(h in host for h in SKIP_HOSTS):
                return a["href"]
        return None
    return None


def _size(link) -> int:
    m = re.match(r"(\d+)x\d+", link.get("sizes") or "")
    return int(m.group(1)) if m else 0


def icon_candidates(site: str, s: requests.Session) -> list[str]:
    """The site's own declared icons, best first, then the usual fixed paths, then Google's favicon cache."""
    out = []
    # screener often lists the plain-http address; many of those sites only answer on https now
    tries = [site.replace("http://", "https://", 1), site] if site.startswith("http://") else [site]
    for home in tries:
        try:
            r = s.get(home, headers=SITE_HEADERS, timeout=20, allow_redirects=True)
        except requests.RequestException:
            continue
        if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
            soup = BeautifulSoup(r.text[:400_000], "html.parser")
            links = [l for l in soup.select("link[rel][href]") if any("icon" in x.lower() for x in l.get("rel") or [])]
            touch = [l for l in links if any("apple-touch" in x.lower() for x in l["rel"])]
            other = sorted([l for l in links if l not in touch], key=_size, reverse=True)
            out += [urljoin(r.url, l["href"]) for l in touch + other]
            site = r.url
            break
    host = urlparse(site).netloc
    out += [urljoin(site, "/apple-touch-icon.png"), urljoin(site, "/favicon.ico"),
            # Google's favicon cache (fetched here, never by visitors): answers 404 when it has nothing
            f"https://www.google.com/s2/favicons?domain={host}&sz=128",
            f"https://www.google.com/s2/favicons?domain={re.sub(r'^www[.]', '', host)}&sz=128"]
    seen, uniq = set(), []
    for u in out:
        if u not in seen and u.startswith("http"):
            seen.add(u)
            uniq.append(u)
    return uniq[:9]


def fetch_icon(url: str, s: requests.Session) -> tuple[bytes, str] | None:
    try:
        r = s.get(url, headers=SITE_HEADERS, timeout=20)
    except requests.RequestException:
        return None
    if r.status_code != 200 or not (MIN_BYTES <= len(r.content) <= MAX_BYTES):
        return None
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    ext = TYPES.get(ctype)
    head = r.content[:12]
    if not ext:   # servers often send icons as octet-stream: trust the bytes instead
        ext = ("png" if head.startswith(b"\x89PNG") else "jpg" if head.startswith(b"\xff\xd8") else "ico" if head.startswith(b"\x00\x00\x01\x00")
               else "gif" if head.startswith(b"GIF8") else "webp" if head[8:12] == b"WEBP" else "svg" if b"<svg" in r.content[:600] else None)
    if not ext or (ext != "svg" and head.lstrip().startswith(b"<")):
        return None
    if ext == "svg" and re.search(rb"<script|on\w+\s*=", r.content, re.I):
        return None
    return r.content, ext


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--budget", type=int, default=600)
    args = ap.parse_args()

    board = load_json(RAW, [])
    if args.codes:
        want = {c.strip().upper() for c in args.codes.split(",") if c.strip()}
        board = [c for c in board if str(c.get("code", "")).upper() in want]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index = load_json(INDEX, {})
    logos, failed = dict(index.get("logos") or {}), dict(index.get("failed") or {})
    before = json.dumps({"logos": logos, "failed": failed}, separators=(",", ":"))
    now = datetime.now(timezone.utc)
    retry_before = (now - timedelta(days=RETRY_DAYS)).isoformat(timespec="seconds")
    work = [c for c in board if args.force or (str(c["code"]) not in logos and (failed.get(str(c["code"])) or {}).get("at", "") <= retry_before)]
    work = work[: args.budget]

    blocked = threading.Event()
    screener_lock, last_screener = threading.Lock(), [0.0]

    def one(rec):
        """(code, site, (bytes, ext) | None) for one company; run in a small thread pool, since nearly all
        the time is spent waiting on slow company websites."""
        if blocked.is_set():
            return str(rec["code"]), None, None, True
        sess = requests.Session()
        try:
            with screener_lock:     # screener.in one request at a time, paced; only the websites run in parallel
                wait = SCREENER_GAP - (time.monotonic() - last_screener[0])
                if wait > 0:
                    time.sleep(wait)
                site = website(rec, sess)
                last_screener[0] = time.monotonic()
        except RuntimeError:
            blocked.set()
            return str(rec["code"]), None, None, True
        except requests.RequestException:
            site = None
        found = None
        if site:
            for u in icon_candidates(site, sess):
                found = fetch_icon(u, sess)
                if found:
                    break
        time.sleep(DELAY)
        return str(rec["code"]), site, found, False

    got = miss = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for n, fut in enumerate(as_completed([pool.submit(one, rec) for rec in work]), 1):
            code, site, found, skipped = fut.result()
            if skipped:
                continue
            if found:
                body, ext = found
                stem = re.sub(r"[^A-Za-z0-9_-]", "_", code)
                for old in OUT_DIR.glob(f"{stem}.*"):
                    if old.name != "index.json":
                        old.unlink()
                (OUT_DIR / f"{stem}.{ext}").write_bytes(body)
                logos[code] = {"file": f"{stem}.{ext}", "site": site, "at": now.isoformat(timespec="seconds")}
                failed.pop(code, None)
                got += 1
            else:
                failed[code] = {"site": site, "at": now.isoformat(timespec="seconds")}
                miss += 1
            if n % 25 == 0:
                print(f"  {n}/{len(work)}: {got} logos, {miss} without", flush=True)
                INDEX.write_text(json.dumps({"logos": logos, "failed": failed}, separators=(",", ":")), encoding="utf-8")
    if blocked.is_set():
        print("run_logos_in: screener.in is refusing requests; kept what was fetched")

    codes = {str(c["code"]) for c in load_json(RAW, [])}
    for code in [c for c in logos if c not in codes]:
        (OUT_DIR / logos.pop(code)["file"]).unlink(missing_ok=True)
    failed = {c: f for c, f in failed.items() if c in codes}
    new = json.dumps({"logos": logos, "failed": failed}, separators=(",", ":"))
    if new != before:
        INDEX.write_text(new, encoding="utf-8")
    print(f"run_logos_in: {got} new logos, {miss} without one; {len(logos)} on file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
