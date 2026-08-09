"""Sync OpenD data into Google Sheets tabs."""

from __future__ import annotations

from momo.adapters import google_sheets as sheets_adapter
from momo.adapters import orders as orders_adapter
from momo.config import get_settings
from momo.domain.options import is_option_code

STOCK_ORDER_HEADERS = [
    "acc_id",
    "order_id",
    "code",
    "name",
    "side",
    "status",
    "order_type",
    "qty",
    "price",
    "dealt_qty",
    "dealt_avg_price",
    "currency",
    "create_time",
    "updated_time",
    "remark",
]

# Do not persist these OpenD statuses to the stock-orders sheet.
_SKIP_ORDER_STATUSES = frozenset({"CANCELLED_ALL", "FAILED"})


def stock_orders_to_rows(orders: list[dict]) -> list[list]:
    """Map stock (non-option) order dicts to sheet rows."""
    rows: list[list] = []
    for order in orders:
        code = str(order.get("code") or "")
        if not code or is_option_code(code):
            continue
        if (order.get("status") or "").upper() in _SKIP_ORDER_STATUSES:
            continue
        rows.append([_cell(order.get(key)) for key in STOCK_ORDER_HEADERS])
    return rows


def sync_stock_orders(
    *,
    acc_id: int | None = None,
    start: str | None = None,
    end: str | None = None,
    trd_env: str | None = None,
) -> dict:
    """Fetch stock orders from OpenD and replace the 'stock orders' sheet tab."""
    settings = get_settings()
    if acc_id is None and settings.trd_acc_id:
        acc_id = settings.trd_acc_id
    env = (trd_env or settings.orders_trd_env).strip().upper()
    if env not in ("REAL", "SIMULATE"):
        env = settings.orders_trd_env

    snap = orders_adapter.fetch_trading_snapshot(
        acc_id=acc_id, start=start, end=end, trd_env=env
    )
    rows = stock_orders_to_rows(snap["orders"])
    write = sheets_adapter.replace_tab_values(
        headers=STOCK_ORDER_HEADERS,
        rows=rows,
        tab_title=settings.google_sheets_stock_orders_tab,
    )
    return {
        "ok": True,
        "trd_env": snap["trd_env"],
        "start": snap["start"],
        "end": snap["end"],
        "selected_acc_id": acc_id,
        "orders_fetched": len(snap["orders"]),
        "stock_orders_synced": write["rows_written"],
        "spreadsheet_id": write["spreadsheet_id"],
        "tab": write["tab"],
    }


def _cell(value) -> str | int | float:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return str(value)
