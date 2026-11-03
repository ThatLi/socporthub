from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from api.config import get_settings

settings = get_settings()

def is_external_pooler(database_url: str) -> bool:
    """True for a transaction pooler such as Supabase's (port 6543). The session pooler and
    direct connections (port 5432) keep SQLAlchemy's normal pool."""
    return make_url(database_url).port == 6543


def engine_options(database_url: str) -> dict:
    """Engine settings for a URL. SQLite needs a thread flag. Behind an external pooler
    (serverless functions each hold their own process), SQLAlchemy must not keep its own
    pool, or idle connections pile up on the pooler: open one per use and close it."""
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    if is_external_pooler(database_url):
        return {"poolclass": NullPool}
    return {}


engine = create_engine(settings.database_url, **engine_options(settings.database_url))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
