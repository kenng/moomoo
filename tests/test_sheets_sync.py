from __future__ import annotations

from unittest.mock import patch

import pytest

from momo.config import get_settings
from momo.services import sheets_sync


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_stock_orders_to_rows_filters_options_cancelled_and_failed():
    orders = [
        {
            "acc_id": 1,
            "order_id": "a",
            "code": "US.QCOM",
            "name": "Qualcomm",
            "side": "BUY",
            "status": "FILLED_ALL",
            "order_type": "NORMAL",
            "qty": 10,
            "price": 100.0,
            "dealt_qty": 10,
            "dealt_avg_price": 100.0,
            "currency": "USD",
            "create_time": "2026-01-01 10:00:00",
            "updated_time": "2026-01-01 10:01:00",
            "remark": "",
        },
        {
            "acc_id": 1,
            "order_id": "b",
            "code": "US.QCOM260911P140000",
            "name": "QCOM put",
            "side": "BUY",
            "status": "FILLED_ALL",
            "order_type": "NORMAL",
            "qty": 1,
            "price": 2.0,
            "dealt_qty": 1,
            "dealt_avg_price": 2.0,
            "currency": "USD",
            "create_time": "2026-01-02 10:00:00",
            "updated_time": "",
            "remark": "",
        },
        {
            "acc_id": 1,
            "order_id": "c",
            "code": "HK.01810",
            "name": "Xiaomi",
            "side": "SELL",
            "status": "CANCELLED_ALL",
            "order_type": "NORMAL",
            "qty": 100,
            "price": 20.0,
            "dealt_qty": 0,
            "dealt_avg_price": None,
            "currency": "HKD",
            "create_time": "2026-01-03 10:00:00",
            "updated_time": "",
            "remark": "x",
        },
        {
            "acc_id": 1,
            "order_id": "d",
            "code": "US.AAPL",
            "name": "Apple",
            "side": "BUY",
            "status": "FAILED",
            "order_type": "NORMAL",
            "qty": 1,
            "price": 150.0,
            "dealt_qty": 0,
            "dealt_avg_price": None,
            "currency": "USD",
            "create_time": "2026-01-04 10:00:00",
            "updated_time": "",
            "remark": "rejected",
        },
    ]
    rows = sheets_sync.stock_orders_to_rows(orders)
    assert len(rows) == 1
    assert rows[0][1] == "a"
    assert rows[0][2] == "US.QCOM"
    assert rows[0][10] == 100.0


def test_sync_stock_orders_writes_sheet(monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_SPREADSHEET_ID", "sheet-123")
    monkeypatch.setenv("GOOGLE_SHEETS_STOCK_ORDERS_TAB", "stock orders")
    get_settings.cache_clear()

    snap = {
        "trd_env": "REAL",
        "start": "2025-01-01",
        "end": "2026-01-01",
        "orders": [
            {
                "acc_id": 9,
                "order_id": "ord-1",
                "code": "US.AAPL",
                "name": "Apple",
                "side": "BUY",
                "status": "FILLED_ALL",
                "order_type": "NORMAL",
                "qty": 5,
                "price": 150,
                "dealt_qty": 5,
                "dealt_avg_price": 150,
                "currency": "USD",
                "create_time": "2026-01-01",
                "updated_time": "",
                "remark": "",
            }
        ],
    }

    with (
        patch(
            "momo.services.sheets_sync.orders_adapter.fetch_trading_snapshot",
            return_value=snap,
        ) as fetch,
        patch(
            "momo.services.sheets_sync.sheets_adapter.replace_tab_values",
            return_value={
                "spreadsheet_id": "sheet-123",
                "tab": "stock orders",
                "rows_written": 1,
            },
        ) as write,
    ):
        result = sheets_sync.sync_stock_orders(trd_env="REAL")

    fetch.assert_called_once()
    write.assert_called_once()
    kwargs = write.call_args.kwargs
    assert kwargs["headers"] == sheets_sync.STOCK_ORDER_HEADERS
    assert len(kwargs["rows"]) == 1
    assert kwargs["tab_title"] == "stock orders"
    assert result["ok"] is True
    assert result["stock_orders_synced"] == 1
    assert result["tab"] == "stock orders"
