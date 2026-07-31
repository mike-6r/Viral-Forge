from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.common.config import get_settings


class Base(DeclarativeBase):
    pass


def build_engine(database_url: str | None = None):  # type: ignore[no-untyped-def]
    return create_engine(database_url or get_settings().database_url, pool_pre_ping=True)


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False, class_=Session)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal(bind=build_engine())
    try:
        yield session
    finally:
        session.close()
