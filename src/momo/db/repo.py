from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from momo.db.models import NewsItem, QuoteSnapshot


def upsert_news_items(session: Session, items: list[dict]) -> int:
    saved = 0
    for item in items:
        existing = session.scalar(
            select(NewsItem).where(
                NewsItem.symbol == item["symbol"],
                NewsItem.url == item["url"],
            )
        )
        if existing:
            existing.title = item["title"]
            existing.news_sub_type = item["news_sub_type"]
            existing.source = item["source"]
            existing.publish_time = item["publish_time"]
            existing.view_count = item["view_count"]
            existing.related_securities = item["related_securities"]
            existing.importance_score = item["importance_score"]
            existing.fetched_at = datetime.utcnow()
            existing.stock_name = item.get("stock_name", existing.stock_name)
        else:
            session.add(NewsItem(**item))
            saved += 1
    session.commit()
    return saved


def save_snapshot(session: Session, snapshot: dict) -> None:
    session.add(QuoteSnapshot(**snapshot))
    session.commit()


def list_news_for_symbol(session: Session, symbol: str, limit: int = 10) -> list[NewsItem]:
    stmt = (
        select(NewsItem)
        .where(NewsItem.symbol == symbol)
        .order_by(NewsItem.importance_score.desc(), NewsItem.fetched_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def list_top_news(session: Session, limit: int = 50) -> list[NewsItem]:
    stmt = (
        select(NewsItem)
        .order_by(NewsItem.importance_score.desc(), NewsItem.fetched_at.desc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def latest_snapshot(session: Session, symbol: str) -> QuoteSnapshot | None:
    stmt = (
        select(QuoteSnapshot)
        .where(QuoteSnapshot.symbol == symbol)
        .order_by(QuoteSnapshot.fetched_at.desc())
        .limit(1)
    )
    return session.scalar(stmt)
