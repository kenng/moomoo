from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from momo.adapters import finnhub as finnhub_adapter
from momo.adapters import news as news_adapter
from momo.adapters import quote as quote_adapter
from momo.config import get_settings
from momo.db.repo import (
    delete_news_older_than,
    latest_snapshot,
    list_news_for_symbol,
    list_top_news,
    save_snapshot,
    upsert_news_items,
)
from momo.db.session import get_session
from momo.domain.ranking import score_news_item
from momo.watchlist import load_watchlist, resolve_stock

logger = logging.getLogger(__name__)

NEWS_RETENTION_DAYS = 30
NEWS_PROVIDERS = ("all", "opend", "finnhub")
FINNHUB_DIGEST_LIMIT = 50


def normalize_news_provider(provider: str | None) -> str:
    value = (provider or "all").strip().lower()
    return value if value in NEWS_PROVIDERS else "all"


def _collect_item(
    item: dict,
    *,
    stock: dict,
    symbol: str,
    name: str,
    provider: str,
    seen_urls: set[str],
    collected: list[dict],
) -> None:
    url = item.get("url") or item.get("title")
    if not url or url in seen_urls:
        return
    seen_urls.add(url)
    related = item.get("related_securities") or []
    score = score_news_item(
        news_sub_type=item.get("news_sub_type", ""),
        view_count=int(item.get("view_count") or 0),
        publish_time=item.get("publish_time", ""),
        related_securities=related,
        symbol=symbol,
        stock_name=name,
        title=item.get("title", ""),
    )
    collected.append(
        {
            "symbol": symbol,
            "stock_code": stock["code"],
            "stock_name": name,
            "title": item.get("title", ""),
            "news_sub_type": item.get("news_sub_type", ""),
            "source": item.get("source", ""),
            "publish_time": item.get("publish_time", ""),
            "view_count": int(item.get("view_count") or 0),
            "related_securities": ",".join(related),
            "url": item.get("url", ""),
            "provider": provider,
            "importance_score": score,
        }
    )


def _fetch_finnhub_news(stock: dict) -> list[dict]:
    settings = get_settings()
    if not settings.finnhub_api_key:
        return []

    to_day = date.today()
    from_day = to_day - timedelta(days=max(1, settings.finnhub_news_lookback_days))
    fh_symbol = finnhub_adapter.to_finnhub_symbol(stock)
    try:
        return finnhub_adapter.company_news(
            fh_symbol,
            from_date=from_day.isoformat(),
            to_date=to_day.isoformat(),
        )
    except finnhub_adapter.FinnhubError as exc:
        logger.warning("Finnhub news skipped for %s: %s", fh_symbol, exc)
        return []


def _dedupe_by_url(items: list[dict]) -> list[dict]:
    by_url: dict[str, dict] = {}
    for item in items:
        key = item.get("url") or item.get("title") or ""
        if not key:
            continue
        prev = by_url.get(key)
        if prev is None or item["importance_score"] > prev["importance_score"]:
            by_url[key] = item
    return sorted(by_url.values(), key=lambda x: x["importance_score"], reverse=True)


def _items_to_store(
    opend_items: list[dict], finnhub_items: list[dict], top_n: int
) -> list[dict]:
    """OpenD top-N plus all Finnhub rows; on URL clash keep the higher score."""
    opend_top = sorted(
        opend_items, key=lambda x: x["importance_score"], reverse=True
    )[:top_n]
    return _dedupe_by_url(finnhub_items + opend_top)


def refresh_stock_news(stock: dict, max_count: int | None = None) -> dict:
    settings = get_settings()
    max_count = max_count or settings.news_max_count
    symbol = stock["symbol"]
    name = stock.get("name") or stock["code"]

    keywords = [symbol, name]
    opend_seen: set[str] = set()
    opend_items: list[dict] = []
    for keyword in keywords:
        for item in news_adapter.search_news(keyword, max_count=max_count):
            _collect_item(
                item,
                stock=stock,
                symbol=symbol,
                name=name,
                provider="opend",
                seen_urls=opend_seen,
                collected=opend_items,
            )

    fh_seen: set[str] = set()
    finnhub_items: list[dict] = []
    for item in _fetch_finnhub_news(stock):
        _collect_item(
            item,
            stock=stock,
            symbol=symbol,
            name=name,
            provider="finnhub",
            seen_urls=fh_seen,
            collected=finnhub_items,
        )

    to_store = _items_to_store(opend_items, finnhub_items, settings.news_top_n)
    all_unique = _dedupe_by_url(opend_items + finnhub_items)
    display = all_unique[: settings.news_top_n]

    session = get_session()
    try:
        inserted = upsert_news_items(session, to_store)
        purged = delete_news_older_than(
            session,
            cutoff=datetime.now(timezone.utc) - timedelta(days=NEWS_RETENTION_DAYS),
        )
        snapshots = quote_adapter.get_snapshots([symbol])
        snapshot = snapshots[0] if snapshots else None
        if snapshot:
            save_snapshot(session, snapshot)
    finally:
        session.close()

    return {
        "symbol": symbol,
        "name": name,
        "fetched": len(all_unique),
        "stored_top": len(to_store),
        "inserted": inserted,
        "purged": purged,
        "snapshot": snapshot,
        "news": display,
    }


def refresh_watchlist() -> list[dict]:
    results = []
    for stock in load_watchlist():
        results.append(refresh_stock_news(stock))
    return results


def get_digest_for_code(
    code: str,
    limit: int | None = None,
    market: str | None = None,
    provider: str | None = None,
) -> dict:
    settings = get_settings()
    source = normalize_news_provider(provider)
    db_provider = None if source == "all" else source
    if limit is None:
        limit = (
            FINNHUB_DIGEST_LIMIT
            if source == "finnhub"
            else settings.news_top_n
        )
    stock = resolve_stock(code, market=market)
    symbol = stock["symbol"]
    session = get_session()
    try:
        news = list_news_for_symbol(
            session, symbol, limit=limit, provider=db_provider
        )
        snap = latest_snapshot(session, symbol)
        return {
            "symbol": symbol,
            "snapshot": _snap_dict(snap),
            "provider": source,
            "news": [_news_dict(n) for n in news],
        }
    finally:
        session.close()


def get_watchlist_digest(
    limit_per_stock: int | None = None, provider: str | None = None
) -> list[dict]:
    settings = get_settings()
    source = normalize_news_provider(provider)
    if limit_per_stock is None:
        limit_per_stock = (
            FINNHUB_DIGEST_LIMIT
            if source == "finnhub"
            else settings.news_top_n
        )
    digests = []
    for stock in load_watchlist():
        digests.append(
            {
                **stock,
                **get_digest_for_code(
                    stock["code"],
                    limit=limit_per_stock,
                    market=stock["market"],
                    provider=source,
                ),
            }
        )
    return digests


def get_all_top_news(limit: int = 50, provider: str | None = None) -> list[dict]:
    source = normalize_news_provider(provider)
    db_provider = None if source == "all" else source
    session = get_session()
    try:
        return [
            _news_dict(n)
            for n in list_top_news(session, limit=limit, provider=db_provider)
        ]
    finally:
        session.close()


def _news_dict(n) -> dict:
    return {
        "id": n.id,
        "symbol": n.symbol,
        "stock_code": n.stock_code,
        "stock_name": n.stock_name,
        "title": n.title,
        "news_sub_type": n.news_sub_type,
        "source": n.source,
        "publish_time": n.publish_time,
        "view_count": n.view_count,
        "related_securities": n.related_securities,
        "url": n.url,
        "provider": getattr(n, "provider", None) or "opend",
        "importance_score": n.importance_score,
        "fetched_at": n.fetched_at.isoformat() if n.fetched_at else None,
    }


def _snap_dict(snap) -> dict | None:
    if snap is None:
        return None
    return {
        "symbol": snap.symbol,
        "last_price": snap.last_price,
        "change_rate": snap.change_rate,
        "volume": snap.volume,
        "turnover": snap.turnover,
        "fetched_at": snap.fetched_at.isoformat() if snap.fetched_at else None,
    }
