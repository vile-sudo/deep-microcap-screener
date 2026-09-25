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


NEWS_REFRESH_SECONDS = 300


def _refresh_news_and_push() -> bool:
    """Runs on a worker thread (see asyncio.to_thread below): fetches if
    stale, and notifies every registered device (mobile, via push.py) and
    every subscribed browser (desktop, via webpush.py) when that fetch
    actually added something new. Counts before/after rather than trusting
    refresh_if_stale's own True/False, because a run can fetch successfully
    and still add nothing (every story already seen)."""
    before = news_feed.load().get("count", 0)
    if not news_feed.refresh_if_stale(settings.newsdata_api_key or None, NEWS_REFRESH_SECONDS - 30):
        return False
    payload = news_feed.load()
    # count can also fall (old stories aging out past the 48h cutoff), so a
    # net drop is clamped to 0 rather than pushing a nonsensical negative
    added = max(0, payload.get("count", 0) - before)
    if added > 0:
        body = f"{added} new stor{'y' if added == 1 else 'ies'} just in"
        if push.configured():
            db = SessionLocal()
            try:
                push.send_to_all(db, "News Channel", body, {"type": "news_channel"})
            finally:
                db.close()
        if webpush.configured():
            db = SessionLocal()
            try:
                webpush.send_to_all(db, "News Channel", body, url="/#v=news", tag="news-channel")
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


DESKTOP_PUSH_SECONDS = 600
DESKTOP_PUSH_STATE_KEY = "DESKTOP_PUSH_LAST"
DEAL_TYPES = ("deal_buy", "deal_sell")


def _desktop_push_tick() -> int:
    """Runs on a worker thread: if the India Alerts feed (chart_data/alerts.json +
    data/deals/clusters.json -- see routers/alerts.load()) has a newer or bigger
    latest session than last time this checked, sends one summary push to every
    subscribed browser. First run only records the current state and sends
    nothing, so turning this on never replays the whole alert history as one
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
        state = {"date": date, "count": len(items)}
        if prev is None:
            db.add(MetaKV(key=DESKTOP_PUSH_STATE_KEY, value=state))
            db.commit()
            return 0
        if state == prev:
            return 0
        new_count = state["count"] - prev["count"] if prev.get("date") == date else state["count"]
        row.value = state          # row exists whenever prev is not None (the branch above returns first)
        db.commit()
        if new_count <= 0:
            return 0

        hi = sum(1 for it in items if it["type"] == "high")
        lo = sum(1 for it in items if it["type"] == "low")
        bo = sum(1 for it in items if it["type"].endswith("breakout"))
        deals_n = sum(1 for it in items if it["type"] in DEAL_TYPES)
        bits = []
        if hi: bits.append(f"{hi} new high{'s' if hi != 1 else ''}")
        if lo: bits.append(f"{lo} new low{'s' if lo != 1 else ''}")
        if bo: bits.append(f"{bo} breakout{'s' if bo != 1 else ''}")
        if deals_n: bits.append(f"{deals_n} deal cluster{'s' if deals_n != 1 else ''}")
        body = ", ".join(bits) if bits else f"{len(items)} alerts"
        return webpush.send_to_all(db, "Deep Sweep alerts", body, url="/#v=overview", tag="deep-sweep-alerts")
    finally:
        db.close()


async def _desktop_push_loop():
    """Every 10 minutes: push a summary to every browser that opted into desktop notifications, when the
    Alerts feed has moved on since the last check. See webpush.py for delivery, _desktop_push_tick above
    for the "what's new" logic."""
    log = logging.getLogger("deepsweep")
    await asyncio.sleep(30)
    while True:
        try:
            if webpush.configured():
                await asyncio.to_thread(_desktop_push_tick)
        except Exception as e:  # noqa: BLE001
            log.warning("desktop push check failed: %s", e)
        await asyncio.sleep(DESKTOP_PUSH_SECONDS)


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
    try:
        yield
    finally:
        news_task.cancel()
        alerts_task.cancel()
        desktop_push_task.cancel()


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
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(FRONTEND_DIR / "static" / "favicon.ico", media_type="image/x-icon",
                            headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(FRONTEND_DIR / "login.html")

    @app.get("/privacy", include_in_schema=False)
    def privacy_page():
        # Public (see auth.PUBLIC_PATHS) -- linked from the App Store/Play
        # Store listings, which need this reachable with no account at all.
        return FileResponse(FRONTEND_DIR / "privacy.html")

    @app.get("/us", include_in_schema=False)
    def us_index():
        # Phase 2 of US-market support: a separate, smaller page rather than
        # a market flag threaded through index.html/app.js -- those ~4800
        # lines of closures assume one homogeneous India dataset (currency
        # formatting, NSE/BSE fields, ...) at nearly every line, so a
        # parallel page sharing only the CSS/login shell is the change that
        # doesn't fight that coupling. Requires login exactly like / does
        # (not in AccountGateMiddleware's PUBLIC_PATHS).
        return FileResponse(FRONTEND_DIR / "us.html")
