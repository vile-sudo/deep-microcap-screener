"""Download and parse NSE's daily security-wise bhavcopy.

NSE settles the official closing price as a volume-weighted average of the last
thirty minutes of trading, and publishes it as CLOSE_PRICE. That is the figure it
carries forward as the next session's PREV_CLOSE, so a scan built on CLOSE_PRICE
is self-consistent day to day. LAST_PRICE, the final traded price, is a different
number and is not what NSE or a quote service reports as the close.

This is the same "products/content/sec_bhavdata_full_DDMMYYYY.csv" file the Chart
gallery's legacy-format fallback reads (see app/charts.py), but kept in its own
small cache here rather than sharing that one: Movers only ever needs today's
file (plus a handful of recent days for a rerun), while the Chart gallery's cache
holds years of history for candle-building. Keeping them apart means neither can
break the other.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

LOGGER = logging.getLogger(__name__)

BHAVCOPY_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{stamp}.csv"
NSE_HOME = "https://www.nseindia.com"
BROWSER_HEADERS = {
    # NSE's archive rejects requests that do not look like a browser session.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
# Equity series carried in the cash segment. EQ is the rolling-settlement board,
# BE and BZ are trade-to-trade, SM and ST are the SME board.
EQUITY_SERIES = frozenset({"EQ", "BE", "BZ", "SM", "ST"})

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".movers_cache" / "bhavcopy"


class MarketHoliday(RuntimeError):
    """Raised when NSE published no bhavcopy for the requested date."""


@dataclass(frozen=True, slots=True)
class Close:
    symbol: str
    series: str
    prev_close: float
    close: float
    delivery_percent: float | None

    @property
    def pct_change(self) -> float:
        return (self.close - self.prev_close) / self.prev_close * 100


def cache_paths(trade_date: date, cache_dir: Path) -> tuple[Path, Path]:
    """Return the gzipped and plain cache paths for a date, in that order."""
    stem = f"sec_bhavdata_full_{trade_date.strftime('%d%m%Y')}"
    return cache_dir / f"{stem}.csv.gz", cache_dir / f"{stem}.csv"


def read_cached(trade_date: date, cache_dir: Path) -> str | None:
    """Read a cached bhavcopy, gzipped or plain, or None if it is not there."""
    compressed, plain = cache_paths(trade_date, cache_dir)
    if compressed.is_file() and compressed.stat().st_size > 200:
        with gzip.open(compressed, "rt", encoding="utf-8") as handle:
            return handle.read()
    if plain.is_file() and plain.stat().st_size > 1000:
        return plain.read_text(encoding="utf-8")
    return None


def write_cached(trade_date: date, text: str, cache_dir: Path) -> Path:
    """Store a bhavcopy gzipped. A year of raw CSVs is about 100 MB of history."""
    compressed, _ = cache_paths(trade_date, cache_dir)
    compressed.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 keeps the bytes reproducible so an unchanged file is not re-committed.
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed.open("wb"), mtime=0) as raw:
        raw.write(text.encode("utf-8"))
    return compressed


def fetch_bhavcopy(
    trade_date: date,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    retries: int = 3,
    timeout: int = 30,
) -> str:
    """Return the raw bhavcopy CSV for a date, caching it on disk."""
    cached = read_cached(trade_date, cache_dir)
    if cached is not None:
        LOGGER.info("Using cached bhavcopy for %s", trade_date.isoformat())
        return cached

    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    url = BHAVCOPY_URL.format(stamp=trade_date.strftime("%d%m%Y"))
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            # The archive host expects a cookie from the main site first.
            session.get(NSE_HOME, timeout=timeout)
            response = session.get(url, timeout=timeout)
            if response.status_code == 404:
                raise MarketHoliday(
                    f"NSE published no bhavcopy for {trade_date.isoformat()}; "
                    "it was most likely a trading holiday."
                )
            response.raise_for_status()
            if len(response.content) < 1000:
                raise RuntimeError(f"Bhavcopy for {trade_date.isoformat()} looks truncated")
            written = write_cached(trade_date, response.text, cache_dir)
            LOGGER.info(
                "Downloaded bhavcopy for %s (%d KB, stored as %d KB at %s)",
                trade_date.isoformat(),
                len(response.content) // 1024,
                written.stat().st_size // 1024,
                written,
            )
            return response.text
        except MarketHoliday:
            raise
        except Exception as error:
            last_error = error
            delay = min(2**attempt, 8)
            LOGGER.warning(
                "Bhavcopy request failed (%d/%d): %s; retrying in %ss",
                attempt + 1,
                retries,
                error,
                delay,
            )
            if attempt < retries - 1:
                time.sleep(delay)
    raise RuntimeError(f"Could not download the bhavcopy for {trade_date.isoformat()}") from last_error


def parse_bhavcopy(text: str, *, series: frozenset[str] = EQUITY_SERIES) -> dict[str, Close]:
    """Parse the CSV into one Close per symbol, keeping equity series only."""
    closes: dict[str, Close] = {}
    for row in csv.DictReader(io.StringIO(text, newline="")):
        # NSE pads its header and its values with spaces.
        clean = {
            key.strip(): (value.strip() if isinstance(value, str) else value)
            for key, value in row.items()
            if key is not None
        }
        symbol, row_series = clean.get("SYMBOL", ""), clean.get("SERIES", "")
        if not symbol or row_series not in series:
            continue
        try:
            prev_close = float(clean["PREV_CLOSE"])
            close = float(clean["CLOSE_PRICE"])
        except (KeyError, TypeError, ValueError):
            continue
        if prev_close <= 0 or close <= 0:
            continue
        try:
            delivery = float(clean.get("DELIV_PER", ""))
        except (TypeError, ValueError):
            delivery = None
        closes[symbol] = Close(symbol, row_series, prev_close, close, delivery)
    return closes


def file_date(text: str) -> date | None:
    """Return the session date the CSV actually contains, from its DATE1 column."""
    for row in csv.DictReader(io.StringIO(text, newline="")):
        for key, value in row.items():
            if key is not None and key.strip() == "DATE1" and isinstance(value, str):
                try:
                    return datetime.strptime(value.strip(), "%d-%b-%Y").date()
                except ValueError:
                    return None
    return None


def load_closes(
    trade_date: date,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    series: frozenset[str] = EQUITY_SERIES,
) -> dict[str, Close]:
    """Download (or reuse) and parse a session's closes."""
    text = fetch_bhavcopy(trade_date, cache_dir=cache_dir)

    # Asking for a holiday does not 404. NSE answers 200 with the previous trading
    # session's file, so the content has to be checked against what was requested
    # or the wrong day's moves get recorded under the requested date.
    contained = file_date(text)
    if contained is not None and contained != trade_date:
        for stale in cache_paths(trade_date, cache_dir):
            stale.unlink(missing_ok=True)
        raise MarketHoliday(
            f"NSE has no session for {trade_date.isoformat()}; the file it served holds "
            f"{contained.isoformat()} instead, so {trade_date.isoformat()} was a trading holiday."
        )

    closes = parse_bhavcopy(text, series=series)
    if not closes:
        raise RuntimeError(f"The bhavcopy for {trade_date.isoformat()} contained no equity rows")
    LOGGER.info("Loaded %d equity closes for %s", len(closes), trade_date.isoformat())
    return closes
