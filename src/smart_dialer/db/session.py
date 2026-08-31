from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from smart_dialer.config import get_settings


def build_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    engine = create_engine(
        database_url or get_settings().database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        pool_timeout=10,
    )
    return sessionmaker(bind=engine, expire_on_commit=False)
