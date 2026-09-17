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
from .routers import account, alerts, asme, auth as auth_routes, charts, companies, logic_gates, market, meta, movers, news_channel, reports, sectors, watchlist

settings = get_settings()

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"


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
    yield


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
app.include_router(watchlist.router)
app.include_router(account.router)
app.include_router(alerts.router)
app.include_router(sectors.router)
app.include_router(logic_gates.router)
app.include_router(movers.router)
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
