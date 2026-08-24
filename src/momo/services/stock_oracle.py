from __future__ import annotations

import logging

from momo.adapters.stock_oracle import (
    StockOracleError,
    get_oracle_valuation,
    oracle_browser_page,
)
from momo.db.repo import upsert_stock_oracle_valuation
from momo.db.session import get_session
from momo.watchlist import load_watchlist

logger = logging.getLogger(__name__)


def _market_of(stock: dict) -> str:
    market = (stock.get("market") or "").upper()
    symbol = (stock.get("symbol") or "").strip().upper()
    if not market and "." in symbol:
        market = symbol.split(".", 1)[0]
    return market


def refresh_watchlist_oracle() -> dict:
    """Fetch Stock Oracle valuations for US names on the local watchlist (no OpenD)."""
    stocks = load_watchlist()
    us_stocks = [s for s in stocks if _market_of(s) == "US"]
    skipped = len(stocks) - len(us_stocks)
    refreshed = 0
    errors: list[str] = []

    if not us_stocks:
        return {
            "ok": True,
            "refreshed": 0,
            "skipped": skipped,
            "errors": errors,
            "source": "watchlist",
            "symbols": 0,
        }

    with get_session() as session, oracle_browser_page() as page:
        for stock in us_stocks:
            symbol = stock["symbol"]
            try:
                valuation = get_oracle_valuation(symbol, page=page)
            except (StockOracleError, ValueError) as exc:
                logger.warning("stock oracle refresh failed for %s: %s", symbol, exc)
                errors.append(f"{symbol}: {exc}")
                continue
            if not valuation:
                errors.append(f"{symbol}: no Stock Oracle data")
                continue
            upsert_stock_oracle_valuation(
                session,
                {
                    **valuation,
                    "stock_code": stock.get("code") or stock["symbol"],
                    "stock_name": stock.get("name") or stock.get("code") or "",
                },
            )
            refreshed += 1

    return {
        "ok": not errors,
        "refreshed": refreshed,
        "skipped": skipped,
        "errors": errors,
        "source": "watchlist",
        "symbols": len(us_stocks),
    }
