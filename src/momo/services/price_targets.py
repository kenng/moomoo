from __future__ import annotations

import logging

from momo.adapters import orders as orders_adapter
from momo.adapters import quote as quote_adapter
from momo.adapters import research as research_adapter
from momo.config import bare_code, get_settings
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
from momo.domain.options import is_option_code
from momo.opend_client import OpenDError
from momo.watchlist import load_watchlist

logger = logging.getLogger(__name__)

# Portfolio weight bands (lower inclusive, upper exclusive except top).
_WEIGHT_BANDS: list[tuple[float, float | None, str]] = [
    (20.0, None, "20%+ of portfolio"),
    (10.0, 20.0, "10–20% of portfolio"),
    (5.0, 10.0, "5–10% of portfolio"),
    (0.0, 5.0, "Under 5% of portfolio"),
]


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


def _stock_positions_from_opend(
    *,
    acc_id: int | None = None,
    trd_env: str | None = None,
) -> tuple[list[dict], dict]:
    """Open stock/ETF positions aggregated by symbol (options excluded)."""
    settings = get_settings()
    if acc_id is None and settings.trd_acc_id:
        acc_id = settings.trd_acc_id
    snap = orders_adapter.fetch_positions(acc_id=acc_id, trd_env=trd_env)
    by_symbol: dict[str, dict] = {}
    for pos in snap.get("positions") or []:
        symbol = (pos.get("code") or "").strip().upper()
        if not symbol or is_option_code(symbol):
            continue
        qty = pos.get("qty") or 0.0
        if not qty:
            continue
        market = symbol.split(".", 1)[0] if "." in symbol else ""
        code = bare_code(symbol)
        market_val = pos.get("market_val")
        if market_val is None:
            last = pos.get("nominal_price")
            if last is not None:
                market_val = last * qty
        avg_cost = pos.get("average_cost")
        unrealized = pos.get("unrealized_pl")
        existing = by_symbol.get(symbol)
        if existing:
            prev_qty = existing["qty"]
            existing["qty"] += qty
            if market_val is not None:
                existing["market_val"] = (existing["market_val"] or 0.0) + market_val
            if unrealized is not None:
                existing["unrealized_pl"] = (
                    (existing["unrealized_pl"] or 0.0) + unrealized
                )
            if avg_cost is not None and qty:
                cost_sum = (existing.get("_cost_sum") or 0.0) + (avg_cost * qty)
                existing["_cost_sum"] = cost_sum
                if existing["qty"]:
                    existing["average_cost"] = cost_sum / existing["qty"]
            elif existing.get("average_cost") is None and avg_cost is not None:
                existing["average_cost"] = avg_cost
            if pos.get("nominal_price") is not None:
                existing["nominal_price"] = pos["nominal_price"]
            if not existing.get("name") and pos.get("name"):
                existing["name"] = pos["name"]
            # Keep pl_ratio coherent after merges.
            if (
                existing.get("unrealized_pl") is not None
                and existing.get("average_cost") not in (None, 0)
                and existing["qty"]
            ):
                basis = abs(existing["average_cost"] * existing["qty"])
                if basis:
                    existing["pl_ratio"] = (existing["unrealized_pl"] / basis) * 100.0
            elif prev_qty == 0:
                existing["pl_ratio"] = pos.get("pl_ratio_avg_cost")
            continue
        by_symbol[symbol] = {
            "symbol": symbol,
            "code": code,
            "name": pos.get("name") or code,
            "market": market,
            "qty": qty,
            "market_val": market_val,
            "average_cost": avg_cost,
            "nominal_price": pos.get("nominal_price"),
            "unrealized_pl": unrealized,
            "pl_ratio": pos.get("pl_ratio_avg_cost"),
            "_cost_sum": (avg_cost * qty) if avg_cost is not None and qty else 0.0,
        }
    rows = []
    for row in by_symbol.values():
        row.pop("_cost_sum", None)
        rows.append(row)
    meta = {
        "trd_env": snap.get("trd_env"),
        "selected_acc_id": snap.get("selected_acc_id"),
        "accounts": snap.get("accounts") or [],
    }
    return rows, meta


def _stocks_for_targets(
    *,
    acc_id: int | None = None,
    trd_env: str | None = None,
) -> tuple[list[dict], dict]:
    """Prefer live positions; fall back to watchlist if OpenD is unavailable."""
    try:
        positions, meta = _stock_positions_from_opend(acc_id=acc_id, trd_env=trd_env)
        if positions:
            return positions, {**meta, "source": "positions"}
    except OpenDError as exc:
        logger.warning("position fetch failed for price targets: %s", exc)
        meta_err = {"source": "watchlist", "error": str(exc)}
    else:
        meta_err = {
            **meta,
            "source": "watchlist",
            "error": "No open stock positions; showing watchlist.",
        }

    stocks = []
    for stock in load_watchlist():
        symbol = stock["symbol"]
        stocks.append(
            {
                "symbol": symbol,
                "code": stock["code"],
                "name": stock.get("name") or stock["code"],
                "market": stock.get("market") or "",
                "qty": None,
                "market_val": None,
                "average_cost": None,
                "nominal_price": None,
                "unrealized_pl": None,
                "pl_ratio": None,
            }
        )
    return stocks, meta_err


def _weight_band(weight_pct: float | None) -> str:
    if weight_pct is None:
        return "Watchlist"
    for low, high, label in _WEIGHT_BANDS:
        if weight_pct >= low and (high is None or weight_pct < high):
            return label
    return "Under 5% of portfolio"


def _attach_weights(stocks: list[dict]) -> list[dict]:
    total = sum(
        (s["market_val"] or 0.0)
        for s in stocks
        if s.get("market_val") is not None and (s.get("market_val") or 0) > 0
    )
    out = []
    for stock in stocks:
        row = dict(stock)
        mv = row.get("market_val")
        if total > 0 and mv is not None:
            row["weight_pct"] = (mv / total) * 100.0
        else:
            row["weight_pct"] = None
        row["weight_band"] = _weight_band(row["weight_pct"])
        out.append(row)
    return out


def _group_by_weight(items: list[dict]) -> list[dict]:
    order = [label for _, _, label in _WEIGHT_BANDS] + ["Watchlist"]
    buckets: dict[str, list[dict]] = {label: [] for label in order}
    for item in items:
        band = item.get("weight_band") or "Watchlist"
        buckets.setdefault(band, []).append(item)

    groups = []
    for label in order:
        rows = buckets.get(label) or []
        if not rows:
            continue
        rows.sort(
            key=lambda r: (
                -(r.get("weight_pct") if r.get("weight_pct") is not None else -1.0),
                r.get("code") or "",
            )
        )
        groups.append(
            {
                "label": label,
                "count": len(rows),
                "weight_pct": sum(
                    (r.get("weight_pct") or 0.0) for r in rows if r.get("weight_pct")
                )
                or None,
                "items": rows,
            }
        )
    return groups


def _upside_pct(average: float | None, last_price: float | None) -> float | None:
    if average is None or last_price in (None, 0):
        return None
    return ((average - last_price) / last_price) * 100.0


def refresh_watchlist_targets(
    *,
    acc_id: int | None = None,
    trd_env: str | None = None,
) -> dict:
    """Fetch consensus + institution targets for open positions (or watchlist)."""
    stocks, meta = _stocks_for_targets(acc_id=acc_id, trd_env=trd_env)
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
        "source": meta.get("source"),
        "symbols": len(stocks),
    }


def get_watchlist_price_targets(
    *,
    acc_id: int | None = None,
    trd_env: str | None = None,
) -> dict:
    """Return cached price targets for open positions, grouped by portfolio weight."""
    stocks, meta = _stocks_for_targets(acc_id=acc_id, trd_env=trd_env)
    stocks = _attach_weights(stocks)
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
            # Prefer live OpenD position mark; fall back to cached quote snapshot.
            last_price = stock.get("nominal_price")
            change_rate = None
            if snap:
                if last_price is None:
                    last_price = snap.last_price
                change_rate = snap.change_rate
            consensus_dict = _row_to_dict(consensus) if consensus else None
            avg = consensus_dict["average"] if consensus_dict else None
            cost = stock.get("average_cost")
            items.append(
                {
                    "symbol": symbol,
                    "code": stock["code"],
                    "name": stock.get("name") or stock["code"],
                    "market": stock.get("market") or "",
                    "qty": stock.get("qty"),
                    "market_val": stock.get("market_val"),
                    "average_cost": cost,
                    "unrealized_pl": stock.get("unrealized_pl"),
                    "pl_ratio": stock.get("pl_ratio"),
                    "weight_pct": stock.get("weight_pct"),
                    "weight_band": stock.get("weight_band"),
                    "snapshot": (
                        {
                            "last_price": last_price,
                            "change_rate": change_rate,
                        }
                        if last_price is not None or change_rate is not None
                        else None
                    ),
                    "consensus": consensus_dict,
                    "upside_pct": _upside_pct(avg, last_price),
                    "vs_cost_pct": _upside_pct(last_price, cost),
                    "institutions": institutions,
                    "fetched_at": consensus.fetched_at if consensus else None,
                }
            )
    return {
        "items": items,
        "groups": _group_by_weight(items),
        "last_updated": last_updated,
        "source": meta.get("source"),
        "trd_env": meta.get("trd_env"),
        "error": meta.get("error"),
    }
