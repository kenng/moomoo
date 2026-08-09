from __future__ import annotations

import logging
from datetime import datetime, timezone

from momo.adapters import r2 as r2_adapter
from momo.config import bare_code
from momo.opend_client import OpenDError
from momo.services import (
    ai_news_summary,
    dividend_history,
    news_digest,
    order_history,
    price_targets,
)
from momo.watchlist import load_watchlist

logger = logging.getLogger(__name__)

EXPORT_VERSION = 1
_STOCK_NEWS_MARKET_ORDER = ("HK", "US", "MY")
_KLSE_NEWS_URL = "https://www.klsescreener.com/v2/news/stock/{code}"

_EMPTY_ORDERS_TOTALS = {
    "open_positions": 0,
    "symbols": 0,
    "orders": 0,
    "market_val": None,
    "unrealized_pl": None,
}


def _empty_orders_payload() -> dict:
    return {
        "trd_env": "",
        "accounts": [],
        "selected_acc_id": None,
        "start": "",
        "end": "",
        "as_of": "",
        "stock_groups": [],
        "option_clusters": [],
        "totals": dict(_EMPTY_ORDERS_TOTALS),
        "stock_totals": dict(_EMPTY_ORDERS_TOTALS),
        "option_totals": dict(_EMPTY_ORDERS_TOTALS),
        "error": None,
    }


def group_position_stocks_by_market(
    stock_groups: list[dict],
    option_clusters: list[dict] | None = None,
) -> list[dict]:
    """Group open stock (+ option underlying) positions as HK → US → MY."""
    by_symbol: dict[str, dict] = {}
    for g in stock_groups:
        symbol = (g.get("code") or "").strip().upper()
        if not symbol:
            continue
        market = symbol.split(".", 1)[0] if "." in symbol else ""
        code = bare_code(symbol)
        by_symbol[symbol] = {
            "code": code,
            "symbol": symbol,
            "name": g.get("name") or code,
            "market": market,
            "qty": (g.get("position") or {}).get("qty"),
            "option_contracts": 0,
            "klse_news_url": (
                _KLSE_NEWS_URL.format(code=code) if market == "MY" else None
            ),
        }

    for cluster in option_clusters or []:
        symbol = (cluster.get("underlying_symbol") or "").strip().upper()
        if not symbol:
            continue
        contracts = cluster.get("contracts") or []
        option_contracts = sum(
            1
            for c in contracts
            if ((c.get("position") or {}).get("qty") or 0)
        )
        if not option_contracts:
            continue
        market = symbol.split(".", 1)[0] if "." in symbol else ""
        code = bare_code(symbol)
        existing = by_symbol.get(symbol)
        if existing:
            existing["option_contracts"] = option_contracts
            continue
        root = (cluster.get("underlying_root") or code).strip()
        by_symbol[symbol] = {
            "code": code,
            "symbol": symbol,
            "name": root or code,
            "market": market,
            "qty": None,
            "option_contracts": option_contracts,
            "klse_news_url": (
                _KLSE_NEWS_URL.format(code=code) if market == "MY" else None
            ),
        }

    buckets: dict[str, list[dict]] = {m: [] for m in _STOCK_NEWS_MARKET_ORDER}
    other: list[dict] = []
    for row in by_symbol.values():
        if row["market"] in buckets:
            buckets[row["market"]].append(row)
        else:
            other.append(row)

    sections: list[dict] = []
    for market in _STOCK_NEWS_MARKET_ORDER:
        stocks = sorted(buckets[market], key=lambda s: s["code"])
        if stocks:
            sections.append({"market": market, "stocks": stocks})
    if other:
        sections.append(
            {
                "market": "OTHER",
                "stocks": sorted(other, key=lambda s: (s["market"], s["code"])),
            }
        )
    return sections


def build_orders_snapshot() -> dict:
    try:
        data = order_history.get_order_history()
        data.setdefault("error", None)
        return data
    except OpenDError as exc:
        payload = _empty_orders_payload()
        payload["error"] = str(exc)
        return payload


def build_holdings_news_snapshot(orders: dict | None = None) -> dict:
    orders = orders if orders is not None else build_orders_snapshot()
    sections = group_position_stocks_by_market(
        orders.get("stock_groups") or [],
        option_clusters=orders.get("option_clusters") or [],
    )
    symbols = [
        s["symbol"] for section in sections for s in section.get("stocks") or []
    ]
    summaries = ai_news_summary.get_summaries_for_symbols(symbols)
    for section in sections:
        for stock in section.get("stocks") or []:
            stock["ai_summary"] = summaries.get(stock["symbol"])
    return {
        "trd_env": orders.get("trd_env"),
        "accounts": orders.get("accounts") or [],
        "selected_acc_id": orders.get("selected_acc_id"),
        "sections": sections,
        "error": orders.get("error"),
    }


def build_dividends_snapshot() -> dict:
    try:
        data = dividend_history.get_dividend_history(show_all=True, sync=False)
        data.setdefault("error", None)
        return data
    except OpenDError as exc:
        return {
            "trd_env": "",
            "accounts": [],
            "selected_acc_id": None,
            "start": "",
            "end": "",
            "show_all": True,
            "as_of": "",
            "dividends": [],
            "new_dividends": [],
            "totals": {"count": 0, "by_currency": []},
            "sync": {
                "fetched_days": 0,
                "remaining_days": 0,
                "accounts_synced": 0,
                "fetched_dates": [],
            },
            "auto_sync": False,
            "error": str(exc),
        }


def build_stock_payload(stock: dict, *, provider: str = "all") -> dict:
    digest = news_digest.get_digest_for_code(
        stock["code"],
        market=stock.get("market"),
        provider=provider,
        limit=news_digest.FINNHUB_DIGEST_LIMIT,
    )
    summaries = {
        p: news_digest.get_digest_for_code(
            stock["code"],
            market=stock.get("market"),
            provider=p,
            limit=news_digest.FINNHUB_DIGEST_LIMIT,
        )
        for p in news_digest.NEWS_PROVIDERS
    }
    ai = ai_news_summary.get_summary_for_symbol(stock["symbol"])
    return {
        "stock": {
            "code": stock["code"],
            "symbol": stock["symbol"],
            "name": stock.get("name") or stock["code"],
            "market": stock.get("market"),
        },
        "digest": digest,
        "digests_by_provider": summaries,
        "ai_summary": ai,
    }


def collect_symbols_for_stocks(
    orders: dict | None = None,
) -> dict[str, dict]:
    """Map SYMBOL → stock dict from watchlist + open holdings."""
    by_symbol: dict[str, dict] = {}
    for stock in load_watchlist():
        by_symbol[stock["symbol"]] = stock

    orders = orders if orders is not None else build_orders_snapshot()
    sections = group_position_stocks_by_market(
        orders.get("stock_groups") or [],
        option_clusters=orders.get("option_clusters") or [],
    )
    for section in sections:
        for row in section.get("stocks") or []:
            symbol = row["symbol"]
            if symbol in by_symbol:
                continue
            by_symbol[symbol] = {
                "code": row["code"],
                "symbol": symbol,
                "name": row.get("name") or row["code"],
                "market": row.get("market"),
            }
    return by_symbol


def build_export_bundle() -> dict[str, dict | list]:
    """Build all R2 payloads keyed by relative object path (no prefix)."""
    synced_at = datetime.now(timezone.utc).isoformat()
    orders = build_orders_snapshot()
    holdings = build_holdings_news_snapshot(orders)
    home = price_targets.get_watchlist_price_targets()
    dividends = build_dividends_snapshot()

    stock_news: dict[str, list] = {}
    for provider in news_digest.NEWS_PROVIDERS:
        stock_news[provider] = news_digest.get_watchlist_digest(provider=provider)

    stock_map = collect_symbols_for_stocks(orders)
    stocks = {
        symbol: build_stock_payload(stock)
        for symbol, stock in stock_map.items()
    }
    stock_index = {
        symbol: {
            "code": stock["code"],
            "symbol": stock["symbol"],
            "name": stock.get("name") or stock["code"],
            "market": stock.get("market"),
        }
        for symbol, stock in stock_map.items()
    }

    return {
        "meta.json": {
            "synced_at": synced_at,
            "version": EXPORT_VERSION,
            "stock_count": len(stocks),
        },
        "home.json": home,
        "orders.json": orders,
        "holdings-news.json": holdings,
        "dividends.json": dividends,
        "stocks/index.json": stock_index,
        **{f"stock-news/{p}.json": digests for p, digests in stock_news.items()},
        **{f"stocks/{symbol}.json": payload for symbol, payload in stocks.items()},
    }


def publish_to_r2() -> dict:
    """Export digests and upload JSON objects to R2."""
    if not r2_adapter.r2_configured():
        raise r2_adapter.R2ConfigError(
            "R2 is not configured — set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY, and R2_BUCKET"
        )

    bundle = build_export_bundle()
    uploaded: list[str] = []
    for relative_key, payload in bundle.items():
        key = r2_adapter.put_json(relative_key, payload)
        uploaded.append(key)
        logger.info("uploaded %s", key)

    meta = bundle["meta.json"]
    return {
        "synced_at": meta["synced_at"],
        "version": meta["version"],
        "uploaded": uploaded,
        "count": len(uploaded),
    }
