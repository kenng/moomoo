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
    provider: Mapped[str] = mapped_column(String(16), default="opend", index=True)
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


class NewsAiSummary(Base):
    """Cached LLM value-investor summary for a symbol's recent news."""

    __tablename__ = "news_ai_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(128), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    source_urls: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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


class PriceTargetConsensus(Base):
    """Cached OpenD analyst consensus target price for a symbol (latest query)."""

    __tablename__ = "price_target_consensus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(128), default="")
    highest: Mapped[float | None] = mapped_column(Float, nullable=True)
    average: Mapped[float | None] = mapped_column(Float, nullable=True)
    lowest: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_label: Mapped[str] = mapped_column(String(32), default="")
    total_analysts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    update_time_str: Mapped[str] = mapped_column(String(32), default="")
    buy: Mapped[float | None] = mapped_column(Float, nullable=True)
    hold: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell: Mapped[float | None] = mapped_column(Float, nullable=True)
    strong_buy: Mapped[float | None] = mapped_column(Float, nullable=True)
    underperform: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PriceTargetConsensusHistory(Base):
    """Point-in-time consensus fetches for latest vs ~1 month ago diffs."""

    __tablename__ = "price_target_consensus_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(128), default="")
    highest: Mapped[float | None] = mapped_column(Float, nullable=True)
    average: Mapped[float | None] = mapped_column(Float, nullable=True)
    lowest: Mapped[float | None] = mapped_column(Float, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_label: Mapped[str] = mapped_column(String(32), default="")
    total_analysts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    update_time_str: Mapped[str] = mapped_column(String(32), default="")
    buy: Mapped[float | None] = mapped_column(Float, nullable=True)
    hold: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell: Mapped[float | None] = mapped_column(Float, nullable=True)
    strong_buy: Mapped[float | None] = mapped_column(Float, nullable=True)
    underperform: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class InstitutionPriceTarget(Base):
    """Cached per-institution target price (OpenD research rating summary)."""

    __tablename__ = "institution_price_targets"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "institution_uid", name="uq_inst_target_symbol_uid"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(128), default="")
    institution_uid: Mapped[str] = mapped_column(String(64), index=True)
    institution_name: Mapped[str] = mapped_column(String(128), default="")
    institution_en_name: Mapped[str] = mapped_column(String(128), default="")
    institution_source_name: Mapped[str] = mapped_column(String(128), default="")
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_label: Mapped[str] = mapped_column(String(32), default="")
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommendation_date_str: Mapped[str] = mapped_column(String(32), default="")
    rating_url: Mapped[str] = mapped_column(Text, default="")
    update_time_str: Mapped[str] = mapped_column(String(32), default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StockOracleValuation(Base):
    """Cached Stock Oracle moat / OracleValue for a US-listed symbol."""

    __tablename__ = "stock_oracle_valuations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(128), default="")
    moat: Mapped[str] = mapped_column(String(16), default="")
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="")
    assess_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StrategyCheck(Base):
    """Append-only option-strategy timing check."""

    __tablename__ = "strategy_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    stock_code: Mapped[str] = mapped_column(String(16), index=True)
    stock_name: Mapped[str] = mapped_column(String(128), default="")
    strategy: Mapped[str] = mapped_column(String(32), index=True)
    conclusion: Mapped[str] = mapped_column(String(32), default="")
    penalty: Mapped[int] = mapped_column(Integer, default=0)
    conditions_json: Mapped[str] = mapped_column(Text, default="[]")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
