from __future__ import annotations

from momo.adapters import news as news_adapter
from momo.adapters import quote as quote_adapter
from momo.config import get_settings
from momo.db.repo import (
    latest_snapshot,
    list_news_for_symbol,
    list_top_news,
    save_snapshot,
    upsert_news_items,
)
from momo.db.session import get_session
from momo.domain.ranking import score_news_item
from momo.watchlist import load_watchlist, resolve_stock


def refresh_stock_news(stock: dict, max_count: int | None = None) -> dict:
    settings = get_settings()
    max_count = max_count or settings.news_max_count
    symbol = stock["symbol"]
    name = stock.get("name") or stock["code"]

    keywords = [symbol, name]
    seen_urls: set[str] = set()
    collected: list[dict] = []

    for keyword in keywords:
        for item in news_adapter.search_news(keyword, max_count=max_count):
            url = item.get("url") or item.get("title")
            if not url or url in seen_urls:
                continue
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
                    "importance_score": score,
                }
            )

    collected.sort(key=lambda x: x["importance_score"], reverse=True)
    top = collected[: settings.news_top_n]

    session = get_session()
    try:
        inserted = upsert_news_items(session, top)
        snapshots = quote_adapter.get_snapshots([symbol])
        snapshot = snapshots[0] if snapshots else None
        if snapshot:
            save_snapshot(session, snapshot)
    finally:
        session.close()

    return {
        "symbol": symbol,
        "name": name,
        "fetched": len(collected),
        "stored_top": len(top),
        "inserted": inserted,
        "snapshot": snapshot,
        "news": top,
    }


def refresh_watchlist() -> list[dict]:
    results = []
    for stock in load_watchlist():
        results.append(refresh_stock_news(stock))
    return results


def get_digest_for_code(
    code: str, limit: int | None = None, market: str | None = None
) -> dict:
    settings = get_settings()
    limit = limit or settings.news_top_n
    stock = resolve_stock(code, market=market)
    symbol = stock["symbol"]
    session = get_session()
    try:
        news = list_news_for_symbol(session, symbol, limit=limit)
        snap = latest_snapshot(session, symbol)
        return {
            "symbol": symbol,
            "snapshot": _snap_dict(snap),
            "news": [_news_dict(n) for n in news],
        }
    finally:
        session.close()


def get_watchlist_digest(limit_per_stock: int | None = None) -> list[dict]:
    settings = get_settings()
    limit_per_stock = limit_per_stock or settings.news_top_n
    digests = []
    for stock in load_watchlist():
        digests.append(
            {
                **stock,
                **get_digest_for_code(
                    stock["code"],
                    limit=limit_per_stock,
                    market=stock["market"],
                ),
            }
        )
    return digests


def get_all_top_news(limit: int = 50) -> list[dict]:
    session = get_session()
    try:
        return [_news_dict(n) for n in list_top_news(session, limit=limit)]
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
