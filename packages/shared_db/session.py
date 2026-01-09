from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from packages.shared_db.settings import settings

def _build_engine():
    url = settings.database_url
    pool_kwargs: dict[str, object] = {"pool_pre_ping": True}
    if not url.startswith("sqlite"):
        if settings.db_pool_size > 0:
            pool_kwargs["pool_size"] = settings.db_pool_size
        if settings.db_max_overflow >= 0:
            pool_kwargs["max_overflow"] = settings.db_max_overflow
        if settings.db_pool_timeout > 0:
            pool_kwargs["pool_timeout"] = settings.db_pool_timeout
        if settings.db_pool_recycle > 0:
            pool_kwargs["pool_recycle"] = settings.db_pool_recycle
        if settings.db_connect_timeout > 0:
            pool_kwargs["connect_args"] = {
                "connect_timeout": settings.db_connect_timeout
            }
    return create_engine(url, **pool_kwargs)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
