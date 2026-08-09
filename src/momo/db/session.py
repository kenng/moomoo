from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from momo.config import get_settings
from momo.db.models import Base


def _ensure_sqlite_dir(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return
    path = Path(database_url.removeprefix("sqlite:///"))
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)


def get_engine():
    settings = get_settings()
    _ensure_sqlite_dir(settings.database_url)
    return create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
    )


def _migrate_sqlite(engine) -> None:
    """Apply lightweight additive migrations for existing SQLite DBs."""
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    if "news_items" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("news_items")}
    if "provider" not in cols:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE news_items "
                    "ADD COLUMN provider VARCHAR(16) NOT NULL DEFAULT 'opend'"
                )
            )


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_sqlite(engine)


def get_session() -> Session:
    init_db()
    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return factory()
