from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from momo.db.models import (
    CashFlowDay,
    DividendReceived,
    InstitutionPriceTarget,
    NewsAiSummary,
    NewsItem,
    PriceTargetConsensus,
    PriceTargetConsensusHistory,
    QuoteSnapshot,
    StockOracleValuation,
    StrategyCheck,
)
from momo.domain.ranking import NEWS_RETENTION_DAYS, news_is_fresh

_CONSENSUS_FIELDS = (
    "stock_code",
    "stock_name",
    "highest",
    "average",
    "lowest",
    "rating",
    "rating_label",
    "total_analysts",
    "update_time_str",
    "buy",
    "hold",
    "sell",
    "strong_buy",
    "underperform",
)

# Prefer a snapshot near 30 days ago; ignore very recent rows as "month ago".
_MONTH_AGO_TARGET = timedelta(days=30)
_MONTH_AGO_MIN_AGE = timedelta(days=20)
_HISTORY_RETENTION = timedelta(days=45)


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
    deleted = 0
    for row in session.scalars(select(NewsItem)):
        if news_is_fresh(
            row.publish_time, cutoff=cutoff, fetched_at=row.fetched_at
        ):
            continue
        session.delete(row)
        deleted += 1
    if deleted:
        session.commit()
    return deleted


def save_snapshot(session: Session, snapshot: dict) -> None:
    existing = latest_snapshot(session, snapshot["symbol"])
    if existing:
        existing.stock_code = snapshot.get("stock_code", existing.stock_code)
        existing.last_price = snapshot.get("last_price")
        existing.change_rate = snapshot.get("change_rate")
        existing.volume = snapshot.get("volume")
        existing.turnover = snapshot.get("turnover")
        existing.fetched_at = datetime.utcnow()
        session.commit()
        return
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
    )
    return _fresh_news(list(session.scalars(stmt)), limit)


def list_top_news(
    session: Session, limit: int = 50, provider: str | None = None
) -> list[NewsItem]:
    stmt = select(NewsItem)
    if provider:
        stmt = stmt.where(NewsItem.provider == provider)
    stmt = stmt.order_by(
        NewsItem.importance_score.desc(), NewsItem.fetched_at.desc()
    )
    return _fresh_news(list(session.scalars(stmt)), limit)


def _fresh_news(rows: list[NewsItem], limit: int) -> list[NewsItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=NEWS_RETENTION_DAYS)
    fresh = [
        row
        for row in rows
        if news_is_fresh(row.publish_time, cutoff=cutoff, fetched_at=row.fetched_at)
    ]
    return fresh[:limit]


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


def _consensus_payload(item: dict) -> dict:
    return {k: item.get(k) for k in _CONSENSUS_FIELDS}


def _consensus_row_payload(row) -> dict:
    return {k: getattr(row, k) for k in _CONSENSUS_FIELDS}


def _history_should_skip(
    prev: PriceTargetConsensusHistory | None, item: dict, *, fetched_at: datetime
) -> bool:
    """Dedupe only rapid identical refreshes; keep spaced points for month-ago."""
    if prev is None:
        return False
    for key in ("highest", "average", "lowest", "rating", "update_time_str", "total_analysts"):
        if getattr(prev, key) != item.get(key):
            return False
    if prev.fetched_at is None:
        return False
    return (fetched_at - prev.fetched_at) < timedelta(hours=12)


def _append_consensus_history(
    session: Session, *, symbol: str, payload: dict, fetched_at: datetime
) -> None:
    prev = session.scalar(
        select(PriceTargetConsensusHistory)
        .where(PriceTargetConsensusHistory.symbol == symbol)
        .order_by(PriceTargetConsensusHistory.fetched_at.desc())
        .limit(1)
    )
    if _history_should_skip(prev, payload, fetched_at=fetched_at):
        return
    session.add(
        PriceTargetConsensusHistory(
            symbol=symbol,
            fetched_at=fetched_at,
            **payload,
        )
    )


def _prune_consensus_history(session: Session, symbol: str) -> None:
    """Keep recent history plus the best ~1 month ago candidate."""
    rows = list(
        session.scalars(
            select(PriceTargetConsensusHistory)
            .where(PriceTargetConsensusHistory.symbol == symbol)
            .order_by(PriceTargetConsensusHistory.fetched_at.desc())
        )
    )
    if len(rows) <= 2:
        return
    now = datetime.utcnow()
    keep_ids = {rows[0].id}  # latest history row
    month_ago = _pick_month_ago_row(rows, now=now)
    if month_ago is not None:
        keep_ids.add(month_ago.id)
    cutoff = now - _HISTORY_RETENTION
    for row in rows:
        if row.id in keep_ids:
            continue
        if row.fetched_at >= cutoff and len(keep_ids) < 8:
            keep_ids.add(row.id)
            continue
        session.delete(row)


def _pick_month_ago_row(rows: list, *, now: datetime):
    if not rows:
        return None
    ordered = sorted(rows, key=lambda r: r.fetched_at)
    latest = ordered[-1]
    older = [r for r in ordered if r.fetched_at < latest.fetched_at]
    if not older:
        return None
    target = now - _MONTH_AGO_TARGET
    aged = [r for r in older if (now - r.fetched_at) >= _MONTH_AGO_MIN_AGE]
    pool = aged or older
    return min(pool, key=lambda r: abs((r.fetched_at - target).total_seconds()))


def upsert_price_target_consensus(
    session: Session, item: dict
) -> PriceTargetConsensus:
    """Upsert latest consensus and retain a history point for month-ago diffs."""
    now = datetime.utcnow()
    symbol = item["symbol"]
    payload = _consensus_payload(item)
    existing = session.scalar(
        select(PriceTargetConsensus).where(PriceTargetConsensus.symbol == symbol)
    )
    if existing:
        # Archive the previous latest before overwriting so month-ago can resolve.
        _append_consensus_history(
            session,
            symbol=symbol,
            payload=_consensus_row_payload(existing),
            fetched_at=existing.fetched_at or now,
        )
        for key in _CONSENSUS_FIELDS:
            if key in item:
                setattr(existing, key, item[key])
        existing.fetched_at = now
        _append_consensus_history(session, symbol=symbol, payload=payload, fetched_at=now)
        _prune_consensus_history(session, symbol)
        session.commit()
        session.refresh(existing)
        return existing
    row = PriceTargetConsensus(
        symbol=symbol,
        fetched_at=now,
        **payload,
    )
    session.add(row)
    _append_consensus_history(session, symbol=symbol, payload=payload, fetched_at=now)
    session.commit()
    session.refresh(row)
    return row


def list_month_ago_consensus_for_symbols(
    session: Session, symbols: list[str]
) -> dict[str, PriceTargetConsensusHistory]:
    """Best historical consensus near ~30 days ago for each symbol."""
    if not symbols:
        return {}
    rows = list(
        session.scalars(
            select(PriceTargetConsensusHistory).where(
                PriceTargetConsensusHistory.symbol.in_(symbols)
            )
        )
    )
    by_symbol: dict[str, list[PriceTargetConsensusHistory]] = {s: [] for s in symbols}
    for row in rows:
        by_symbol.setdefault(row.symbol, []).append(row)

    now = datetime.utcnow()
    out: dict[str, PriceTargetConsensusHistory] = {}
    for symbol, hist in by_symbol.items():
        picked = _pick_month_ago_row(hist, now=now)
        if picked is not None:
            out[symbol] = picked
            continue
        # Bootstrap: if history is thin, use current latest when it itself is aged.
        latest = session.scalar(
            select(PriceTargetConsensus).where(PriceTargetConsensus.symbol == symbol)
        )
        if (
            latest
            and latest.fetched_at
            and (now - latest.fetched_at) >= _MONTH_AGO_MIN_AGE
        ):
            # Synthetic history-shaped object is not needed; skip until 2nd fetch.
            pass
    return out


def replace_institution_targets(
    session: Session, *, symbol: str, items: list[dict]
) -> int:
    """Replace all institution targets for a symbol with a fresh fetch."""
    now = datetime.utcnow()
    existing = list(
        session.scalars(
            select(InstitutionPriceTarget).where(
                InstitutionPriceTarget.symbol == symbol
            )
        )
    )
    for row in existing:
        session.delete(row)
    session.flush()
    for item in items:
        session.add(
            InstitutionPriceTarget(
                symbol=symbol,
                stock_code=item.get("stock_code", ""),
                stock_name=item.get("stock_name", ""),
                institution_uid=item["institution_uid"],
                institution_name=item.get("institution_name", ""),
                institution_en_name=item.get("institution_en_name", ""),
                institution_source_name=item.get("institution_source_name", ""),
                rating=item.get("rating"),
                rating_label=item.get("rating_label", ""),
                target_price=item.get("target_price"),
                recommendation_date_str=item.get("recommendation_date_str", ""),
                rating_url=item.get("rating_url", ""),
                update_time_str=item.get("update_time_str", ""),
                fetched_at=now,
            )
        )
    session.commit()
    return len(items)


def list_price_target_consensus_for_symbols(
    session: Session, symbols: list[str]
) -> dict[str, PriceTargetConsensus]:
    if not symbols:
        return {}
    rows = session.scalars(
        select(PriceTargetConsensus).where(PriceTargetConsensus.symbol.in_(symbols))
    )
    return {row.symbol: row for row in rows}


def list_stock_oracle_valuations_for_symbols(
    session: Session, symbols: list[str]
) -> dict[str, StockOracleValuation]:
    if not symbols:
        return {}
    rows = session.scalars(
        select(StockOracleValuation).where(StockOracleValuation.symbol.in_(symbols))
    )
    return {row.symbol: row for row in rows}


def list_institution_targets_for_symbols(
    session: Session, symbols: list[str]
) -> dict[str, list[InstitutionPriceTarget]]:
    if not symbols:
        return {}
    rows = session.scalars(
        select(InstitutionPriceTarget)
        .where(InstitutionPriceTarget.symbol.in_(symbols))
        .order_by(
            InstitutionPriceTarget.recommendation_date_str.desc(),
            InstitutionPriceTarget.institution_name.asc(),
        )
    )
    by_symbol: dict[str, list[InstitutionPriceTarget]] = {s: [] for s in symbols}
    for row in rows:
        by_symbol.setdefault(row.symbol, []).append(row)
    return by_symbol


def latest_price_target_fetched_at(
    session: Session, symbols: list[str]
) -> datetime | None:
    """Most recent fetch across consensus rows for the given symbols."""
    if not symbols:
        return None
    rows = session.scalars(
        select(PriceTargetConsensus.fetched_at)
        .where(PriceTargetConsensus.symbol.in_(symbols))
        .order_by(PriceTargetConsensus.fetched_at.desc())
        .limit(1)
    )
    return next(iter(rows), None)


def upsert_stock_oracle_valuation(
    session: Session, item: dict
) -> StockOracleValuation:
    now = datetime.utcnow()
    symbol = item["symbol"]
    existing = session.scalar(
        select(StockOracleValuation).where(StockOracleValuation.symbol == symbol)
    )
    fields = {
        "stock_code": item.get("stock_code", ""),
        "stock_name": item.get("stock_name", ""),
        "moat": item.get("moat") or "",
        "value": item.get("value"),
        "currency": item.get("currency") or "",
        "assess_pct": item.get("assess_pct"),
    }
    if existing:
        for key, val in fields.items():
            setattr(existing, key, val)
        existing.fetched_at = now
        session.commit()
        session.refresh(existing)
        return existing
    row = StockOracleValuation(symbol=symbol, fetched_at=now, **fields)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def save_strategy_check(session: Session, item: dict) -> StrategyCheck:
    row = StrategyCheck(
        symbol=item["symbol"],
        stock_code=item.get("stock_code") or "",
        stock_name=item.get("stock_name") or "",
        strategy=item["strategy"],
        conclusion=item.get("conclusion") or "",
        penalty=int(item.get("penalty") or 0),
        conditions_json=item.get("conditions_json") or "[]",
        fetched_at=item.get("fetched_at") or datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_recent_strategy_checks(
    session: Session, limit: int = 20
) -> list[StrategyCheck]:
    return list(
        session.scalars(
            select(StrategyCheck)
            .order_by(StrategyCheck.fetched_at.desc(), StrategyCheck.id.desc())
            .limit(limit)
        )
    )

