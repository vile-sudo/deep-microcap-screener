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

    # --- Data seeding ----------------------------------------------------
    seed_file: Path = BASE_DIR / "data" / "companies_raw.json"
    meta_file: Path = BASE_DIR / "data" / "meta_raw.json"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
