"""Shared client for NSE's JSON APIs.

Unlike the bhavcopy archive, these endpoints sit behind a bot check: they only
answer a session that has first collected cookies from the site itself, and they
expect a referer from the page the data belongs to. Responses are cached on disk
per day so a rerun, or a second job on the same day, does not hit them again.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
NSE_HOME = "https://www.nseindia.com"
FILINGS_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-actions"


class NseClient:
    """A cookie-warmed session for NSE's JSON endpoints."""

    def __init__(self, *, timeout: int = 30, retries: int = 3) -> None:
        self.timeout = timeout
        self.retries = retries
        self._session: requests.Session | None = None

    def _ready(self) -> requests.Session:
        if self._session is not None:
            return self._session
        session = requests.Session()
        session.headers.update(BROWSER_HEADERS)
        # The API rejects a session that has not seen the site first.
        session.get(NSE_HOME, timeout=self.timeout)
        session.get(FILINGS_PAGE, timeout=self.timeout)
        self._session = session
        return session

    def get_json(self, url: str, *, referer: str = FILINGS_PAGE) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                session = self._ready()
                response = session.get(url, timeout=self.timeout, headers={"Referer": referer})
                response.raise_for_status()
                return response.json()
            except Exception as error:
                last_error = error
                # A stale cookie jar looks like a hard failure, so start over.
                self._session = None
                delay = min(2**attempt, 8)
                LOGGER.warning(
                    "NSE request failed (%d/%d): %s; retrying in %ss",
                    attempt + 1,
                    self.retries,
                    error,
                    delay,
                )
                if attempt < self.retries - 1:
                    time.sleep(delay)
        raise RuntimeError(f"NSE request failed after {self.retries} attempts: {url}") from last_error


def cached_json(
    path: Path,
    build: "callable[[], Any]",
    *,
    label: str = "data",
) -> Any:
    """Return the cached payload for a day, fetching and storing it if absent."""
    if path.is_file() and path.stat().st_size > 2:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            LOGGER.warning("Cached %s at %s is unreadable; refetching", label, path)
    payload = build()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1) + chr(10), encoding="utf-8")
    LOGGER.info("Fetched %s -> %s", label, path)
    return payload


def as_records(payload: Any) -> list[dict]:
    """NSE returns either a bare list or an object with a data key."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    return []


def nse_date(value: date) -> str:
    """NSE's APIs take dates as DD-MM-YYYY."""
    return value.strftime("%d-%m-%Y")


def parse_nse_datetime(raw: str | None) -> datetime | None:
    """Parse the 10-Sep-2026 15:59:46 stamps used across these endpoints."""
    if not raw:
        return None
    text = str(raw).strip()
    for pattern in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None
