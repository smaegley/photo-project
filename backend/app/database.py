from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    # Sized above uvicorn's sync-route threadpool (40 by default) so a connection
    # checkout can never queue: 20 + 30 overflow = 50 > 40. SQLite connections are
    # cheap (a file handle), and WAL lets readers run concurrently. The default
    # 5 + 10 = 15 was below the threadpool ceiling, so an image burst exhausted the
    # pool and requests died on the 30s checkout wait (QueuePool TimeoutError).
    pool_size=20,
    max_overflow=30,
    # Fail fast rather than hanging a tile for 30s if this is ever wrong again.
    pool_timeout=10,
    connect_args={"check_same_thread": False},  # SQLite + FastAPI
)


# Enforce foreign keys on SQLite (off by default).
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # Readers don't block the writer (and vice versa); required for multi-user use.
    cursor.execute("PRAGMA journal_mode=WAL")
    # Wait up to 5s on a locked DB instead of failing immediately.
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
