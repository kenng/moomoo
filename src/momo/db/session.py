from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
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


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)


def get_session() -> Session:
    init_db()
    factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return factory()
