"""
Charts for every actively traded company, not just the board's -- India
(NSE/BSE) and US, each with its own release asset and local-build
fallback but otherwise identical handling.

The candles are far too big to keep in the repo -- thousands of companies,
hundreds of MB, rewritten every run -- so scripts/update_charts.py (India)
and scripts/update_us_charts.py (US) each bundle their own universe_charts*/
into a tarball and the corresponding workflow uploads it as a GitHub
release asset. This module fetches a market's bundle once per server and
reads single stocks out of it.

Committed, and so always available: chart_data/universe.json (India) and
chart_data_us/universe.json (US), one small line per company -- enough to
search and browse. Only opening a chart needs the bundle.

If a bundle cannot be fetched, board companies are unaffected: their
charts live in the repo as before, and only non-board stocks in that
market report no data.
"""
from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
import threading
import time
from pathlib import Path

import requests

from . import charts, charts_us

REPO = os.environ.get("GITHUB_REPO", "vile-sudo/deep-microcap-screener")
_RETRY = 15 * 60       # a failed download is retried after this long

# Per-market config: the small committed index, the release tag+asset name,
# and where a local build (running the update script yourself) leaves its
# charts -- used as-is with no download when that directory exists.
_MARKETS = {
    "IN": {
        "index_file": charts.INDEX_FILE.parent / "universe.json",
        "asset": "universe-charts.tar.gz",
        "release": os.environ.get("UNIVERSE_RELEASE", "charts"),
        "local_dir": charts.CHART_DIR.parent / "universe_charts",
    },
    "US": {
        "index_file": charts_us.UNIVERSE_INDEX,
        "asset": "universe-charts-us.tar.gz",
        "release": os.environ.get("UNIVERSE_RELEASE_US", "charts-us"),
        "local_dir": charts_us.CHART_DIR.parent / "universe_charts_us",
    },
}

_lock = threading.Lock()
_state: dict[str, dict] = {m: {"dir": None, "tried": 0, "error": None} for m in _MARKETS}


def index(market: str = "IN") -> dict:
    try:
        return json.loads(_MARKETS[market]["index_file"].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"as_of": None, "count": 0, "stocks": {}}


def _download(market: str, dest: Path) -> None:
    cfg = _MARKETS[market]
    url = f"https://github.com/{REPO}/releases/download/{cfg['release']}/{cfg['asset']}"
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        bundle = dest / cfg["asset"]
        with open(bundle, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    with tarfile.open(bundle) as tar:
        # every member is a plain <KEY>.json written by our own build step;
        # anything else (a path trying to escape, a symlink) is ignored
        safe = [m for m in tar.getmembers()
                if m.isfile() and m.name.endswith(".json") and "/" not in m.name.strip("./").replace("./", "")]
        tar.extractall(dest, members=safe, filter="data")
    bundle.unlink(missing_ok=True)


def charts_dir(market: str = "IN") -> Path | None:
    """The directory holding a market's universe charts, fetching it once if needed."""
    cfg = _MARKETS[market]
    if cfg["local_dir"].is_dir():
        return cfg["local_dir"]
    with _lock:
        st = _state[market]
        if st["dir"] and Path(st["dir"]).is_dir():
            return Path(st["dir"])
        if st["tried"] and time.time() - st["tried"] < _RETRY:
            return None
        st["tried"] = time.time()
        dest = Path(tempfile.mkdtemp(prefix=f"universe-charts-{market.lower()}-"))
        try:
            _download(market, dest)
            st["dir"], st["error"] = str(dest), None
            return dest
        except (requests.RequestException, tarfile.TarError, OSError) as e:
            shutil.rmtree(dest, ignore_errors=True)
            st["error"] = str(e)[:200]
            return None


def load(key: str, market: str = "IN") -> dict | None:
    d = charts_dir(market)
    if d is None:
        return None
    path = d / f"{key}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def status(market: str = "IN") -> dict:
    idx = index(market)
    cfg, st = _MARKETS[market], _state[market]
    return {"count": idx.get("count", 0), "as_of": idx.get("as_of"),
            "charts_ready": bool(st["dir"] or cfg["local_dir"].is_dir()), "error": st["error"]}
