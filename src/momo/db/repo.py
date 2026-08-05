from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from momo.db.models import CashFlowDay, DividendReceived, NewsItem, QuoteSnapshot


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


def list_synced_cash_flow_days(
    session: Session, *, acc_id: str, trd_env: str, start: str, end: str
) -> set[str]:
    stmt = (
        select(CashFlowDay.clearing_date)
        .where(
            CashFlowDay.acc_id == acc_id,
            CashFlowDay.trd_env == trd_env,
            CashFlowDay.clearing_date >= start,
            CashFlowDay.clearing_date <= end,
        )
    )
    return set(session.scalars(stmt))


def mark_cash_flow_days_synced(
    session: Session, *, acc_id: str, trd_env: str, clearing_dates: list[str]
) -> None:
    now = datetime.utcnow()
    for day in clearing_dates:
        existing = session.scalar(
            select(CashFlowDay).where(
                CashFlowDay.acc_id == acc_id,
                CashFlowDay.trd_env == trd_env,
                CashFlowDay.clearing_date == day,
            )
        )
        if existing:
            existing.fetched_at = now
        else:
            session.add(
                CashFlowDay(
                    acc_id=acc_id,
                    trd_env=trd_env,
                    clearing_date=day,
                    fetched_at=now,
                )
            )
    session.commit()


def upsert_dividends(session: Session, items: list[dict]) -> int:
    saved = 0
    now = datetime.utcnow()
    fields = (
        "clearing_date",
        "settlement_date",
        "currency",
        "cashflow_type",
        "cashflow_direction",
        "cashflow_amount",
        "cashflow_remark",
        "stock_code",
        "stock_name",
        "shares",
    )
    for item in items:
        existing = session.scalar(
            select(DividendReceived).where(
                DividendReceived.acc_id == item["acc_id"],
                DividendReceived.trd_env == item["trd_env"],
                DividendReceived.cashflow_id == item["cashflow_id"],
            )
        )
        if existing:
            for key in fields:
                if key in item:
                    setattr(existing, key, item[key])
            existing.fetched_at = now
        else:
            session.add(
                DividendReceived(
                    acc_id=item["acc_id"],
                    trd_env=item["trd_env"],
                    cashflow_id=item["cashflow_id"],
                    fetched_at=now,
                    **{k: item.get(k) for k in fields},
                )
            )
            saved += 1
    session.commit()
    return saved


def list_dividends(
    session: Session,
    *,
    trd_env: str,
    start: str,
    end: str,
    acc_id: str | None = None,
) -> list[DividendReceived]:
    stmt = (
        select(DividendReceived)
        .where(
            DividendReceived.trd_env == trd_env,
            DividendReceived.clearing_date >= start,
            DividendReceived.clearing_date <= end,
        )
        .order_by(
            DividendReceived.clearing_date.desc(),
            DividendReceived.cashflow_id.desc(),
        )
    )
    if acc_id:
        stmt = stmt.where(DividendReceived.acc_id == acc_id)
    return list(session.scalars(stmt))
