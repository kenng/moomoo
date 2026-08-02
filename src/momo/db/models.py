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
