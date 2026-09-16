"""
Charts for every actively traded NSE/BSE company, not just the board's.

Screen any Chart searches all of them. The candles are far too big to keep in
the repo -- roughly five thousand companies, a few hundred MB, rewritten every
night -- so scripts/update_charts.py bundles them into universe-charts.tar.gz
and the chart workflow uploads that as a GitHub release asset. This module
fetches the bundle once per server and reads single stocks out of it.

Committed, and so always available: chart_data/universe.json, one small line
per company (name, last price, stage, how many bases) -- enough to search and
browse. Only opening a chart needs the bundle.

If the bundle cannot be fetched, board companies are unaffected: their charts
live in the repo as before, and only non-board stocks report no data.
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

from .charts import CHART_DIR

UNIVERSE_INDEX = CHART_DIR / "universe.json"
ASSET = "universe-charts.tar.gz"
RELEASE = os.environ.get("UNIVERSE_RELEASE", "charts")
REPO = os.environ.get("GITHUB_REPO", "vile-sudo/deep-microcap-screener")
# a local build (scripts/update_charts.py) leaves the charts here; when that
# directory exists it is used as is and nothing is downloaded
LOCAL_DIR = CHART_DIR.parent / "universe_charts"

_lock = threading.Lock()
_state: dict = {"dir": None, "tried": 0, "error": None}
_RETRY = 15 * 60       # a failed download is retried after this long


def index() -> dict:
    try:
        return json.loads(UNIVERSE_INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"as_of": None, "count": 0, "stocks": {}}


def _download(dest: Path) -> None:
    url = f"https://github.com/{REPO}/releases/download/{RELEASE}/{ASSET}"
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        bundle = dest / ASSET
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


def charts_dir() -> Path | None:
    """The directory holding the universe charts, fetching it once if needed."""
    if LOCAL_DIR.is_dir():
        return LOCAL_DIR
    with _lock:
        if _state["dir"] and Path(_state["dir"]).is_dir():
            return Path(_state["dir"])
        if _state["tried"] and time.time() - _state["tried"] < _RETRY:
            return None
        _state["tried"] = time.time()
        dest = Path(tempfile.mkdtemp(prefix="universe-charts-"))
        try:
            _download(dest)
            _state["dir"], _state["error"] = str(dest), None
            return dest
        except (requests.RequestException, tarfile.TarError, OSError) as e:
            shutil.rmtree(dest, ignore_errors=True)
            _state["error"] = str(e)[:200]
            return None


def load(key: str) -> dict | None:
    d = charts_dir()
    if d is None:
        return None
    path = d / f"{key}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def status() -> dict:
    idx = index()
    return {"count": idx.get("count", 0), "as_of": idx.get("as_of"),
            "charts_ready": bool(_state["dir"] or LOCAL_DIR.is_dir()), "error": _state["error"]}
