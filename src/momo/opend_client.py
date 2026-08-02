"""OpenD connection lifecycle for quote (and later trade) contexts."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from momo.config import get_settings


class OpenDError(RuntimeError):
    pass


def _import_moomoo():
    try:
        import moomoo as ft

        return ft
    except ImportError as exc:
        raise OpenDError(
            "moomoo-api is not installed. Run: pip install -r requirements.txt"
        ) from exc


@contextmanager
def quote_context() -> Iterator:
    ft = _import_moomoo()
    settings = get_settings()
    ctx = ft.OpenQuoteContext(host=settings.opend_host, port=settings.opend_port)
    try:
        yield ctx
    finally:
        ctx.close()


def smoke_test_snapshot(symbol: str = "HK.01810") -> dict:
    """Fetch one market snapshot to verify OpenD connectivity."""
    ft = _import_moomoo()
    with quote_context() as ctx:
        ret, data = ctx.get_market_snapshot([symbol])
        if ret != ft.RET_OK:
            raise OpenDError(f"get_market_snapshot failed: {data}")
        if data is None or getattr(data, "empty", True):
            raise OpenDError(f"No snapshot data for {symbol}")
        row = data.iloc[0].to_dict()
        return {
            "symbol": symbol,
            "last_price": row.get("last_price"),
            "change_rate": row.get("change_rate"),
            "volume": row.get("volume"),
        }
