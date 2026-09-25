"""Merge the repo's News Channel files into the server's, without losing anything.

The News Channel is written from TWO places: the GitHub workflow (news_channel.yml, which commits
data/news_channel/) and the server itself, which refreshes its own copy every five minutes
(main.py's _news_refresher). Deploys copy the repo's data folder into the running container
(sync-data.sh). Copying the repo's latest.json OVER the server's threw away every story the server
had fetched since the workflow last ran -- typically hours of them, and the workflow is often hours
apart because GitHub does not honour a 5-minute schedule -- so headlines like "Borosil rallies as
India recommends anti-dumping duty on Chinese glassware" were on the dashboard and then vanished
after a deploy.

So news files are never copied; they are merged: every story from either side is kept
(de-duplicated the way news_channel.write() does), for latest.json only the last 48 hours, for each
archive day everything.

    python -m app.news_sync <repo news_channel folder>
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from . import news_channel as nc


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _union(*lists: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in sorted((i for lst in lists for i in lst), key=lambda x: x.get("published") or "", reverse=True):
        keys = nc._item_keys(it)
        if keys & seen:
            continue
        seen |= keys
        out.append(it)
    return out


def _write(path: Path, payload: dict, indent=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=indent, separators=None if indent else (",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def merge(src: Path) -> dict:
    """Merge src (the repo's news_channel folder) into the server's. Returns what changed."""
    changed = {"latest": False, "archive_days": 0}

    # latest.json: the live 48-hour window
    theirs, mine = _read(src / "latest.json"), _read(nc.NEWS_FILE)
    if theirs.get("items"):
        cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - nc.LOOKBACK_HOURS * 3600))
        items = [i for i in _union(mine.get("items") or [], theirs.get("items") or []) if (i.get("published") or "") >= cutoff]
        if len(items) != len(mine.get("items") or []):
            newer = theirs if (theirs.get("as_of") or "") > (mine.get("as_of") or "") else mine
            payload = {**newer, "count": len(items), "items": items, "as_of": max(theirs.get("as_of") or "", mine.get("as_of") or "")}
            _write(nc.NEWS_FILE, payload, indent=1)
            changed["latest"] = True

    # archive days: everything from both sides
    for f in sorted((src / "archive").glob("*.json")):
        theirs, mine = _read(f), _read(nc.ARCHIVE_DIR / f.name)
        items = _union(mine.get("items") or [], theirs.get("items") or [])
        if len(items) != len(mine.get("items") or []):
            _write(nc.ARCHIVE_DIR / f.name, {"date": f.stem, "count": len(items), "items": items})
            changed["archive_days"] += 1
    return changed


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.news_sync <repo news_channel folder>")
    print("news_sync:", merge(Path(sys.argv[1])))
