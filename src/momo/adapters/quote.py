from __future__ import annotations

from momo.opend_client import OpenDError, quote_context


def get_snapshots(symbols: list[str]) -> list[dict]:
    if not symbols:
        return []

    import moomoo as ft

    with quote_context() as ctx:
        ret, data = ctx.get_market_snapshot(symbols)
        if ret != ft.RET_OK:
            raise OpenDError(f"get_market_snapshot failed: {data}")
        if data is None or getattr(data, "empty", True):
            return []

        rows = []
        for _, row in data.iterrows():
            code = str(row.get("code") or "")
            rows.append(
                {
                    "symbol": code,
                    "stock_code": code.removeprefix("MY."),
                    "last_price": _float(row.get("last_price")),
                    "change_rate": _float(row.get("change_rate")),
                    "volume": _float(row.get("volume")),
                    "turnover": _float(row.get("turnover")),
                }
            )
        return rows


def _float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
