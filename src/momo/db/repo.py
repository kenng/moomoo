from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from momo.db.models import (
    CashFlowDay,
    DividendReceived,
    NewsAiSummary,
    NewsItem,
    QuoteSnapshot,
)
from momo.domain.ranking import parse_publish_time


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
            existing.provider = item.get("provider") or existing.provider or "opend"
            existing.fetched_at = datetime.utcnow()
            existing.stock_name = item.get("stock_name", existing.stock_name)
        else:
            session.add(NewsItem(**{**item, "provider": item.get("provider") or "opend"}))
            saved += 1
    session.commit()
    return saved


def delete_news_older_than(session: Session, *, cutoff: datetime) -> int:
    """Delete news older than cutoff (by publish_time, else fetched_at)."""
    cutoff_naive = cutoff.replace(tzinfo=None) if cutoff.tzinfo else cutoff
    deleted = 0
    for row in session.scalars(select(NewsItem)):
        published = parse_publish_time(row.publish_time)
        if published is not None:
            age_ref = published.replace(tzinfo=None)
        else:
            age_ref = row.fetched_at
            if age_ref is None:
                continue
            if age_ref.tzinfo is not None:
                age_ref = age_ref.replace(tzinfo=None)
        if age_ref < cutoff_naive:
            session.delete(row)
            deleted += 1
    if deleted:
        session.commit()
    return deleted


def save_snapshot(session: Session, snapshot: dict) -> None:
    session.add(QuoteSnapshot(**snapshot))
    session.commit()


def list_news_for_symbol(
    session: Session,
    symbol: str,
    limit: int = 10,
    provider: str | None = None,
) -> list[NewsItem]:
    stmt = select(NewsItem).where(NewsItem.symbol == symbol)
    if provider:
        stmt = stmt.where(NewsItem.provider == provider)
    stmt = stmt.order_by(
        NewsItem.importance_score.desc(), NewsItem.fetched_at.desc()
    ).limit(limit)
    return list(session.scalars(stmt))


def list_top_news(
    session: Session, limit: int = 50, provider: str | None = None
) -> list[NewsItem]:
    stmt = select(NewsItem)
    if provider:
        stmt = stmt.where(NewsItem.provider == provider)
    stmt = stmt.order_by(
        NewsItem.importance_score.desc(), NewsItem.fetched_at.desc()
    ).limit(limit)
    return list(session.scalars(stmt))


def latest_snapshot(session: Session, symbol: str) -> QuoteSnapshot | None:
    stmt = (
        select(QuoteSnapshot)
        .where(QuoteSnapshot.symbol == symbol)
        .order_by(QuoteSnapshot.fetched_at.desc())
        .limit(1)
    )
    return session.scalar(stmt)


def get_ai_summary(session: Session, symbol: str) -> NewsAiSummary | None:
    return session.scalar(
        select(NewsAiSummary).where(NewsAiSummary.symbol == symbol)
    )


def upsert_ai_summary(session: Session, item: dict) -> NewsAiSummary:
    now = datetime.utcnow()
    existing = get_ai_summary(session, item["symbol"])
    if existing:
        existing.stock_code = item.get("stock_code", existing.stock_code)
        existing.stock_name = item.get("stock_name", existing.stock_name)
        existing.summary = item["summary"]
        existing.source_urls = item.get("source_urls", existing.source_urls)
        existing.model = item.get("model", existing.model)
        existing.updated_at = now
        session.commit()
        session.refresh(existing)
        return existing
    row = NewsAiSummary(
        symbol=item["symbol"],
        stock_code=item.get("stock_code", ""),
        stock_name=item.get("stock_name", ""),
        summary=item["summary"],
        source_urls=item.get("source_urls", ""),
        model=item.get("model", ""),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_ai_summaries_for_symbols(
    session: Session, symbols: list[str]
) -> dict[str, NewsAiSummary]:
    if not symbols:
        return {}
    rows = session.scalars(
        select(NewsAiSummary).where(NewsAiSummary.symbol.in_(symbols))
    )
    return {row.symbol: row for row in rows}


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
    start: str | None = None,
    end: str | None = None,
    acc_id: str | None = None,
) -> list[DividendReceived]:
    stmt = select(DividendReceived).where(DividendReceived.trd_env == trd_env)
    if start:
        stmt = stmt.where(DividendReceived.clearing_date >= start)
    if end:
        stmt = stmt.where(DividendReceived.clearing_date <= end)
    if acc_id:
        stmt = stmt.where(DividendReceived.acc_id == acc_id)
    stmt = stmt.order_by(
        DividendReceived.clearing_date.desc(),
        DividendReceived.cashflow_id.desc(),
    )
    return list(session.scalars(stmt))
