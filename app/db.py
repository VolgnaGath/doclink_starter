import logging
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


@lru_cache(maxsize=4)
def get_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 2},
    )


SessionLocal = sessionmaker(class_=Session, expire_on_commit=False)


def get_session(database_url: str) -> Session:
    return SessionLocal(bind=get_engine(database_url))


def check_database(database_url: str) -> bool:
    try:
        with get_engine(database_url).connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        logger.warning("Database health check failed", exc_info=True)
        return False
    return True
