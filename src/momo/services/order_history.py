"""Aggregate order history + positions for the Orders web page."""

from __future__ import annotations

from datetime import date

from momo.adapters import orders as orders_adapter
from momo.adapters import quote as quote_adapter
from momo.config import get_settings
from momo.domain.options import parse_option_code
from momo.opend_client import OpenDError


def get_order_history(
    *,
    acc_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    trd_env: str | None = None,
) -> dict:
    settings = get_settings()
    if acc_id is None and settings.trd_acc_id:
        acc_id = settings.trd_acc_id
    env = (trd_env or settings.orders_trd_env).strip().upper()
    if env not in ("REAL", "SIMULATE"):
        env = settings.orders_trd_env

    snap = orders_adapter.fetch_trading_snapshot(
        acc_id=acc_id, start=start, end=end, trd_env=env
    )
    accounts = snap["accounts"]
    if acc_id and not snap["selected_accounts"]:
        return {
            "trd_env": snap["trd_env"],
            "accounts": accounts,
            "selected_acc_id": acc_id,
            "start": snap["start"],
            "end": snap["end"],
            "as_of": date.today().isoformat(),
            "stock_groups": [],
            "option_clusters": [],
            "totals": _empty_totals(),
            "error": f"No account {acc_id} for {snap['trd_env']}",
        }

    positions = snap["positions"]
    orders = [
        o
        for o in snap["orders"]
        if (o.get("status") or "").upper() != "CANCELLED_ALL"
    ]

    symbols = sorted(
        {
            *(p["code"] for p in positions if p.get("code")),
            *(o["code"] for o in orders if o.get("code")),
        }
    )
    underlyings: set[str] = set()
    option_by_code: dict[str, dict] = {}
    for code in symbols:
        info = parse_option_code(code)
        if not info:
            continue
        option_by_code[code] = {
            "expiry": info.expiry.isoformat(),
            "days_to_expiry": info.days_to_expiry,
            "option_type": info.option_type,
            "strike": info.strike,
            "underlying_root": info.underlying_root,
            "underlying_symbol": info.underlying_symbol,
            "multiplier": info.multiplier,
            "expired": info.days_to_expiry < 0,
        }
        underlyings.add(info.underlying_symbol)

    quote_symbols = sorted(set(symbols) | underlyings)
    snapshots: dict[str, dict] = {}
    if quote_symbols:
        try:
            for row in quote_adapter.get_snapshots(quote_symbols):
                snapshots[row["symbol"]] = row
        except OpenDError:
            pass

    groups = _only_open_positions(
        _build_groups(positions, orders, option_by_code, snapshots)
    )
    stock_groups, option_clusters = _partition_stocks_and_options(groups)
    option_groups = [c for cluster in option_clusters for c in cluster["contracts"]]
    return {
        "trd_env": snap["trd_env"],
        "accounts": accounts,
        "selected_acc_id": acc_id,
        "start": snap["start"],
        "end": snap["end"],
        "as_of": date.today().isoformat(),
        "stock_groups": stock_groups,
        "option_clusters": option_clusters,
        "totals": _totals(groups),
        "stock_totals": _totals(stock_groups),
        "option_totals": _totals(option_groups),
        "error": None,
    }


def _only_open_positions(groups: list[dict]) -> list[dict]:
    """Keep symbols with a non-zero open position; drop closed-history-only rows."""
    kept = []
    for g in groups:
        pos = g.get("position")
        qty = (pos or {}).get("qty") or 0.0
        if not qty:
            continue
        kept.append(g)
    return kept


def _build_groups(
    positions: list[dict],
    orders: list[dict],
    option_by_code: dict[str, dict],
    snapshots: dict[str, dict],
) -> list[dict]:
    by_code: dict[str, dict] = {}

    def ensure(code: str, name: str = "") -> dict:
        if code not in by_code:
            opt = option_by_code.get(code)
            by_code[code] = {
                "code": code,
                "name": name,
                "is_option": opt is not None,
                "option": opt,
                "position": None,
                "orders": [],
                "last_price": None,
                "underlying_last_price": None,
                "unrealized_pl": None,
                "pl_ratio": None,
                "market_val": None,
            }
        group = by_code[code]
        if name and not group["name"]:
            group["name"] = name
        return group

    for pos in positions:
        code = pos["code"]
        group = ensure(code, pos.get("name") or "")
        snap = snapshots.get(code) or {}
        last = snap.get("last_price")
        if last is None:
            last = pos.get("nominal_price")
        avg = pos.get("average_cost")
        qty = pos.get("qty") or 0.0
        multiplier = 100 if group["is_option"] else 1
        unrealized = pos.get("unrealized_pl")
        if unrealized is None and last is not None and avg is not None:
            unrealized = (last - avg) * qty * multiplier
        pl_ratio = pos.get("pl_ratio_avg_cost")
        if (
            pl_ratio is None
            and unrealized is not None
            and avg not in (None, 0)
            and qty != 0
        ):
            cost_basis = abs(avg * qty * multiplier)
            if cost_basis:
                pl_ratio = (unrealized / cost_basis) * 100.0

        market_val = pos.get("market_val")
        if market_val is None and last is not None:
            market_val = last * qty * multiplier

        group["position"] = {
            **pos,
            "last_price": last,
            "unrealized_pl": unrealized,
            "pl_ratio": pl_ratio,
            "market_val": market_val,
            "multiplier": multiplier,
        }
        group["last_price"] = last
        group["unrealized_pl"] = unrealized
        group["pl_ratio"] = pl_ratio
        group["market_val"] = market_val
        if group["option"]:
            und = group["option"]["underlying_symbol"]
            group["underlying_last_price"] = (snapshots.get(und) or {}).get(
                "last_price"
            )

    for order in orders:
        code = order["code"]
        group = ensure(code, order.get("name") or "")
        group["orders"].append(order)
        if group["last_price"] is None:
            group["last_price"] = (snapshots.get(code) or {}).get("last_price")
        if group["option"] and group["underlying_last_price"] is None:
            und = group["option"]["underlying_symbol"]
            group["underlying_last_price"] = (snapshots.get(und) or {}).get(
                "last_price"
            )

    return list(by_code.values())


def _partition_stocks_and_options(
    groups: list[dict],
) -> tuple[list[dict], list[dict]]:
    stocks = [g for g in groups if not g.get("is_option")]
    stocks.sort(key=lambda g: g["code"])

    by_underlying: dict[str, list[dict]] = {}
    for g in groups:
        if not g.get("is_option"):
            continue
        opt = g.get("option") or {}
        key = opt.get("underlying_symbol") or g["code"]
        by_underlying.setdefault(key, []).append(g)

    clusters: list[dict] = []
    for und_symbol, contracts in by_underlying.items():
        contracts.sort(
            key=lambda g: (
                (g.get("option") or {}).get("expiry") or "9999",
                (g.get("option") or {}).get("strike") or 0,
                (g.get("option") or {}).get("option_type") or "",
                g["code"],
            )
        )
        root = (contracts[0].get("option") or {}).get("underlying_root") or und_symbol
        nearest = (contracts[0].get("option") or {}).get("expiry") or "9999"
        und_price = next(
            (c.get("underlying_last_price") for c in contracts if c.get("underlying_last_price") is not None),
            None,
        )
        clusters.append(
            {
                "underlying_symbol": und_symbol,
                "underlying_root": root,
                "underlying_last_price": und_price,
                "nearest_expiry": nearest,
                "contracts": contracts,
            }
        )

    # Soonest expiry first; keep same-underlying contracts together.
    clusters.sort(key=lambda c: (c["nearest_expiry"], c["underlying_root"]))
    return stocks, clusters


def _totals(groups: list[dict]) -> dict:
    market_val = 0.0
    unrealized = 0.0
    open_positions = 0
    has_mv = False
    has_pl = False
    for g in groups:
        pos = g.get("position")
        if not pos:
            continue
        open_positions += 1
        if pos.get("market_val") is not None:
            market_val += pos["market_val"]
            has_mv = True
        if pos.get("unrealized_pl") is not None:
            unrealized += pos["unrealized_pl"]
            has_pl = True
    return {
        "open_positions": open_positions,
        "symbols": len(groups),
        "orders": sum(len(g["orders"]) for g in groups),
        "market_val": market_val if has_mv else None,
        "unrealized_pl": unrealized if has_pl else None,
    }


def _empty_totals() -> dict:
    return {
        "open_positions": 0,
        "symbols": 0,
        "orders": 0,
        "market_val": None,
        "unrealized_pl": None,
    }
