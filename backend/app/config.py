"""
Central configuration for the Deep Microcap Screener backend.

Everything here is overridable via environment variables (or a `.env` file
in the `backend/` directory), so the same code runs unmodified in local
dev, Docker, and whatever host you deploy to (Render, Railway, Fly.io,
a plain VPS, etc.) -- see DEPLOYMENT.md.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # .../backend


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database -----------------------------------------------------
    # Defaults to a local SQLite file, which is enough for a dataset this
    # size (a few hundred companies) and needs zero setup. Point
    # DATABASE_URL at Postgres/MySQL for a multi-instance deployment --
    # SQLAlchemy handles both without any code changes.
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'screener.db'}"

    # --- App ------------------------------------------------------------
    app_name: str = "Deep Microcap Screener API"
    environment: str = "development"          # development | production
    cors_origins: str = "*"                    # comma-separated list, or "*"

    # --- Accounts: log in / sign up ---------------------------------------
    # The dashboard requires an approved account once an admin is configured.
    # The admin account is created (and its password kept in sync) at startup
    # from ADMIN_EMAIL + ADMIN_PASSWORD, or -- so an existing deployment keeps
    # working without new settings -- from the older AUTH_USERNAME +
    # AUTH_PASSWORD. With neither set (local development) the site stays open.
    # New sign-ups wait as "pending" until the admin approves them.
    admin_email: str = ""
    admin_password: str = ""
    auth_username: str = ""
    auth_password: str = ""
    session_days: int = 30

    @property
    def admin_login(self) -> tuple[str, str] | None:
        ident = (self.admin_email or self.auth_username).strip().lower()
        pw = self.admin_password or self.auth_password
        return (ident, pw) if ident and pw else None

    # --- Zerodha Kite Connect (Market view live prices) ------------------
    # From your app at https://developers.kite.trade. Set them as env vars on
    # the host (never commit them). kite_admin_key is a password you make up:
    # connecting Zerodha (/api/kite/login) asks for it, so a visitor can't
    # replace the session. Leave all three empty and Market view falls back
    # to delayed index prices and end-of-day breakouts.
    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_admin_key: str = ""

    # --- News Channel (China/India/USA/Japan monopoly-sector news) -------------
    # From https://newsdata.io/ -- the free plan is 200 requests/day. Set as
    # an env var on the host (never commit it); also needed as a GitHub
    # Actions secret (NEWSDATA_API_KEY) for scripts/run_news_channel.py's
    # scheduled runs. Leave empty and the feed stays empty with no error.
    newsdata_api_key: str = ""

    # --- GitHub Actions dispatch (admin "Refresh now" on a sector page) --------
    # A fine-grained personal access token, Actions: Read and write, scoped to
    # this one repo (github.com/settings/tokens -> Fine-grained tokens). Lets an
    # admin fire the "Latest developments" workflow on demand instead of waiting
    # for the 2 AM run. Leave empty and the button explains how to set it up.
    github_repo: str = "vile-sudo/deep-microcap-screener"
    github_branch: str = "main"
    gh_dispatch_token: str = ""

    # --- External cron pinger (News Channel, real 5-minute freshness) ----
    # GitHub's own `schedule:` trigger doesn't reliably fire on a tight
    # interval -- it can silently skip runs under load, with no catch-up and
    # no SLA (see news_channel.yml's history: even an hourly cron drifted to
    # every 3-5 hours in practice). A `workflow_dispatch` trigger doesn't
    # have that problem -- it fires within seconds. So a real external timer
    # (any free HTTP cron service) hits POST /api/cron/news-channel?key=...
    # every 5 minutes instead, which just calls the same dispatch the admin
    # "Refresh now" button uses. cron_key is a password you make up -- set
    # it here and as that service's query-string key, never commit it.
    # Leave empty and the endpoint explains how to set it up.
    cron_key: str = ""

    # --- Push notifications (mobile/ Android/iOS app only) ----------------
    # The Capacitor wrapper in mobile/ registers each install's device with
    # POST /api/push/register (routers/push.py); app/push.py sends to them
    # via Firebase Cloud Messaging, the one push backend that reaches both
    # Android and iOS. The plain website is unaffected either way -- there
    # is no web push here, only these two settings.
    #
    # firebase_credentials_json is the *entire contents* of the service
    # account JSON file Firebase Console gives you (Project settings ->
    # Service accounts -> Generate new private key), pasted as one env var
    # -- easier to set as a platform secret than a file path on every host.
    # Leave empty and app/push.py silently no-ops: registration still works,
    # nothing ever actually sends.
    firebase_credentials_json: str = ""

    # --- Web push (desktop/browser notifications, the plain website) ------
    # A visitor can turn on "Desktop notifications" in the Alerts settings panel
    # (India board) so new alerts (highs/lows, breakouts, institutional deal
    # clusters) show up as an OS notification even when Deep Sweep is not the
    # open tab -- standard Web Push, no app to install. Needs one VAPID keypair
    # for this deployment (not per-user): generate it once with
    #     python -c "from py_vapid import Vapid02; import base64; v=Vapid02(); v.generate_keys(); \
    #       from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat; \
    #       priv=v.private_key.private_numbers().private_value.to_bytes(32,'big'); \
    #       print('VAPID_PUBLIC_KEY='+base64.urlsafe_b64encode(v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)).decode().rstrip('=')); \
    #       print('VAPID_PRIVATE_KEY='+base64.urlsafe_b64encode(priv).decode().rstrip('='))"
    # then set VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY and VAPID_SUBJECT (a
    # mailto: or https: contact URL push services may use if they need to
    # reach you about this deployment) as env vars on the server. Leave empty
    # and app/webpush.py silently no-ops: the toggle explains it isn't set up
    # yet, and nothing else is affected.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = ""

    @property
    def vapid_configured(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key and self.vapid_subject)

    # --- Data seeding ----------------------------------------------------
    seed_file: Path = BASE_DIR / "data" / "companies_raw.json"
    meta_file: Path = BASE_DIR / "data" / "meta_raw.json"

    # Written by the weekly discovery pipeline (see automation/), which runs
    # on your own machine, not on the server. If present, these override
    # whatever CANDIDATES/BUILD_STAMP baked into meta_raw.json at seed time --
    # that's what lets a `git push` of just these two small files update the
    # live "New listings queue" without touching the 375-company dataset.
    candidates_file: Path = BASE_DIR / "data" / "candidates_raw.json"
    build_stamp_file: Path = BASE_DIR / "data" / "build_stamp.json"

    # US market's own seed files (see backend/scripts/us_auto_screen.py and
    # app/seed.py's per-market seed()) -- same shape as the India ones
    # above, a separate pair so seeding one market never touches the other.
    # Missing is fine: seed_if_changed() skips a market whose file isn't
    # there yet rather than failing the whole startup seed.
    seed_file_us: Path = BASE_DIR / "data" / "companies_us_raw.json"
    meta_file_us: Path = BASE_DIR / "data" / "meta_us_raw.json"

    @property
    def sqlalchemy_url(self) -> str:
        """Render hands out postgres:// URLs; SQLAlchemy wants postgresql+psycopg2://."""
        url = self.database_url.strip()
        if url.startswith("postgres://"):
            url = "postgresql+psycopg2://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
