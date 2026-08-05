"""OpenD trade queries: accounts, positions, today's + historical orders."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from momo.config import get_settings
from momo.opend_client import OpenDError, trade_context

# OpenD rejects history_order_list_query ranges longer than this.
_MAX_HISTORY_DAYS = 360


def fetch_trading_snapshot(
    *,
    acc_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    trd_env: str | None = None,
) -> dict:
    """One trade connection: accounts + positions + orders for the env."""
    import moomoo as ft

    settings = get_settings()
    env = _trd_env(ft, trd_env or settings.orders_trd_env)
    end_d = _parse_day(end) or date.today()
    start_d = _parse_day(start) or (end_d - timedelta(days=_MAX_HISTORY_DAYS - 1))
    if start_d > end_d:
        start_d, end_d = end_d, start_d

    with trade_context() as ctx:
        accounts = _accounts_from_ctx(ctx, ft, env)
        selected = (
            [a for a in accounts if a["acc_id"] == acc_id] if acc_id else accounts
        )

        positions: list[dict] = []
        orders: list[dict] = []
        skipped: list[dict] = []
        for acc in selected:
            aid = acc["acc_id"]
            try:
                positions.extend(_positions_from_ctx(ctx, ft, env, aid))
                orders.extend(
                    _orders_from_ctx(
                        ctx, ft, env, aid, start=start_d, end=end_d
                    )
                )
            except OpenDError as exc:
                # e.g. IPO / cash sub-accounts that reject position_list_query
                skipped.append({"acc_id": aid, "reason": str(exc)})
                continue

    orders.sort(key=lambda o: o.get("create_time") or "", reverse=True)
    skipped_ids = {s["acc_id"] for s in skipped}
    usable_selected = [a for a in selected if a["acc_id"] not in skipped_ids]
    return {
        "trd_env": _enum_name(env) or settings.orders_trd_env,
        "accounts": [a for a in accounts if a["acc_id"] not in skipped_ids],
        "selected_accounts": usable_selected,
        "positions": positions,
        "orders": orders,
        "skipped_accounts": skipped,
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
    }


def list_accounts(trd_env: str | None = None) -> list[dict]:
    import moomoo as ft

    settings = get_settings()
    env = _trd_env(ft, trd_env or settings.orders_trd_env)
    with trade_context() as ctx:
        return _accounts_from_ctx(ctx, ft, env)


def list_positions(acc_id: int, trd_env: str | None = None) -> list[dict]:
    import moomoo as ft

    settings = get_settings()
    env = _trd_env(ft, trd_env or settings.orders_trd_env)
    with trade_context() as ctx:
        return _positions_from_ctx(ctx, ft, env, acc_id)


def list_orders(
    acc_id: int,
    trd_env: str | None = None,
    *,
    start: str | None = None,
    end: str | None = None,
    code: str | None = None,
) -> list[dict]:
    import moomoo as ft

    settings = get_settings()
    env = _trd_env(ft, trd_env or settings.orders_trd_env)
    end_d = _parse_day(end) or date.today()
    start_d = _parse_day(start) or (end_d - timedelta(days=_MAX_HISTORY_DAYS - 1))
    with trade_context() as ctx:
        return _orders_from_ctx(
            ctx, ft, env, acc_id, start=start_d, end=end_d, code=code
        )


def _accounts_from_ctx(ctx, ft, env) -> list[dict]:
    ret, data = ctx.get_acc_list()
    if ret != ft.RET_OK:
        raise OpenDError(f"get_acc_list failed: {data}")
    if data is None or getattr(data, "empty", True):
        return []

    env_name = _enum_name(env)
    rows = []
    for _, row in data.iterrows():
        row_env = _enum_name(row.get("trd_env"))
        if row_env and env_name and row_env != env_name:
            continue
        acc_id = _int(row.get("acc_id"))
        if acc_id is None:
            continue
        rows.append(
            {
                "acc_id": acc_id,
                "trd_env": row_env or env_name,
                "acc_type": _enum_name(row.get("acc_type")),
                "sim_acc_type": _enum_name(row.get("sim_acc_type")),
                "markets": _markets(row.get("trdmarket_auth")),
            }
        )
    return rows


def _positions_from_ctx(ctx, ft, env, acc_id: int) -> list[dict]:
    ret, data = ctx.position_list_query(
        trd_env=env, acc_id=acc_id, refresh_cache=True
    )
    if ret != ft.RET_OK:
        raise OpenDError(f"position_list_query failed: {data}")
    if data is None or getattr(data, "empty", True):
        return []

    rows = []
    for _, row in data.iterrows():
        code = str(row.get("code") or "")
        rows.append(
            {
                "acc_id": acc_id,
                "code": code,
                "name": str(row.get("stock_name") or ""),
                "qty": _float(row.get("qty")) or 0.0,
                "can_sell_qty": _float(row.get("can_sell_qty")),
                "average_cost": _float(row.get("average_cost")),
                "nominal_price": _float(row.get("nominal_price")),
                "market_val": _float(row.get("market_val")),
                "unrealized_pl": _float(row.get("unrealized_pl")),
                "pl_ratio_avg_cost": _float(row.get("pl_ratio_avg_cost")),
                "realized_pl": _float(row.get("realized_pl")),
                "today_pl_val": _float(row.get("today_pl_val")),
            }
        )
    return rows


def _orders_from_ctx(
    ctx,
    ft,
    env,
    acc_id: int,
    *,
    start: date,
    end: date,
    code: str | None = None,
) -> list[dict]:
    by_id: dict[str, dict] = {}

    # Today's book (includes working orders not yet in history).
    today_kwargs: dict = {
        "trd_env": env,
        "acc_id": acc_id,
        "refresh_cache": True,
    }
    if code:
        today_kwargs["code"] = code
    ret, data = ctx.order_list_query(**today_kwargs)
    if ret != ft.RET_OK:
        raise OpenDError(f"order_list_query failed: {data}")
    for order in _rows_to_orders(data, acc_id):
        by_id[order["order_id"]] = order

    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=_MAX_HISTORY_DAYS - 1), end)
        kwargs: dict = {
            "trd_env": env,
            "acc_id": acc_id,
            "start": cursor.isoformat(),
            "end": chunk_end.isoformat(),
        }
        if code:
            kwargs["code"] = code
        ret, data = ctx.history_order_list_query(**kwargs)
        if ret != ft.RET_OK:
            raise OpenDError(f"history_order_list_query failed: {data}")
        for order in _rows_to_orders(data, acc_id):
            by_id[order["order_id"]] = order
        cursor = chunk_end + timedelta(days=1)

    orders = list(by_id.values())
    orders.sort(key=lambda o: o.get("create_time") or "", reverse=True)
    return orders


def _rows_to_orders(data, acc_id: int) -> list[dict]:
    if data is None or getattr(data, "empty", True):
        return []
    rows = []
    for _, row in data.iterrows():
        order_id = str(row.get("order_id") or "")
        if not order_id:
            continue
        rows.append(
            {
                "acc_id": acc_id,
                "order_id": order_id,
                "code": str(row.get("code") or ""),
                "name": str(row.get("stock_name") or ""),
                "side": _enum_name(row.get("trd_side")),
                "status": _enum_name(row.get("order_status")),
                "order_type": _enum_name(row.get("order_type")),
                "qty": _float(row.get("qty")) or 0.0,
                "price": _float(row.get("price")),
                "dealt_qty": _float(row.get("dealt_qty")) or 0.0,
                "dealt_avg_price": _float(row.get("dealt_avg_price")),
                "currency": str(row.get("currency") or ""),
                "create_time": str(row.get("create_time") or ""),
                "updated_time": str(row.get("updated_time") or ""),
                "remark": str(row.get("remark") or ""),
            }
        )
    return rows


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _trd_env(ft, name: str):
    key = (name or "SIMULATE").strip().upper()
    return getattr(ft.TrdEnv, key, ft.TrdEnv.SIMULATE)


def _enum_name(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "name"):
        return str(value.name)
    text = str(value).strip()
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text


def _markets(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [_enum_name(v) for v in value if v is not None]
    return [_enum_name(value)]


def _float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in ("", "N/A", "NONE", "NAN"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    """Parse account ids without float() — large acc_ids lose precision via float."""
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return int(value)
        text = str(value).strip()
        if not text or text.upper() in ("N/A", "NONE", "NAN"):
            return None
        if "." in text:
            text = text.split(".", 1)[0]
        return int(text)
    except (TypeError, ValueError):
        return None
