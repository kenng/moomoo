from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("symbol", "url", name="uq_news_symbol_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(Text)
    news_sub_type: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(128), default="")
    publish_time: Mapped[str] = mapped_column(String(64), default="")
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    related_securities: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    importance_score: Mapped[float] = mapped_column(Float, default=0.0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QuoteSnapshot(Base):
    __tablename__ = "quote_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CashFlowDay(Base):
    """Marks a clearing date already pulled from OpenD for an account."""

    __tablename__ = "cash_flow_days"
    __table_args__ = (
        UniqueConstraint(
            "acc_id", "trd_env", "clearing_date", name="uq_cash_flow_day"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    acc_id: Mapped[str] = mapped_column(String(32), index=True)
    trd_env: Mapped[str] = mapped_column(String(16), index=True)
    clearing_date: Mapped[str] = mapped_column(String(10), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DividendReceived(Base):
    __tablename__ = "dividends_received"
    __table_args__ = (
        UniqueConstraint(
            "acc_id", "trd_env", "cashflow_id", name="uq_dividend_cashflow"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    acc_id: Mapped[str] = mapped_column(String(32), index=True)
    trd_env: Mapped[str] = mapped_column(String(16), index=True)
    cashflow_id: Mapped[str] = mapped_column(String(64))
    clearing_date: Mapped[str] = mapped_column(String(10), index=True)
    settlement_date: Mapped[str] = mapped_column(String(10), default="")
    currency: Mapped[str] = mapped_column(String(8), default="")
    cashflow_type: Mapped[str] = mapped_column(String(64), default="")
    cashflow_direction: Mapped[str] = mapped_column(String(16), default="")
    cashflow_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    cashflow_remark: Mapped[str] = mapped_column(Text, default="")
    stock_code: Mapped[str] = mapped_column(String(32), default="")
    stock_name: Mapped[str] = mapped_column(String(128), default="")
    shares: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
