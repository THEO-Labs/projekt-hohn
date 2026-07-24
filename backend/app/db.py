from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


# Pool gross genug fuer Batch-Worker (halten Sessions ueber lange LLM-Phasen)
# PLUS UI-Polling von 40 Cards gleichzeitig. Default 5+10 war unter Batch-Last
# erschoepft -> 500er auf UI-Reads. pool_pre_ping recycelt tote Verbindungen.
engine = create_engine(
    settings.database_url,
    future=True,
    pool_size=20,
    max_overflow=30,
    pool_timeout=30,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
