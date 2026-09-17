"""
Serve a JSON file the daily jobs write, as is.

Returning the parsed dict from an endpoint makes FastAPI walk and re-encode
every value on every request -- for Market view's setups files (hundreds of KB
to a couple of MB) that is a fraction of a second of CPU on a fast machine and
several seconds on a small host, paid again on every page open even though
the file only changes once a day. These files are already compact JSON on
disk, so the bytes are read once per change (by mtime), gzipped once, and
sent straight out; an ETag lets the browser skip the download entirely until
the next run writes a new file.
"""
from __future__ import annotations

import gzip
import json
import threading
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response

_lock = threading.Lock()
_cache: dict[str, tuple[float, bytes, bytes]] = {}   # path (+ extra) -> (mtime, raw, gzipped)


def file_response(request: Request, path: Path, fallback: dict, extra: dict | None = None) -> Response:
    """`extra`: a few top-level keys added to the file's object (e.g. a live status flag)."""
    try:
        st = path.stat()
    except OSError:
        return Response(json.dumps(fallback), media_type="application/json")
    tail = json.dumps(extra, separators=(",", ":"))[1:-1] if extra else ""
    key = f"{path}|{tail}"
    with _lock:
        hit = _cache.get(key)
    if not hit or hit[0] != st.st_mtime:
        raw = path.read_bytes()
        if tail:
            raw = raw.rstrip()
            raw = raw[:-1] + (b"," if raw[:-1].rstrip()[-1:] != b"{" else b"") + tail.encode() + b"}"
        hit = (st.st_mtime, raw, gzip.compress(raw, 6))
        with _lock:
            _cache[key] = hit
    etag = f'"{int(st.st_mtime)}-{st.st_size}-{len(tail)}{tail[-6:]}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Accept-Encoding"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(hit[2], media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
    return Response(hit[1], media_type="application/json", headers=headers)
