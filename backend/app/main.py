import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .auth import AccountGateMiddleware, ensure_admin
from .config import get_settings
from .database import Base, SessionLocal, engine
from .seed import seed_if_changed
from . import news_channel as news_feed
from . import push, us_alerts, webpush
from .routers import (account, alerts, asme, auth as auth_routes, charts, companies, deals, ipo_reports,
                       logic_gates, market, meta, movers, news_channel, push as push_routes,
                       push_web as push_web_routes, reports, sectors, watchlist)

settings = get_settings()

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


NEWS_REFRESH_SECONDS = 120   # was 300 -- as close to real-time as polling Google News/newsdata.io/BusinessLine
                             # allows; see _refresh_news_and_push's docstring for the newsdata.io DAILY_CAP tradeoff


def _refresh_news_and_push() -> bool:
    """Runs on a worker thread (see asyncio.to_thread below): fetches if
    stale, and notifies every registered device (mobile, via push.py) and
    every subscribed browser (desktop, via webpush.py) when that fetch
    actually added something new. Diffs by news_feed.item_keys() rather than
    trusting refresh_if_stale's own True/False (a run can fetch successfully
    and still add nothing, every story already seen) or a bare count (an old
    story aging out of the 48h window can mask a new one arriving in the same
    tick) -- and having the actual new items, not just how many, is what lets
    webpush.news_target() deep-link a single new story straight to itself.

    At NEWS_REFRESH_SECONDS=120, newsdata.io's own DAILY_CAP (see
    news_channel.py) is used up in about an hour instead of the ~2.5 hours a
    300s interval gave -- after that this run's newsdata.io leg is skipped
    for the rest of the day (news_channel.write() already handles that
    gracefully, see _quota_today). Google News and BusinessLine, unmetered
    and already the higher-volume sources, keep running all day regardless,
    so overall freshness still improves; it is only newsdata.io's own share
    of it that front-loads into the first hour."""
    before_keys = {frozenset(news_feed.item_keys(it)) for it in (news_feed.load().get("items") or [])}
    if not news_feed.refresh_if_stale(settings.newsdata_api_key or None, NEWS_REFRESH_SECONDS - 30):
        return False
    payload = news_feed.load()
    items = payload.get("items") or []
    new_items = [it for it in items if frozenset(news_feed.item_keys(it)) not in before_keys]
    if new_items:
        n = len(new_items)
        if push.configured():
            db = SessionLocal()
            try:
                push.send_to_all(db, "News Channel", f"{n} new stor{'y' if n == 1 else 'ies'} just in", {"type": "news_channel"})
            finally:
                db.close()
        if webpush.configured():
            title, body, url, tag = webpush.news_target(new_items)
            db = SessionLocal()
            try:
                webpush.send_to_all(db, title, body, url=url, tag=tag)
            finally:
                db.close()
    return True


async def _news_refresher():
    """Keeps the News Channel feed fresh from the server itself every 5
    minutes -- see news_channel.refresh_if_stale for why."""
    log = logging.getLogger("deepsweep")
    await asyncio.sleep(20)   # let startup finish first
    while True:
        try:
            if await asyncio.to_thread(_refresh_news_and_push):
                log.info("news channel refreshed")
        except Exception as e:  # noqa: BLE001
            log.warning("news channel refresh failed: %s", e)
        await asyncio.sleep(60)


US_ALERTS_SECONDS = 900


def _notify_us_alerts() -> int:
    db = SessionLocal()
    try:
        return us_alerts.notify_new(db)
    finally:
        db.close()


async def _us_alerts_loop():
    """Every 15 minutes: push any new US alert (earnings, insider buy, material 8-K,
    price signal) to the users who follow that company. See us_alerts.py."""
    log = logging.getLogger("deepsweep")
    await asyncio.sleep(45)   # after startup, and after the seed
    while True:
        try:
            await asyncio.to_thread(_notify_us_alerts)
        except Exception as e:  # noqa: BLE001
            log.warning("us alerts check failed: %s", e)
        await asyncio.sleep(US_ALERTS_SECONDS)


DESKTOP_PUSH_SECONDS = 60
DESKTOP_PUSH_STATE_KEY = "DESKTOP_PUSH_LAST"

def _alert_key(it: dict) -> str:
    """Identifies an alert item across runs (the list itself is recomputed fresh each script run, so
    position cannot be used): type + whichever of code/symbol/key names the stock + its window set (a
    high/low gaining a new window, e.g. becoming a 1-month high too, is genuinely new information, not
    the same item, so the key changes with it)."""
    ident = it.get("code") or it.get("symbol") or it.get("key") or ""
    windows = ",".join(sorted(it.get("windows") or ()))
    return f"{it['type']}:{ident}:{windows}"


def _desktop_push_tick() -> int:
    """Runs on a worker thread: if the India Alerts feed (chart_data/alerts.json +
    data/deals/clusters.json + data/insider_trades/clusters.json -- see routers/alerts.load()) has new
    items in its latest session compared to last time this checked, sends one push to every subscribed
    browser -- deep-linked straight to the single new item when there is exactly one (see
    webpush.alert_target()), a summary that opens the Alerts panel otherwise. First run only records the
    current state and sends nothing, so turning this on never replays the whole alert history as one
    notification. Returns how many browsers were sent to."""
    from .models import MetaKV
    from .routers.alerts import load as load_alerts

    doc = load_alerts()
    sessions = doc.get("sessions") or []
    if not sessions:
        return 0
    latest = sessions[-1]
    date, items = latest["date"], latest["items"]
    if not date:
        return 0

    db = SessionLocal()
    try:
        row = db.get(MetaKV, DESKTOP_PUSH_STATE_KEY)
        prev = row.value if row else None
        keys = [_alert_key(it) for it in items]
        state = {"date": date, "keys": keys}
        if prev is None:
            db.add(MetaKV(key=DESKTOP_PUSH_STATE_KEY, value=state))
            db.commit()
            return 0
        if state == prev:
            return 0
        prev_keys = set(prev.get("keys") or []) if prev.get("date") == date else set()
        new_items = [it for it, k in zip(items, keys) if k not in prev_keys]
        row.value = state          # row exists whenever prev is not None (the branch above returns first)
        db.commit()
        if not new_items:
            return 0

        title, body, url, tag = webpush.alert_target(new_items)
        return webpush.send_to_all(db, title, body, url=url, tag=tag)
    finally:
        db.close()


async def _desktop_push_loop():
    """Every minute: push a summary to every browser that opted into desktop notifications, when the
    Alerts feed has moved on since the last check. See webpush.py for delivery, _desktop_push_tick above
    for the "what's new" logic. (The Alerts feed itself -- chart_data/alerts.json and
    data/deals/clusters.json -- only regenerates once a day, at 02:00 IST via daily.yml, so this mostly
    just narrows how long after that daily run a subscribed browser finds out, from up to 10 minutes down
    to up to 1; it does not make anything intraday.)"""
    log = logging.getLogger("deepsweep")
    await asyncio.sleep(30)
    while True:
        try:
            if webpush.configured():
                await asyncio.to_thread(_desktop_push_tick)
        except Exception as e:  # noqa: BLE001
            log.warning("desktop push check failed: %s", e)
        await asyncio.sleep(DESKTOP_PUSH_SECONDS)


async def _filing_alerts_loop():
    """Checks every minute whether a filing-alerts fetch is due (app/filing_alerts.py decides: every 3
    minutes in market hours, every 30 otherwise) and pushes users their new filings by category."""
    from . import filing_alerts
    log = logging.getLogger("deepsweep")
    await asyncio.sleep(45)
    while True:
        try:
            if webpush.configured():
                await asyncio.to_thread(filing_alerts.tick)
        except Exception as e:  # noqa: BLE001
            log.warning("filing alerts check failed: %s", e)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Make sure tables exist even if `python -m app.seed` was never run
    # (e.g. a fresh container with a mounted-but-empty volume).
    # Several workers start at once; a table another worker is creating at the
    # same moment must not take this one down.
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("deepsweep").warning("create_all at startup: %s", e)
    seed_if_changed()
    ensure_admin()
    news_task = asyncio.create_task(_news_refresher())
    alerts_task = asyncio.create_task(_us_alerts_loop())
    desktop_push_task = asyncio.create_task(_desktop_push_loop())
    filing_alerts_task = asyncio.create_task(_filing_alerts_loop())
    try:
        yield
    finally:
        news_task.cancel()
        alerts_task.cancel()
        desktop_push_task.cancel()
        filing_alerts_task.cancel()


app = FastAPI(
    title=settings.app_name,
    description="Backend API for the Deep Microcap Screener research dashboard.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET"],
    allow_headers=["*"],
)
# Every page and API needs an approved account once an admin is configured
# (see app/auth.py); the login page, auth API, static files and /healthz stay open.
app.add_middleware(AccountGateMiddleware)

app.include_router(auth_routes.router)
app.include_router(companies.router)
app.include_router(meta.router)
app.include_router(charts.router)
app.include_router(asme.router)
app.include_router(market.router)
app.include_router(reports.router)
app.include_router(ipo_reports.router)
app.include_router(watchlist.router)
app.include_router(account.router)
app.include_router(alerts.router)
app.include_router(sectors.router)
app.include_router(logic_gates.router)
app.include_router(movers.router)
app.include_router(deals.router)
app.include_router(news_channel.router)
app.include_router(push_routes.router)
app.include_router(push_web_routes.router)


@app.get("/healthz", tags=["ops"])
def healthz():
    # The deployed commit (Render sets RENDER_GIT_COMMIT) lets anyone confirm
    # which version is live without logging in; it reveals nothing else.
    return {"status": "ok", "commit": os.environ.get("RENDER_GIT_COMMIT", "")[:7] or None,
            "database": "postgres" if engine.dialect.name == "postgresql" else engine.dialect.name}


# --- Serve the frontend -------------------------------------------------
# One deployable service: FastAPI answers /api/* and also serves the
# static dashboard, so there's a single process/URL to stand up. If you'd
# rather host the frontend separately (a CDN, Netlify, nginx, ...), point
# it at this API's /api routes and skip mounting these two lines.
# HTML pages must be revalidated on every load: with no Cache-Control header a browser keeps reusing its
# copy for a while (heuristically ~10% of the page's age) and keeps loading the previous deploy's
# script -- the ?v= cache-busters on the scripts only help once the new HTML is fetched. "no-cache"
# still allows the cached copy, it just checks the ETag first (a cheap 304 when nothing changed).
HTML_HEADERS = {"Cache-Control": "no-cache"}

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(FRONTEND_DIR / "static" / "favicon.ico", media_type="image/x-icon",
                            headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND_DIR / "index.html", headers=HTML_HEADERS)

    @app.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(FRONTEND_DIR / "login.html", headers=HTML_HEADERS)

    @app.get("/privacy", include_in_schema=False)
    def privacy_page():
        # Public (see auth.PUBLIC_PATHS) -- linked from the App Store/Play
        # Store listings, which need this reachable with no account at all.
        return FileResponse(FRONTEND_DIR / "privacy.html", headers=HTML_HEADERS)

    @app.get("/us", include_in_schema=False)
    def us_index():
        # Phase 2 of US-market support: a separate, smaller page rather than
        # a market flag threaded through index.html/app.js -- those ~4800
        # lines of closures assume one homogeneous India dataset (currency
        # formatting, NSE/BSE fields, ...) at nearly every line, so a
        # parallel page sharing only the CSS/login shell is the change that
        # doesn't fight that coupling. Requires login exactly like / does
        # (not in AccountGateMiddleware's PUBLIC_PATHS).
        return FileResponse(FRONTEND_DIR / "us.html", headers=HTML_HEADERS)
