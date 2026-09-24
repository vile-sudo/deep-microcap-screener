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
from .database import Base, engine
from .seed import seed_if_changed
from . import news_channel as news_feed
from .routers import account, alerts, asme, auth as auth_routes, charts, companies, deals, ipo_reports, logic_gates, market, meta, movers, news_channel, reports, sectors, watchlist

settings = get_settings()

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


NEWS_REFRESH_SECONDS = 300


async def _news_refresher():
    """Keeps the News Channel feed fresh from the server itself every 5
    minutes -- see news_channel.refresh_if_stale for why."""
    log = logging.getLogger("deepsweep")
    await asyncio.sleep(20)   # let startup finish first
    while True:
        try:
            if await asyncio.to_thread(news_feed.refresh_if_stale, settings.newsdata_api_key or None, NEWS_REFRESH_SECONDS - 30):
                log.info("news channel refreshed")
        except Exception as e:  # noqa: BLE001
            log.warning("news channel refresh failed: %s", e)
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
    try:
        yield
    finally:
        news_task.cancel()


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
