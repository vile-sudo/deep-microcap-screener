from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

settings = get_settings()

url = settings.sqlalchemy_url
# timeout: how long a worker waits for SQLite's write lock (the startup schema migration holds it)
connect_args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}

# pool_pre_ping: a managed Postgres drops idle connections; test before use
engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
