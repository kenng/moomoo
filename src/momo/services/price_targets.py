from __future__ import annotations

import logging

from momo.adapters import quote as quote_adapter
from momo.adapters import research as research_adapter
from momo.db.repo import (
    latest_price_target_fetched_at,
    latest_snapshot,
    list_institution_targets_for_symbols,
    list_price_target_consensus_for_symbols,
    replace_institution_targets,
    save_snapshot,
    upsert_price_target_consensus,
)
from momo.db.session import get_session
from momo.opend_client import OpenDError
from momo.watchlist import load_watchlist

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict:
    return {
        "symbol": row.symbol,
        "stock_code": row.stock_code,
        "stock_name": row.stock_name,
        "highest": row.highest,
        "average": row.average,
        "lowest": row.lowest,
        "rating": row.rating,
        "rating_label": row.rating_label,
        "total_analysts": row.total_analysts,
        "update_time_str": row.update_time_str,
        "buy": row.buy,
        "hold": row.hold,
        "sell": row.sell,
        "strong_buy": row.strong_buy,
        "underperform": row.underperform,
        "fetched_at": row.fetched_at,
    }


def _inst_to_dict(row) -> dict:
    return {
        "institution_uid": row.institution_uid,
        "institution_name": row.institution_name,
        "institution_en_name": row.institution_en_name,
        "institution_source_name": row.institution_source_name,
        "rating": row.rating,
        "rating_label": row.rating_label,
        "target_price": row.target_price,
        "recommendation_date_str": row.recommendation_date_str,
        "rating_url": row.rating_url,
        "update_time_str": row.update_time_str,
        "fetched_at": row.fetched_at,
    }


def refresh_watchlist_targets() -> dict:
    """Fetch consensus + institution targets for the watchlist and persist."""
    stocks = load_watchlist()
    refreshed = 0
    institution_rows = 0
    errors: list[str] = []

    with get_session() as session:
        try:
            snaps = quote_adapter.get_snapshots([s["symbol"] for s in stocks])
            for snap in snaps:
                save_snapshot(session, snap)
        except OpenDError as exc:
            logger.warning("snapshot refresh failed: %s", exc)

        for stock in stocks:
            symbol = stock["symbol"]
            try:
                consensus = research_adapter.get_analyst_consensus(symbol)
                if consensus:
                    upsert_price_target_consensus(
                        session,
                        {
                            **consensus,
                            "stock_code": stock["code"],
                            "stock_name": stock.get("name") or stock["code"],
                        },
                    )
                    refreshed += 1
                institutions = research_adapter.get_institution_targets(symbol)
                for item in institutions:
                    item["stock_code"] = stock["code"]
                    item["stock_name"] = stock.get("name") or stock["code"]
                institution_rows += replace_institution_targets(
                    session, symbol=symbol, items=institutions
                )
            except OpenDError as exc:
                logger.warning("price target refresh failed for %s: %s", symbol, exc)
                errors.append(f"{symbol}: {exc}")

    return {
        "ok": not errors,
        "refreshed": refreshed,
        "institution_rows": institution_rows,
        "errors": errors,
    }


def get_watchlist_price_targets() -> dict:
    """Return cached price targets for the watchlist, plus last-updated time."""
    stocks = load_watchlist()
    symbols = [s["symbol"] for s in stocks]
    with get_session() as session:
        consensus_map = list_price_target_consensus_for_symbols(session, symbols)
        institution_map = list_institution_targets_for_symbols(session, symbols)
        last_updated = latest_price_target_fetched_at(session, symbols)
        items = []
        for stock in stocks:
            symbol = stock["symbol"]
            consensus = consensus_map.get(symbol)
            institutions = [
                _inst_to_dict(r) for r in institution_map.get(symbol) or []
            ]
            snap = latest_snapshot(session, symbol)
            items.append(
                {
                    "symbol": symbol,
                    "code": stock["code"],
                    "name": stock.get("name") or stock["code"],
                    "market": stock.get("market") or "",
                    "snapshot": (
                        {
                            "last_price": snap.last_price,
                            "change_rate": snap.change_rate,
                        }
                        if snap
                        else None
                    ),
                    "consensus": _row_to_dict(consensus) if consensus else None,
                    "institutions": institutions,
                    "fetched_at": consensus.fetched_at if consensus else None,
                }
            )
    return {"items": items, "last_updated": last_updated}
